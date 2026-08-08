from dataclasses import dataclass
from typing import Dict, Generator, List, Literal, Optional, Any
from ..utils.logging import get_logger

logger = get_logger(__name__)
import re
import mathutils
import numpy as np
import uuid
from collections import Counter
import math

import bpy
from bpy.app.handlers import persistent
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
    FloatVectorProperty,
)
from bpy.types import (
    Context,
    Image,
    Node,
    NodeSocket,
    NodeTree,
    Object,
    PropertyGroup,
    Material,
)
from bpy.utils import register_classes_factory
from bpy_extras.node_utils import connect_sockets
from typing import Optional
from mathutils import Color, Euler, Vector

from .image import blender_image_to_numpy, set_image_pixels, save_image, ImageTiles

from .list_manager import ListManager

# ---
from ..utils.version import is_newer_than
from ..utils.nodes import find_node, find_socket_on_node, get_material_output, get_node_socket_enum, get_nodetree_socket_enum, transfer_connection
from ..preferences import get_preferences
from ..utils import get_next_unique_name
from .context import get_legacy_global_layer, parse_context
from .enums import *
from .graph import (
    NodeTreeBuilder,
    create_layer_graph,
    get_layer_blend_type,
    set_layer_blend_type,
    build_channel_graph,
    vector_transform,
)
from .graph.common import DEFAULT_PS_UV_MAP_NAME
from .layer_types import get_layer_type, layer_modifies_color
from .legacy import (
    LegacyPaintSystemLayer,
    LegacyPaintSystemGroup,
    LegacyPaintSystemGroups,
    LegacyPaintSystemContextParser,
)
from .nested_list_manager import BaseNestedListManager, BaseNestedListItem

# Layer/Channel PropertyGroup은 layer.py / channel.py로 분리되었다.
# 순환 임포트 방지를 위해 반드시 layer -> channel 순서로 임포트한다
# (Channel은 클래스 정의 시점에 CollectionProperty(type=Layer)가 필요).
from .layer import (
    MarkerAction,
    GlobalLayer,
    LayerMask,
    Layer,
    add_empty_to_collection,
    update_brush_settings,
    update_active_image,
    update_active_layer,
    update_active_channel,
    update_active_group,
)
from .channel import Channel, ps_bake, save_cycles_settings, restore_cycles_settings

def is_valid_uuidv4(uuid_string):
    """
    Checks if a given string is a valid UUIDv4.

    Args:
        uuid_string (str): The string to validate.

    Returns:
        bool: True if the string is a valid UUIDv4, False otherwise.
    """
    try:
        uuid_obj = uuid.UUID(uuid_string, version=4)
        # Ensure the string representation of the object matches the original string
        # to catch cases where a valid UUID string might have been padded or altered
        return str(uuid_obj) == uuid_string
    except ValueError:
        return False


def find_channels_containing_layer(check_layer: "Layer") -> list["Channel"]:
    """Find all channels that reference *check_layer* (directly or via link)."""
    # 링크 레이어는 다른 머티리얼에서 uid로 참조하므로 스캔 범위를 소유 머티리얼로 줄일 수 없다
    check_uid = check_layer.uid
    channels = []
    seen = set()
    if not check_uid:
        # uid가 비어 있으면 인덱스 키로 묶을 수 없어 직접 참조만 본다
        for _mat, _grp, channel, layer in iter_all_layers():
            if layer != check_layer:
                continue
            ch_id = channel.as_pointer()
            if ch_id in seen:
                continue
            seen.add(ch_id)
            channels.append(channel)
        return channels

    for channel in _get_layer_uid_channel_index().get(check_uid, ()):
        try:
            ch_id = channel.as_pointer()
        except ReferenceError:
            continue
        if ch_id in seen:
            continue
        seen.add(ch_id)
        channels.append(channel)
    return channels


# uid → 해당 uid를 소유하거나 링크하는 채널 목록
_layer_uid_channel_index: Optional[dict[str, list["Channel"]]] = None
_layer_uid_channel_index_uses: int = 0
_LAYER_UID_CHANNEL_INDEX_MAX_USES = 60


def invalidate_layer_uid_channel_index():
    """레이어 추가/삭제/링크·언두 시 uid→채널 인덱스를 버린다."""
    global _layer_uid_channel_index, _layer_uid_channel_index_uses
    _layer_uid_channel_index = None
    _layer_uid_channel_index_uses = 0


def _get_layer_uid_channel_index() -> dict[str, list["Channel"]]:
    """레이어 uid / linked_layer_uid → 채널 목록 인덱스를 돌려준다."""
    global _layer_uid_channel_index, _layer_uid_channel_index_uses
    if (_layer_uid_channel_index is not None
            and _layer_uid_channel_index_uses < _LAYER_UID_CHANNEL_INDEX_MAX_USES):
        _layer_uid_channel_index_uses += 1
        return _layer_uid_channel_index

    index: dict[str, list] = {}
    seen: dict[str, set] = {}
    for _mat, _grp, channel, layer in iter_all_layers():
        try:
            ch_id = channel.as_pointer()
        except ReferenceError:
            continue
        keys = []
        if layer.uid:
            keys.append(layer.uid)
        if layer.linked_layer_uid:
            keys.append(layer.linked_layer_uid)
        for key in keys:
            bucket_seen = seen.setdefault(key, set())
            if ch_id in bucket_seen:
                continue
            bucket_seen.add(ch_id)
            index.setdefault(key, []).append(channel)

    _layer_uid_channel_index = index
    _layer_uid_channel_index_uses = 0
    return index


def get_node_from_nodetree(node_tree: NodeTree, identifier: str) -> Node | None:
    """Find a node by its label in a node tree."""
    if not node_tree or not node_tree.nodes:
        return None
    return find_node(node_tree, {'label': identifier}, connected_to_output=False)

def is_valid_ps_nodetree(node_tree: NodeTree) -> bool:
        # check if the node tree has both Color and Alpha inputs and outputs
        has_color_input = False
        has_alpha_input = False
        has_color_output = False
        has_alpha_output = False
        for interface_item in node_tree.interface.items_tree:
            if interface_item.item_type == "SOCKET":
                # logger.debug(interface_item.name, interface_item.socket_type, interface_item.in_out)
                if interface_item.name == "Color" and interface_item.socket_type == "NodeSocketColor":
                    if interface_item.in_out == "INPUT":
                        has_color_input = True
                    else:
                        has_color_output = True
                elif interface_item.name == "Alpha" and interface_item.socket_type == "NodeSocketFloat":
                    if interface_item.in_out == "INPUT":
                        has_alpha_input = True
                    else:
                        has_alpha_output = True
        return has_color_input and has_alpha_input and has_color_output and has_alpha_output


def get_paint_system_collection(context: bpy.types.Context) -> bpy.types.Collection:
    view_layer = context.view_layer
    if "Paint System Collection" not in view_layer.layer_collection.collection.children:
        collection = bpy.data.collections.new("Paint System Collection")
        view_layer.layer_collection.collection.children.link(collection)
    else:
        collection = view_layer.layer_collection.collection.children["Paint System Collection"]
    return collection

def blender_color_to_srgb_hex(color: Color):
    """
    Converts a Blender Color property (Linear R, G, B floats 0.0-1.0) 
    to the corresponding sRGB color, and then to an 8-character hex string (#RRGGBB).
    """
    
    # 3. Convert the sRGB floats to 0-255 integers
    r = int(color.r * 255)
    g = int(color.g * 255)
    b = int(color.b * 255)
    
    # 4. Format and return
    return "#{:02x}{:02x}{:02x}".format(r, g, b).upper()


HEX_PATTERN = re.compile(r'^[0-9a-fA-F]{6}$')

def _is_valid_hex_code(hex_str_6char):
    """
    Checks if a cleaned 6-character string is a valid hex code using regex.
    """
    return HEX_PATTERN.match(hex_str_6char) is not None


def hex_string_to_blender_color(hex_string):
    """
    Converts a hex string (e.g., #A3F5B4 or A3F5B4) into a Blender 
    (R, G, B) float tuple (linear color space).

    If the string is invalid, returns White (1.0, 1.0, 1.0).

    Args:
        hex_string (str): The input string to check.

    Returns:
        tuple: (R, G, B) float values in linear color space.
    """
    
    # Define the default return color (White)
    WHITE_COLOR = (1.0, 1.0, 1.0)
    
    # 1. Cleanup the input string (remove optional hash prefix)
    cleaned_hex = hex_string.lstrip('#')
    
    # 2. Validation Check
    if not _is_valid_hex_code(cleaned_hex):
        logger.warning(f"Invalid hex code received: {hex_string}. Returning white.")
        return WHITE_COLOR
        
    # --- If Valid, proceed to conversion ---
    
    try:
        # 3. Parse components (convert RR, GG, BB from base 16 to base 10)
        r = int(cleaned_hex[0:2], 16)
        g = int(cleaned_hex[2:4], 16)
        b = int(cleaned_hex[4:6], 16)

        # 4. Normalize to 0.0 - 1.0 float values (treating input as sRGB)
        r_norm = r / 255.0
        g_norm = g / 255.0
        b_norm = b / 255.0

        # 5. Convert from sRGB (the standard hex space) to Linear (Blender space)
        # We must use mathutils.Color for accurate color space conversion
        linear_color = Color((r_norm, g_norm, b_norm))
        
        # 6. Return as a standard tuple
        return (linear_color.r, linear_color.g, linear_color.b)

    except ValueError:
        # Failsafe for any unexpected parsing error during int() conversion
        return WHITE_COLOR
        
# Ensure node sockets are in the correct order
def detect_change(old, new):
    if len(new) > len(old):  # ADD
        for i in range(len(new)):
            if i >= len(old) or old[i] != new[i]:
                return "ADD", i

    elif len(new) < len(old):  # REMOVE
        for i in range(len(old)):
            if i >= len(new) or old[i] != new[i]:
                return "REMOVE", i

    else:  # Same length: MOVE or RENAME
        # Check if it's a MOVE
        for i in range(len(old)):
            if old[i] != new[i]:
                # MOVE: element exists in both lists but index changed
                if old[i] in new and new[i] in old:
                    return "MOVE", i
                else:
                    return "RENAME", i

    return None, None  # No change

@dataclass
class ExpectedSocket:
    name: str
    socket_type: str
    use_max_min: bool = False
    min_value: float = 0
    max_value: float = 1
    hide_value: bool = False
        
def ensure_sockets(node_tree: NodeTree, expected_sockets: List[ExpectedSocket], in_out = "OUTPUT"):
    nt_interface = node_tree.interface
    nt_sockets = nt_interface.items_tree
    if in_out == "INPUT":
        offset_idx = len(expected_sockets)
    else:
        offset_idx = 0
    while True:
        output_sockets = [socket for socket in nt_sockets if socket.item_type == "SOCKET" and socket.in_out == in_out]
        output_sockets_names = [socket.name for socket in output_sockets]
        change, idx = detect_change(output_sockets_names, [socket.name for socket in expected_sockets])
        if change is None:
            break
        match change:
            case "ADD":
                socket_name, socket_type, use_max_min = expected_sockets[idx].name, expected_sockets[idx].socket_type, expected_sockets[idx].use_max_min
                socket = nt_interface.new_socket(name=socket_name, socket_type=socket_type, in_out=in_out)
                if hasattr(socket, "subtype") and use_max_min:
                    socket.subtype = "FACTOR"
                    socket.min_value = expected_sockets[idx].min_value
                    socket.max_value = expected_sockets[idx].max_value
                nt_interface.move(socket, idx + offset_idx)
            case "REMOVE":
                socket = output_sockets[idx]
                nt_interface.remove(socket)
            case "MOVE":
                socket = output_sockets[idx]
                expected_socket_idx = [socket.name for socket in expected_sockets].index(socket.name)
                nt_interface.move(socket, expected_socket_idx + offset_idx + 1)
            case "RENAME":
                socket = output_sockets[idx]
                socket.name = expected_sockets[idx].name
    
    # ensure socket type
    output_sockets = [socket for socket in nt_sockets if socket.item_type == "SOCKET" and socket.in_out == in_out]
    for idx, socket in enumerate(output_sockets):
        
        if hasattr(socket, "subtype"):
            expected_subtype = "FACTOR" if expected_sockets[idx].use_max_min else "NONE"
            socket.subtype = expected_subtype
            if expected_sockets[idx].use_max_min:
                socket.min_value = expected_sockets[idx].min_value
                socket.max_value = expected_sockets[idx].max_value
            else:
                socket.min_value = -1e39
                socket.max_value = 1e39
        
        if hasattr(socket, "hide_value"):
            socket.hide_value = expected_sockets[idx].hide_value
        
        if socket.socket_type != expected_sockets[idx].socket_type:
            socket.socket_type = expected_sockets[idx].socket_type

def get_udim_tiles(object: bpy.types.Object, uv_layer_name: str):
    """Return the set of UDIM tile numbers that *object*'s UV data touches."""
    uv_layer = object.data.uv_layers.get(uv_layer_name)
    if not uv_layer:
        return {1001}
    n = len(uv_layer.uv)
    if n == 0:
        return {1001}
    uv_data = np.empty(n * 2, dtype=np.float32)
    uv_layer.uv.foreach_get("vector", uv_data)
    uv_data = uv_data.reshape((n, 2))
    rows = np.maximum(1, np.ceil(uv_data[:, 1]).astype(int)) - 1
    cols = np.maximum(1, np.ceil(uv_data[:, 0]).astype(int))
    tile_numbers = 1000 + rows * 10 + cols
    return set(tile_numbers.tolist())

def ensure_udim_tiles(image: bpy.types.Image, objects: list[bpy.types.Object], uv_layer_name: str):
    # Check position the data in uv_layer, create a list of number for UDIM tiles
    udim_tiles = set()
    for object in objects:
        udim_tiles.update(get_udim_tiles(object, uv_layer_name))
    width, height = image.size
    
    # Clean up tiles that does not have image
    for tile in image.tiles:
        if tile.channels == 0:
            image.tiles.remove(tile)

    for tile_number in udim_tiles:
        if any(tile_number == tile.number for tile in image.tiles):
            continue
        with bpy.context.temp_override(edit_image=image):
            bpy.ops.image.tile_add(number=tile_number, color=(0, 0, 0, 0), width=width, height=height)
    # Delete unused tiles
    for tile in image.tiles:
        if tile.number not in udim_tiles:
            logger.debug(f"Removing tile {tile.number}")
            image.tiles.remove(tile)
    save_image(image)

def create_ps_image(name: str, width: int = 2048, height: int = 2048, use_udim_tiles: bool = False, objects: list[bpy.types.Object] = None, uv_layer_name: str = None, use_float: bool = False):
    img = bpy.data.images.new(
        name=name, width=width, height=height, alpha=True, float_buffer=use_float)
    img.generated_color = (0, 0, 0, 0)
    save_image(img)
    if use_udim_tiles:
        img.source = "TILED"
        if objects and uv_layer_name:
            ensure_udim_tiles(img, objects, uv_layer_name)
        else:
            raise ValueError("Objects and UV layer name are required for UDIM tiles")
    return img

def ensure_paint_system_uv_map(context: bpy.types.Context):
    selection = context.selected_objects

    # Get the active object
    ps_object = parse_context(context).ps_object
    
    if not ps_object:
        return
    
    if ps_object.data.uv_layers.get(DEFAULT_PS_UV_MAP_NAME):
        return

    # Deselect all objects
    for obj in selection:
        if obj != ps_object:
            obj.select_set(False)
    # Make it active
    context.view_layer.objects.active = ps_object
    original_mode = str(ps_object.mode)
    
    # Apply to only the active object
    uv_layers = ps_object.data.uv_layers
    uvmap = uv_layers.new(name=DEFAULT_PS_UV_MAP_NAME)
    ps_object.data.uv_layers.active = uvmap
    
    bpy.ops.object.mode_set(mode='EDIT')
    ps_object.update_from_editmode()
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(angle_limit=30/180*math.pi, island_margin=0.005)
    bpy.ops.object.mode_set(mode=original_mode)
    # Deselect the object
    ps_object.select_set(False)
    # Restore the selection
    for obj in selection:
        obj.select_set(True)
    context.view_layer.objects.active = ps_object

def get_layer_by_uid(material: Material, uid: str) -> Layer | None:
    uid_to_layer = _get_material_layer_uid_map(material)
    layer = uid_to_layer.get(uid)
    if not layer:
        layer = _get_material_layer_uid_map(material, force_refresh=True).get(uid)
    return layer

# Module-level cache for material layer UID maps
_material_uid_cache: Dict[Material, Dict[str, 'Layer']] = {}

def _get_material_layer_uid_map(material: Material, force_refresh: bool = False) -> Dict[str, 'Layer']:
    """Get a UID to Layer mapping for a material. Uses caching for performance."""
    if not material or not material.ps_mat_data:
        return {}
    
    # Check if cache is valid (simple version check using material name as key)
    cache_key = material
    if cache_key in _material_uid_cache and not force_refresh:
        return _material_uid_cache[cache_key]
    
    # Build the UID map
    uid_map = {}
    for group in material.ps_mat_data.groups:
        for channel in group.channels:
            for layer in channel.layers:
                if layer.uid:
                    uid_map[layer.uid] = layer
    
    # Cache it
    _material_uid_cache[cache_key] = uid_map
    return uid_map

def _invalidate_material_layer_cache(material: Material = None):
    """Invalidate the layer UID cache for a material or all materials."""
    global _material_uid_cache
    if material:
        _material_uid_cache.pop(material, None)
    else:
        _material_uid_cache.clear()


class Group(PropertyGroup):
    """A Paint System group that bundles multiple channels into one node-tree.
    
    Each group corresponds to a ShaderNodeGroup in the material node tree
    and exposes its channels as input/output sockets.
    """
    
    def get_group_node(self, node_tree: NodeTree) -> bpy.types.Node:
        group_node = find_node(node_tree, {'bl_idname': 'ShaderNodeGroup', 'node_tree': self.node_tree})
        if not group_node:
            group_node = find_node(node_tree, {'bl_idname': 'ShaderNodeGroup', 'node_tree': self.node_tree}, connected_to_output=False)
        return group_node
    
    def update_node_tree(self, context):
        if not self.node_tree:
            return
        node_tree = self.node_tree
        # Group은 Material.ps_mat_data 아래 중첩 PropertyGroup이므로 id_data가 소유 머티리얼이다
        owner = self.id_data
        mat = owner if isinstance(owner, Material) else None
        if mat is None:
            # 소유자를 못 얻는 예외적 상황에서만 전체 머티리얼을 뒤진다
            for material in bpy.data.materials:
                if material.ps_mat_data and material.ps_mat_data.groups:
                    if any(group.node_tree == node_tree for group in material.ps_mat_data.groups):
                        mat = material
                        break
        if mat:
            node_tree.name = f"PS {self.name} ({mat.name})"
        else:
            node_tree.name = f"PS {self.name} (None)"
        # node_tree.name = f"Paint System ({self.name})"
        if not isinstance(node_tree, bpy.types.NodeTree):
            return
        
        expected_sockets: List[ExpectedSocket] = []
        for channel in self.channels:
            # Map channel type to valid socket type
            type_map = {"COLOR": "NodeSocketColor", "VECTOR": "NodeSocketVector", "FLOAT": "NodeSocketFloat"}
            socket_type = type_map.get(channel.type, "NodeSocketColor")
            expected_sockets.append(ExpectedSocket(channel.name, socket_type, channel.use_max_min, channel.factor_min, channel.factor_max, hide_value=channel.default_value != "NONE"))
            if channel.use_alpha:
                expected_sockets.append(ExpectedSocket(f"{channel.name} Alpha", "NodeSocketFloat", True, 0, 1))
        
        ensure_sockets(node_tree, expected_sockets, "OUTPUT")
        ensure_sockets(node_tree, expected_sockets, "INPUT")
        
        node_builder = NodeTreeBuilder(self.node_tree, frame_name="Group Graph", clear=True)
        node_builder.add_node("group_input", "NodeGroupInput")
        node_builder.add_node("group_output", "NodeGroupOutput")
        for channel in self.channels:
            if not channel.node_tree or len(channel.node_tree.interface.items_tree) == 0:
                # Channel is not valid, skip it
                continue
            channel_name = channel.name
            c_alpha_name = f"{channel.name} Alpha"
            node_builder.add_node(channel_name, "ShaderNodeGroup", {"node_tree": channel.node_tree}, {"Alpha": 1})
            node_builder.link("group_input", channel_name, channel_name, "Color")
                
            if channel.use_alpha:
                node_builder.link("group_input", channel_name, c_alpha_name, "Alpha")
            node_builder.link(channel_name, "group_output", "Color", channel_name)
            if channel.use_alpha:
                node_builder.link(channel_name, "group_output", "Alpha", c_alpha_name)
        node_builder.compile()
    
    name: StringProperty(
        name="Name",
        description="Group name",
        default="New Group",
        update=update_node_tree
    )
    channels: CollectionProperty(
        type=Channel,
        name="Channels",
        description="Collection of channels in the Paint System"
    )
    template: EnumProperty(
        name="Template",
        items=TEMPLATE_ENUM,
        default='NONE'
    )
    coord_type: EnumProperty(
        items=COORDINATE_TYPE_ENUM,
        name="Coordinate Type",
        description="Coordinate type",
        default='UV'
    )
    uv_map_name: StringProperty(
        name="UV Map",
        description="UV map"
    )
    
    def update_channel(self, context):
        ps_ctx = parse_context(context)
        ps_mat_data = ps_ctx.ps_mat_data
        if ps_mat_data.preview_channel:
            # Call paint_system.isolate_active_channel twice to ensure it's updated
            ps_ctx.active_channel.isolate_channel(context)
            ps_ctx.active_channel.isolate_channel(context)
        if ps_ctx.active_channel and ps_ctx.active_channel.use_bake_image:
            # Force to object mode
            bpy.ops.object.mode_set(mode="OBJECT")
        update_active_image(self, context)
    
    active_index: IntProperty(name="Active Channel Index", update=update_channel)
    node_tree: PointerProperty(
        name="Node Tree",
        type=NodeTree
    )
    
    def create_channel(
        self, 
        context, 
        channel_name: str = "New Channel",
        channel_type: str = "COLOR",
        disable_output_transform: bool = False, # Newly created channels are disabled by default
        **kwargs
    ):
        channels = self.channels
        node_tree = bpy.data.node_groups.new(name=f"Temp Channel Name", type='ShaderNodeTree')
        new_channel = channels.add()
        self.active_index = len(channels) - 1
        unique_name = get_next_unique_name(channel_name, [channel.name for channel in channels])
        new_channel.name = unique_name
        new_channel.type = channel_type
        new_channel.disable_output_transform = disable_output_transform
        for key, value in kwargs.items():
            setattr(new_channel, key, value)
        new_channel.node_tree = node_tree
        new_channel.update_node_tree(context)
        self.update_node_tree(context)
        return new_channel
    
    def create_channel_template(
        self,
        context: Context,
        template: str,
        add_layers: bool = True
    ):
        ps_ctx = parse_context(context)
        mat = ps_ctx.active_material
        mat_node_tree = mat.node_tree
        node_group = ps_ctx.active_group.get_group_node(mat_node_tree)
        to_node = find_node(mat_node_tree, {'bl_idname': 'ShaderNodeBsdfPrincipled'})
        if not to_node:
            to_node = find_node(mat_node_tree, {'bl_idname': 'ShaderNodeBsdfDiffuse'})
        match template:
            case "COLOR":
                channel = self.create_channel(context, channel_name='Color', channel_type='COLOR', use_alpha=True)
                if node_group and to_node:
                    color_socket = find_socket_on_node(to_node, 'Base Color')
                    # Color
                    if not color_socket:
                        color_socket = find_socket_on_node(to_node, 'Color')
                    if color_socket:
                        transfer_connection(mat_node_tree, color_socket, node_group.inputs['Color'])
                        connect_sockets(node_group.outputs['Color'], color_socket)
                    # Alpha
                    alpha_socket = find_socket_on_node(to_node, 'Alpha')
                    if alpha_socket:
                        transfer_connection(mat_node_tree, alpha_socket, node_group.inputs['Color Alpha'])
                        connect_sockets(node_group.outputs['Color Alpha'], alpha_socket)
                    else:
                        # Disable alpha
                        channel.use_alpha = False
                if add_layers:
                    channel.create_layer(context, layer_name='Image', layer_type='IMAGE', coord_type=self.coord_type, uv_map_name=self.uv_map_name)
                return channel
            case "METALLIC":
                channel = self.create_channel(context, channel_name='Metallic', channel_type='FLOAT', use_alpha=False, use_max_min=True, color_space='NONCOLOR')
                if node_group and to_node:
                    metallic_socket = find_socket_on_node(to_node, 'Metallic')
                    if metallic_socket:
                        transfer_connection(mat_node_tree, metallic_socket, node_group.inputs['Metallic'])
                        connect_sockets(node_group.outputs['Metallic'], metallic_socket)
                return channel
            case "ROUGHNESS":
                channel = self.create_channel(context, channel_name='Roughness', channel_type='FLOAT', use_alpha=False, use_max_min=True, color_space='NONCOLOR')
                if node_group and to_node:
                    roughness_socket = find_socket_on_node(to_node, 'Roughness')
                    if roughness_socket:
                        transfer_connection(mat_node_tree, roughness_socket, node_group.inputs['Roughness'])
                        connect_sockets(node_group.outputs['Roughness'], roughness_socket)
                return channel
            case "NORMAL":
                socket_transferred = False
                channel = self.create_channel(context, channel_name='Normal', channel_type='VECTOR', use_alpha=False, normalize_input=True, color_space='NONCOLOR', default_value='NORMAL', use_space_transform_input=True, use_space_transform_output=True)
                if node_group and to_node:
                    normal_socket = find_socket_on_node(to_node, 'Normal')
                    if normal_socket:
                        socket_transferred = transfer_connection(mat_node_tree, to_node.inputs['Normal'], node_group.inputs['Normal'])
                        connect_sockets(node_group.outputs['Normal'], normal_socket)
                if add_layers:
                    if not socket_transferred:
                        channel.create_layer(context, layer_name='Normal', layer_type='GEOMETRY', geometry_type='OBJECT_NORMAL', normalize_normal=True)
                    channel.create_layer(context, layer_name='Image', layer_type='IMAGE', coord_type=self.coord_type, uv_map_name=self.uv_map_name)
            case _:
                raise ValueError(f"Invalid template: {template}")
    
    def delete_channel(self, context, channel: "Channel"):
        active_index = self.channels.find(channel.name)
        if active_index < 0 or active_index >= len(self.channels):
            logger.warning(f"No valid channel selected for deletion")
            return
        
        self.channels.remove(active_index)
        self.active_index = max(0, active_index - 1)
        self.update_node_tree(context)


class ClipboardLayer(PropertyGroup):
    """Clipboard layer"""
    uid: StringProperty(
        name="UID",
        description="UID of the layer",
        default=""
    )
    material: PointerProperty(
        name="Material",
        type=Material
    )


class TempMaterial(PropertyGroup):
    material: PointerProperty(
        name="Material",
        type=Material
    )
    enabled: BoolProperty(
        name="Enabled",
        description="Enabled",
        default=False
    )

class PaintSystemGlobalData(PropertyGroup):
    """Scene-level global state for the Paint System (stored on ``Scene.ps_scene_data``).
    
    Holds the clipboard, colour history palette, HSV brush state, and legacy layer data.
    """
    
    def get_brush_color(self, context):
        settings = context.tool_settings.image_paint
        brush = settings.brush
        if hasattr(context.tool_settings, "unified_paint_settings"):
            ups = context.tool_settings.unified_paint_settings
        else:
            ups = settings.unified_paint_settings
        prop_owner = ups if ups.use_unified_color else brush
        return prop_owner.color
    
    def update_unified_color(self, context):
        brush_color = self.get_brush_color(context)
        if brush_color.hsv != (self.hue, self.saturation, self.value):
            brush_color.hsv = (self.hue, self.saturation, self.value)
    
    def update_hex_color(self, context):
        brush_color = self.get_brush_color(context)
        brush_color_hex = blender_color_to_srgb_hex(brush_color)
        if brush_color_hex != self.hex_color:
            color = hex_string_to_blender_color(self.hex_color)
            brush_color.r = color[0]
            brush_color.g = color[1]
            brush_color.b = color[2]
    
    def update_hsv_color(self, context):
        if context.mode != 'PAINT_TEXTURE':
            return
        settings = context.tool_settings.image_paint
        brush = settings.brush
        if hasattr(context.tool_settings, "unified_paint_settings"):
            ups = context.tool_settings.unified_paint_settings
        else:
            ups = settings.unified_paint_settings
        ubs = ups if ups.use_unified_color else brush
        # Store color to context.ps_scene_data.hsv_color
        hsv = ubs.color.hsv
        if hsv != (context.scene.ps_scene_data.hue, context.scene.ps_scene_data.saturation, context.scene.ps_scene_data.value):
            context.scene.ps_scene_data.hue = hsv[0]
            context.scene.ps_scene_data.saturation = hsv[1]
            context.scene.ps_scene_data.value = hsv[2]
            color = ubs.color
            r = int(color[0] * 255)
            g = int(color[1] * 255)
            b = int(color[2] * 255)
            hex_color = "#{:02x}{:02x}{:02x}".format(r, g, b).upper()
            context.scene.ps_scene_data.hex_color = hex_color
    
    clipboard_layers: CollectionProperty(
        type=ClipboardLayer,
        name="Clipboard Layers",
        description="Collection of layers in the clipboard",
        options={'SKIP_SAVE'}
    )
    active_clipboard_index: IntProperty(name="Active Clipboard Layer Index")
    layers: CollectionProperty(
        type=GlobalLayer,
        name="Paint System Layers",
        description="Collection of layers in the Paint System"
    )
    active_index: IntProperty(name="Active Layer Index")
    last_selected_ps_object: PointerProperty(
        name="Last Selected Object",
        type=Object
    )
    last_selected_object: PointerProperty(
        name="Last Selected Object",
        type=Object
    )
    last_selected_material: PointerProperty(
        name="Last Selected Material",
        type=Material
    )
    hue: FloatProperty(
        name="Hue",
        description="Hue of the brush",
        default=0.0,
        update=update_unified_color,
        min=0.0,
        max=1.0,
        subtype='FACTOR'
    )
    saturation: FloatProperty(
        name="Saturation",
        description="Saturation of the brush",
        default=0.0,
        update=update_unified_color,
        min=0.0,
        max=1.0,
        subtype='FACTOR'
    )
    value: FloatProperty(
        name="Value",
        description="Value of the brush",
        default=0.0,
        update=update_unified_color,
        min=0.0,
        max=1.0,
        subtype='FACTOR'
    )
    hex_color: StringProperty(
        name="Hex Color",
        description="Hex color of the brush",
        default="#000000",
        update=update_hex_color,
    )
    color_history_palette: PointerProperty(
        name="Color History Palette",
        type=bpy.types.Palette,
        description="Palette to store color history"
    )
    temp_materials: CollectionProperty(
        type=TempMaterial,
        name="Temp Materials",
        description="Collection of materials in the temporary collection",
        options={'SKIP_SAVE'}
    )
    
    def add_layer_to_clipboard(self, layer: "Layer"):
        ps_ctx = parse_context(bpy.context)
        clipboard_layer = self.clipboard_layers.add()
        if layer.is_linked:
            clipboard_layer.uid = layer.linked_layer_uid
            clipboard_layer.material = layer.linked_material
        else:
            clipboard_layer.uid = layer.uid
            clipboard_layer.material = ps_ctx.active_material
    
    def clear_clipboard(self):
        self.clipboard_layers.clear()
        self.active_clipboard_index = 0

class MaterialData(PropertyGroup):
    """Per-material Paint System data (stored on ``Material.ps_mat_data``).
    
    Contains groups, preview state, and helper methods for creating groups.
    """
    groups: CollectionProperty(
        type=Group,
        name="Groups",
        description="Collection of groups in the Paint System"
    )
    active_index: IntProperty(name="Active Group Index")
    use_alpha: BoolProperty(
        name="Use Alpha",
        description="Use alpha channel in the Paint System",
        default=True
    )
    preview_channel: BoolProperty(
        name="Preview Channel",
        description="Preview the channel",
        default=False
    )
    original_node_name: StringProperty(
        name="Original Node Name",
        description="Original node name of the channel"
    )
    original_socket_name: StringProperty(
        name="Original Socket Name",
        description="Original socket name of the channel"
    )
    original_view_transform: StringProperty(
        name="Original View Transform",
        description="Original view transform of the channel"
    )
    
    def create_new_group(self, context, group_name: str, node_tree: bpy.types.NodeTree = None):
        if not node_tree:
            node_tree = bpy.data.node_groups.new(name=f"Temp Group Name", type='ShaderNodeTree')
        else:
            # Delete all nodes in the node tree
            for node in node_tree.nodes:
                node_tree.nodes.remove(node)
        lm = ListManager(self, 'groups', self, 'active_index')
        new_group = lm.add_item()
        new_group.name = group_name
        new_group.node_tree = node_tree
        new_group.update_node_tree(context)
        return new_group


class CameraPlaneData(PropertyGroup):
    position: FloatVectorProperty(
        name="Position",
        description="Position of the camera plane",
        default=(0, 0, 0)
    )
    rotation: FloatVectorProperty(
        name="Rotation",
        description="Rotation of the camera plane",
        default=(0, 0, 0)
    )
    ref_layer_id: StringProperty()


class Filter(PropertyGroup):
    name: StringProperty()
    type: EnumProperty(
        items=FILTER_TYPE_ENUM,
        name="Filter Type",
        description="Filter type"
    )
    radius: FloatProperty(
        name="Radius",
        description="Radius of the filter",
        default=1.0
    )
    iterations: IntProperty(
        name="Iterations",
        description="Iterations of the filter",
        default=1
    )

def iter_all_layers() -> Generator[tuple[Material, Group, Channel, Layer], None, None]:
    """Yield (material, group, channel, layer) for every layer across all materials.
    
    This is the canonical way to iterate over all Paint System layers and avoids
    duplicating the four-level nested loop throughout the codebase.
    """
    for material in bpy.data.materials:
        if hasattr(material, 'ps_mat_data'):
            for group in material.ps_mat_data.groups:
                for channel in group.channels:
                    for layer in channel.layers:
                        yield material, group, channel, layer


def get_all_layers() -> list[Layer]:
    """Return a flat list of every layer across all materials."""
    return [layer for _mat, _grp, _ch, layer in iter_all_layers()]


# 액션이 달린 레이어 캐시 — frame_change_pre가 매 프레임 파일 전체를 순회하는 것을 막는다
_action_layers_cache: Optional[list[tuple['Layer', str]]] = None
_action_layers_cache_uses: int = 0
# 레이어 복제처럼 무효화가 걸리지 않는 경로가 있어 사용 횟수 상한을 둔다
_ACTION_LAYERS_CACHE_MAX_USES = 60


def invalidate_action_layer_cache():
    """액션 목록·레이어 구성이 바뀌었을 때 캐시를 버린다."""
    global _action_layers_cache, _action_layers_cache_uses
    _action_layers_cache = None
    _action_layers_cache_uses = 0


def get_action_layers() -> list[Layer]:
    """액션이 하나 이상 등록된 레이어만 반환한다.

    캐시에 담긴 RNA 포인터는 삭제·언두로 죽거나 다른 레이어를 가리킬 수 있으므로,
    uid 재확인에 실패하면 전체를 다시 스캔한다.
    """
    global _action_layers_cache, _action_layers_cache_uses
    if _action_layers_cache is not None and _action_layers_cache_uses < _ACTION_LAYERS_CACHE_MAX_USES:
        try:
            if all(layer.uid == uid and len(layer.actions) > 0
                   for layer, uid in _action_layers_cache):
                _action_layers_cache_uses += 1
                return [layer for layer, _uid in _action_layers_cache]
        except ReferenceError:
            pass

    _action_layers_cache = [(layer, layer.uid) for _mat, _grp, _ch, layer
                            in iter_all_layers() if len(layer.actions) > 0]
    _action_layers_cache_uses = 0
    return [layer for layer, _uid in _action_layers_cache]

def build_layer_link_counter() -> Counter:
    """파일 전체 레이어의 uid 참조 횟수를 센다.

    UIList처럼 행마다 is_layer_linked를 부르는 곳에서 이 카운터를 1회만 만들어
    넘겨주면 전체 머티리얼 순회가 반복되지 않는다.
    """
    counter = Counter()
    for _mat, _grp, _ch, layer in iter_all_layers():
        counter[layer.uid if not layer.is_linked else layer.linked_layer_uid] += 1
    return counter


def is_layer_linked(check_layer: Layer, link_counter: Optional[Counter] = None) -> bool:
    """Check if the layer is linked (referenced by more than one layer entry)."""
    if link_counter is None:
        link_counter = build_layer_link_counter()
    return link_counter[check_layer.uid if not check_layer.is_linked else check_layer.linked_layer_uid] > 1

def sort_actions(context: bpy.types.Context, global_layer: GlobalLayer) -> list[MarkerAction]:
    sorted_actions = []
    if global_layer.actions:
        for action in global_layer.actions:
            if action.action_bind == 'FRAME':
                sorted_actions.append((action.frame, action))
            elif action.action_bind == 'MARKER':
                marker = context.scene.timeline_markers.get(action.marker_name)
                if marker:
                    sorted_actions.append((marker.frame, action))
                else:
                    sorted_actions.append((0, action))
        sorted_actions.sort(key=lambda x: x[0])
    return [x for _, x in sorted_actions]



# Legacy PropertyGroups / parser: imported from .legacy and re-exported for registration + callers


classes = (
    MarkerAction,
    GlobalLayer,
    LayerMask,
    Layer,
    Channel,
    Group,
    ClipboardLayer,
    TempMaterial,
    PaintSystemGlobalData,
    MaterialData,
    LegacyPaintSystemLayer,
    LegacyPaintSystemGroup,
    LegacyPaintSystemGroups,
    )

_register, _unregister = register_classes_factory(classes)

def register():
    """Register the Paint System data module."""
    _register()
    bpy.types.Scene.ps_scene_data = PointerProperty(
        type=PaintSystemGlobalData,
        name="Paint System Data",
        description="Data for the Paint System"
    )
    bpy.types.Material.ps_mat_data = PointerProperty(
        type=MaterialData,
        name="Paint System Material Data",
        description="Material Data for the Paint System"
    )
    bpy.types.Material.paint_system = PointerProperty(type=LegacyPaintSystemGroups)
    
def unregister():
    """Unregister the Paint System data module."""
    del bpy.types.Material.paint_system
    del bpy.types.Material.ps_mat_data
    del bpy.types.Scene.ps_scene_data
    _unregister()