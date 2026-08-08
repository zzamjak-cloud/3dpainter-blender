"""Layer 관련 PropertyGroup 정의.

MarkerAction, GlobalLayer(레거시), LayerMask, Layer를 담당한다.
Channel/Group 등 상위 개념은 data.py(파사드)와 channel.py에 있으므로,
순환 임포트를 피하기 위해 이 모듈은 Channel을 최상위에서 임포트하지 않는다.
data.py에만 남아있는 헬퍼(find_channels_containing_layer 등)가 필요하면
메서드 내부에서 지연 임포트(late import)로 가져온다.
"""
from typing import List
from ..utils.logging import get_logger

logger = get_logger(__name__)
import math
import uuid

import bpy
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
)
from mathutils import Euler, Vector

from .context import parse_context
from .enums import *
from .graph import create_layer_graph, get_layer_blend_type
from .graph.common import DEFAULT_PS_UV_MAP_NAME
from .image import save_image
from .layer_types import get_layer_type, layer_modifies_color
from .nested_list_manager import BaseNestedListItem
from ..utils.nodes import find_node, get_node_socket_enum, get_nodetree_socket_enum


def update_brush_settings(self=None, context: bpy.types.Context = bpy.context):
    if context.mode != 'PAINT_TEXTURE':
        return
    ps_ctx = parse_context(context)
    active_layer = ps_ctx.active_layer
    if not active_layer:
        return
    brush = context.tool_settings.image_paint.brush
    if not brush:
        return
    brush.use_alpha = not active_layer.lock_alpha

def update_active_image(self=None, context: bpy.types.Context = None):
    context = context or bpy.context
    ps_ctx = parse_context(context)
    image_paint = context.tool_settings.image_paint
    obj = ps_ctx.ps_object
    mat = ps_ctx.active_material
    active_channel = ps_ctx.active_channel
    if not mat or not active_channel:
        return
    active_layer = ps_ctx.active_layer
    update_brush_settings(self, context)

    if image_paint.mode == 'MATERIAL':
        image_paint.mode = 'IMAGE'
    if not active_layer or active_layer.lock_layer or active_channel.use_bake_image:
        image_paint.canvas = None
        # Unable to paint
        return
    
    selected_image: Image = active_layer.image
    image_paint.canvas = selected_image
    if active_layer.coord_type == 'UV':
        if active_layer.uv_map_name and obj.data.uv_layers.get(active_layer.uv_map_name):
            obj.data.uv_layers[active_layer.uv_map_name].active = True
    elif active_layer.coord_type == 'AUTO' and obj.data.uv_layers.get(DEFAULT_PS_UV_MAP_NAME):
        obj.data.uv_layers[DEFAULT_PS_UV_MAP_NAME].active = True

def update_active_layer(self, context):
    ps_ctx = parse_context(context)
    active_layer = ps_ctx.active_layer
    if active_layer:
        active_layer.update_node_tree(context)

def update_active_channel(self, context):
    ps_ctx = parse_context(context)
    active_channel = ps_ctx.active_channel
    if active_channel:
        active_channel.update_node_tree(context)

def update_active_group(self, context):
    ps_ctx = parse_context(context)
    active_group = ps_ctx.active_group
    if active_group:
        active_group.update_node_tree(context)


class MarkerAction(PropertyGroup):
    action_bind: EnumProperty(
        name="Action Bind",
        description="Action bind",
        items=ACTION_BIND_ENUM
    )
    action_type: EnumProperty(
        name="Action Type",
        description="Action type",
        items=ACTION_TYPE_ENUM
    )
    frame: IntProperty(
        name="Frame",
        description="Frame to enable/disable the layer",
        default=0
    )
    marker_name: StringProperty(
        name="Marker Name",
        description="Marker name",
        default=""
    )
    enabled: BoolProperty(
        name="Enabled",
        description="Enable the layer on a specific frame",
        default=True
    )

class GlobalLayer(PropertyGroup):
    """DEPRECATED -- Legacy global layer data.
    
    This class is kept only for backward-compatible migration (see ``versioning.py``).
    Global layer entries are cleared on file load after migration. Do not use for new code.
    """
    def find_node(self, identifier: str) -> Node | None:
        from .data import get_node_from_nodetree
        return get_node_from_nodetree(self.node_tree, identifier)
            
    @property
    def mix_node(self) -> Node | None:
        return self.find_node("mix_rgb")
    
    @property
    def post_mix_node(self) -> Node | None:
        return self.find_node("post_mix")
    
    @property
    def pre_mix_node(self) -> Node | None:
        return self.find_node("pre_mix")
    
    name: StringProperty()
    
    layer_name: StringProperty(
        name="Name",
        description="Layer name",
    )
    updating_name_flag: bpy.props.BoolProperty(
        default=False, 
        options={'SKIP_SAVE'} # Don't save this flag in the .blend file
    )
    image: PointerProperty(
        name="Image",
        type=Image,
    )
    actions: CollectionProperty(
        type=MarkerAction,
        name="Actions",
        description="Collection of actions for the layer"
    )
    active_action_index: IntProperty(
        name="Active Action Index",
        description="Active action index",
        default=0
    )
    custom_node_tree: PointerProperty(
        name="Custom Node Tree",
        type=NodeTree,
    )
    custom_color_input: IntProperty(
        name="Custom Color Input",
        description="Custom color input",
        default=-1,
    )
    custom_alpha_input: IntProperty(
        name="Custom Alpha Input",
        description="Custom alpha input",
        default=-1,
    )
    custom_color_output: IntProperty(
        name="Custom Color Output",
        description="Custom color output",
        default=-1,
    )
    custom_alpha_output: IntProperty(
        name="Custom Alpha Output",
        description="Custom alpha output",
        default=-1,
    )
    coord_type: EnumProperty(
        items=COORDINATE_TYPE_ENUM,
        name="Coordinate Type",
        description="Coordinate type",
        default='UV',
    )
    uv_map_name: StringProperty(
        name="UV Map",
        description="Name of the UV map to use",
    )
    adjustment_type: EnumProperty(
        items=ADJUSTMENT_TYPE_ENUM,
        name="Adjustment Type",
        description="Adjustment type",
    )
    empty_object: PointerProperty(
        name="Empty Object",
        type=Object,
    )
    gradient_type: EnumProperty(
        items=GRADIENT_TYPE_ENUM,
        name="Gradient Type",
        description="Gradient type",
        default='LINEAR',
    )
    texture_type: EnumProperty(
        items=TEXTURE_TYPE_ENUM,
        name="Texture Type",
        description="Texture type",
    )
    geometry_type: EnumProperty(
        items=GEOMETRY_TYPE_ENUM,
        name="Geometry Type",
        description="Geometry type",
    )
    normalize_normal: BoolProperty(
        name="Normalize Normal",
        description="Normalize the normal",
        default=False,
    )
    type: EnumProperty(
        items=LAYER_TYPE_ENUM,
        default='IMAGE'
    )
    lock_layer: BoolProperty(
        name="Lock Layer",
        description="Lock the layer",
        default=False,
    )
    node_tree: PointerProperty(
        name="Node Tree",
        type=NodeTree
    )
    external_image: PointerProperty(
        name="Edit External Image",
        type=Image,
    )
    is_expanded: BoolProperty(
        name="Expanded",
        description="Expand the layer",
        default=True,
    )
    is_clip: BoolProperty(
        name="Clip",
        description="Clip the layer",
        default=False,
    )
    enabled: BoolProperty(
        name="Enabled",
        description="Toggle layer visibility",
        default=True,
        options=set()
    )
    lock_alpha: BoolProperty(
        name="Lock Alpha",
        description="Lock the alpha channel",
        default=False,
    )

class LayerMask(PropertyGroup):
    uid: StringProperty()
    name: StringProperty(
        name="Name",
        description="Name of the mask",
        default="Mask",
    )
    node_tree: PointerProperty(
        name="Node Tree",
        type=NodeTree,
    )
    type: EnumProperty(
        items=MASK_TYPE_ENUM,
        name="Mask Type",
        description="Mask type",
        default='VALUE',
    )
    coord_type: EnumProperty(
        items=MASK_COORDINATE_TYPE_ENUM,
        name="Coordinate Type",
        description="Coordinate type",
        default='UV',
    )
    blend_mode: EnumProperty(
        items=MASK_BLEND_MODE_ENUM,
        name="Blend Mode",
        description="Blend mode",
        default='MULTIPLY',
    )
    mask_image: PointerProperty(
        name="Mask Image",
        type=Image,
    )
    mask_uv_map: StringProperty(
        name="Mask UV Map",
        description="Mask UV map",
        default="",
    )

def add_empty_to_collection(context: bpy.types.Context, empty_object: bpy.types.Object):
    from .data import get_paint_system_collection
    collection = get_paint_system_collection(context)
    if empty_object.name not in collection.objects:
        collection.objects.link(empty_object)

class Layer(BaseNestedListItem):
    """A single paint layer (image, solid colour, adjustment, etc.) within a channel.
    
    Layers are organized in a nested hierarchy (via BaseNestedListItem) and can
    be linked across materials by sharing a ``linked_layer_uid``.
    """
    
    # Deprecated
    ref_layer_id: StringProperty()
    
    def update_name(self, context):
        if self.layer_name != self.name:
            self.layer_name = self.name
        self.update_node_tree(context)
    
    name: StringProperty(
        name="Name",
        description="Layer name",
        default="Layer",
        update=update_name
    )
    
    def update_node_tree(self, context):
        from .data import ExpectedSocket, ensure_sockets, ensure_paint_system_uv_map, get_paint_system_collection, is_valid_uuidv4
        if not self.auto_update_node_tree:
            return
        # Ensure the paint system UV map even if it's linked
        if self.get_layer_data().coord_type == 'AUTO':
            ensure_paint_system_uv_map(context)
        
        # If the layer is linked, do nothing
        if self.is_linked:
            return
        
        # Ensure a valid UUID
        if not is_valid_uuidv4(self.uid):
            self.uid = str(uuid.uuid4())
        
        # If the layer is blank, do nothing
        if self.type == "BLANK":
            return
        
        # Make sure blend mode is not PASSTHROUGH with non-folder layers
        if self.blend_mode == "PASSTHROUGH" and self.type != "FOLDER":
            self.blend_mode = "MIX"
        
        # Ensure node tree
        if not self.node_tree:
            node_tree = bpy.data.node_groups.new(name=f"PS_Layer ({self.name})", type='ShaderNodeTree')
            self.node_tree = node_tree
        
        # Ensure sockets
        expected_input = [
            ExpectedSocket(name="Clip", socket_type="NodeSocketBool"),
            ExpectedSocket(name="Color", socket_type="NodeSocketColor"),
            ExpectedSocket(name="Alpha", socket_type="NodeSocketFloat"),
        ]
        if self.type == "FOLDER":
            expected_input.append(ExpectedSocket(name="Over Color", socket_type="NodeSocketColor"))
            expected_input.append(ExpectedSocket(name="Over Alpha", socket_type="NodeSocketFloat"))
        expected_output = [
            ExpectedSocket(name="Color", socket_type="NodeSocketColor"),
            ExpectedSocket(name="Alpha", socket_type="NodeSocketFloat"),
        ]
        ensure_sockets(self.node_tree, expected_input, "INPUT")
        ensure_sockets(self.node_tree, expected_output, "OUTPUT")
        
        # Update node tree name
        if self.name:
            self.node_tree.name = f".PS {self.name} ({self.uid[:8]})"
        
        if self.coord_type == "DECAL":
            if not self.empty_object:
                self.ensure_empty_object()
                self.empty_object.empty_display_type = 'SINGLE_ARROW'
            elif self.empty_object.name not in context.view_layer.objects:
                add_empty_to_collection(context, self.empty_object)
        
        match self.type:
            case "IMAGE":
                if self.image:
                    self.image.name = self.name
            case "GRADIENT":
                if self.gradient_type in ('LINEAR', 'RADIAL', 'FAKE_LIGHT'):
                    if not self.empty_object:
                        self.ensure_empty_object()
                        if self.gradient_type == 'LINEAR':
                            self.empty_object.empty_display_type = 'SINGLE_ARROW'
                        elif self.gradient_type == 'RADIAL':
                            self.empty_object.empty_display_type = 'SPHERE'
                        elif self.gradient_type == 'FAKE_LIGHT':
                            self.empty_object.location += Vector((0, 0, 2))
                            self.empty_object.rotation_euler = Euler((3*math.pi/4, math.pi/4, 0))
                            self.empty_object.empty_display_type = 'SINGLE_ARROW'
                    elif self.empty_object.name not in context.view_layer.objects:
                        add_empty_to_collection(context, self.empty_object)
        
        # Clean up
        spec = get_layer_type(self.type)
        if self.empty_object and not (spec and spec.keeps_empty_object):
            collection = get_paint_system_collection(context)
            if self.empty_object.name in collection.objects:
                collection.objects.unlink(self.empty_object)
        elif self.type == "IMAGE" and self.empty_object and self.coord_type != "DECAL":
            collection = get_paint_system_collection(context)
            if self.empty_object.name in collection.objects:
                collection.objects.unlink(self.empty_object)
        
        layer_graph = create_layer_graph(self)
        layer_graph.compile()
        
        # For fake light, we need to update the empty object rotation via drivers
        object_rot_node = self.find_node("object_rotation")
        def add_rot_driver_to_socket(socket: NodeSocket, transform_type: str = "ROT_X"):
            # Try to delete the driver first
            try:
                socket.driver_remove("default_value")
            except Exception:
                pass
            curve = socket.driver_add("default_value")
            curve.driver.type = "AVERAGE"
            driver_var = curve.driver.variables.new()
            driver_var.name = "rotation_euler"
            driver_var.type = "TRANSFORMS"
            driver_var.targets[0].id = self.empty_object
            driver_var.targets[0].transform_type = transform_type
            return curve
        if object_rot_node:
            add_rot_driver_to_socket(object_rot_node.inputs["X"], "ROT_X")
            add_rot_driver_to_socket(object_rot_node.inputs["Y"], "ROT_Y")
            add_rot_driver_to_socket(object_rot_node.inputs["Z"], "ROT_Z")
        
        update_active_image(self, context)
    
            
    def find_node(self, identifier: str) -> Node | None:
        from .data import get_node_from_nodetree
        self = self.get_layer_data()
        return get_node_from_nodetree(self.node_tree, identifier)
            
    @property
    def mix_node(self) -> Node | None:
        self = self.get_layer_data()
        return self.find_node("mix_rgb")
    
    @property
    def post_mix_node(self) -> Node | None:
        self = self.get_layer_data()
        return self.find_node("post_mix")
    
    @property
    def source_node(self) -> Node | None:
        if not self.node_tree:
            return None
        source_node = self.node_tree.nodes.get("source")
        if source_node:
            return source_node
        # Backup
        source_node = self.find_node("source")
        if source_node:
            return source_node
        # Legacy source node
        spec = get_layer_type(self.type)
        if spec and spec.legacy_source_name:
            return self.find_node(spec.legacy_source_name)
        return None
    
    @property
    def pre_mix_node(self) -> Node | None:
        self = self.get_layer_data()
        return self.find_node("pre_mix")
    
    @property
    def opacity(self) -> float:
        self = self.get_layer_data()
        return self.pre_mix_node.inputs['Opacity'].default_value

    uid: StringProperty()
    
    def update_layer_name(self, context):
        if self.layer_name != self.name:
            self.name = self.layer_name
        self.update_node_tree(context)
    
    layer_name: StringProperty(
        name="Name",
        description="Layer name",
        update=update_layer_name
    )
    updating_name_flag: BoolProperty(
        default=False, 
        options={'SKIP_SAVE'} # Don't save this flag in the .blend file
    )
    image: PointerProperty(
        name="Image",
        type=Image,
        update=update_node_tree
    )
    correct_image_aspect: BoolProperty(
        name="Correct Image Aspect",
        description="Correct the image aspect",
        default=True,
        update=update_node_tree
    )
    
    # Layer actions
    actions: CollectionProperty(
        type=MarkerAction,
        name="Actions",
        description="Collection of actions for the layer"
    )
    active_action_index: IntProperty(
        name="Active Action Index",
        description="Active action index",
        default=0
    )
    
    # For NODE_GROUP type
    custom_node_tree: PointerProperty(
        name="Custom Node Tree",
        type=NodeTree,
        update=update_node_tree
    )
    def get_inputs_enum(self, context: Context):
        inputs = []
        if self.type == "NODE_GROUP" and self.custom_node_tree:
            custom_node_tree = bpy.data.node_groups.get(self.custom_node_tree.name)
            if custom_node_tree:
                inputs = get_nodetree_socket_enum(custom_node_tree, in_out='INPUT', include_none=True)
        elif self.source_node:
            inputs = get_node_socket_enum(self.source_node, in_out='INPUT', include_none=True)
        else:
            inputs = [('_NONE_', 'None', '', 'BLANK1', 0)]
        return inputs
    def get_color_enum(self, context: Context):
        outputs = []
        if self.type == "NODE_GROUP" and self.custom_node_tree:
            custom_node_tree = bpy.data.node_groups.get(self.custom_node_tree.name)
            if custom_node_tree:
                outputs = get_nodetree_socket_enum(custom_node_tree, in_out='OUTPUT', include_none=False)
        elif self.source_node:
            outputs = get_node_socket_enum(self.source_node, in_out='OUTPUT', favor_socket_name='Color', include_none=False, none_at_start=False)
        else:
            outputs = [('_NONE_', 'None', '', 'BLANK1', 0)]
        return outputs
    def get_alpha_enum(self, context: Context):
        outputs = []
        if self.type == "NODE_GROUP" and self.custom_node_tree:
            custom_node_tree = bpy.data.node_groups.get(self.custom_node_tree.name)
            if custom_node_tree:
                outputs = get_nodetree_socket_enum(custom_node_tree, in_out='OUTPUT', include_none=True)
        elif self.source_node:
            outputs = get_node_socket_enum(self.source_node, in_out='OUTPUT', favor_socket_name='Alpha', include_none=True, none_at_start=False)
        else:
            outputs = [('_NONE_', 'None', '', 'BLANK1', 0)]
        return outputs
    color_input_name: EnumProperty(
        name="Color Input Socket Name",
        description="Color input socket",
        items=get_inputs_enum,
        update=update_node_tree
    )
    alpha_input_name: EnumProperty(
        name="Alpha Input Socket Name",
        description="Alpha input socket",
        items=get_inputs_enum,
        update=update_node_tree
    )
    color_output_name: EnumProperty(
        name="Color Output Socket Name",
        description="Color output socket",
        items=get_color_enum,
        update=update_node_tree
    )
    alpha_output_name: EnumProperty(
        name="Alpha Output Socket Name",
        description="Alpha output socket",
        items=get_alpha_enum,
        update=update_node_tree
    )
    
    # Deprecated. Use color_input_socket
    custom_color_input: IntProperty(
        name="Custom Color Input",
        description="Custom color input",
        default=-1,
        update=update_node_tree
    )
    # Deprecated. Use alpha_input_socket instead
    custom_alpha_input: IntProperty(
        name="Custom Alpha Input",
        description="Custom alpha input",
        default=-1,
        update=update_node_tree
    )
    # Deprecated. Use color_output_socket instead
    custom_color_output: IntProperty(
        name="Custom Color Output",
        description="Custom color output",
        default=-1,
        update=update_node_tree
    )
    # Deprecated. Use alpha_output_socket instead
    custom_alpha_output: IntProperty(
        name="Custom Alpha Output",
        description="Custom alpha output",
        default=-1,
        update=update_node_tree
    )
    def set_projection_view(self, context: Context):
        ps_ctx = parse_context(context)
        active_space = context.area.spaces.active
        if active_space.type == 'VIEW_3D':
            region_3d = active_space.region_3d
            if region_3d:
                match region_3d.view_perspective:
                    case 'PERSP':
                        view_mat = region_3d.view_matrix.copy()
                        if self.projection_space == "OBJECT":
                            view_mat = view_mat @ ps_ctx.ps_object.matrix_world
                        view_mat.invert()
                        loc, rot, sca = view_mat.decompose()
                        self.projection_position = loc
                        self.projection_rotation = rot.to_euler()
                        self.projection_fov = 2*math.atan(0.5*72/active_space.lens)
                    case 'ORTHO':
                        # TODO: Implement orthographic projection
                        pass
                    case "CAMERA":
                        active_camera = bpy.context.scene.camera
                        view_mat = active_camera.matrix_world.copy()
                        if self.projection_space == "OBJECT":
                            view_mat = ps_ctx.ps_object.matrix_world.inverted() @ view_mat
                        loc, rot, sca = view_mat.decompose()
                        self.projection_position = loc
                        self.projection_rotation = rot.to_euler()
                        self.projection_fov = active_camera.data.angle
                    case _:
                        pass
    def update_coord_type(self, context: Context):
        if self.coord_type in ['DECAL', 'PROJECT']:
            if self.type == "IMAGE":
                image_node = self.source_node
                if image_node:
                    image_node.extension = "CLIP"
        if self.coord_type == "PROJECT" and not self.find_node("proj_node"):
            # Capture the camera position
            self.set_projection_view(context)
        self.update_node_tree(context)
    coord_type: EnumProperty(
        items=COORDINATE_TYPE_ENUM,
        name="Coordinate Type",
        description="Coordinate type",
        default='UV',
        update=update_coord_type,
    )
    uv_map_name: StringProperty(
        name="UV Map",
        description="Name of the UV map to use",
        update=update_node_tree
    )
    adjustment_type: EnumProperty(
        items=ADJUSTMENT_TYPE_ENUM,
        name="Adjustment Type",
        description="Adjustment type",
        update=update_node_tree
    )
    empty_object: PointerProperty(
        name="Empty Object",
        type=Object,
        update=update_node_tree
    )
    gradient_type: EnumProperty(
        items=GRADIENT_TYPE_ENUM,
        name="Gradient Type",
        description="Gradient type",
        default='GRADIENT_MAP',
        update=update_node_tree
    )
    def update_texture_type(self, context: Context):
        self.auto_update_node_tree = False
        try:
            if self.type == "TEXTURE":
                self.color_output_name = "Color"
                self.alpha_output_name = "_NONE_"
        except Exception:
            pass
        self.auto_update_node_tree = True
        self.update_node_tree(context)
    texture_type: EnumProperty(
        items=TEXTURE_TYPE_ENUM,
        name="Texture Type",
        description="Texture type",
        update=update_texture_type
    )
    geometry_type: EnumProperty(
        items=GEOMETRY_TYPE_ENUM,
        name="Geometry Type",
        description="Geometry type",
        update=update_node_tree
    )
    normalize_normal: BoolProperty(
        name="Normalize Normal",
        description="Normalize the normal",
        default=False,
        update=update_node_tree
    )
    def update_type(self, context: Context):
        try:
            if self.type == "IMAGE":
                self.color_output_name = "Color"
                self.alpha_output_name = "Alpha"
        except Exception:
            pass
        self.update_node_tree(context)
    type: EnumProperty(
        items=LAYER_TYPE_ENUM,
        default='BLANK',
        update=update_type
    )
    lock_layer: BoolProperty(
        name="Lock Layer",
        description="Lock the layer",
        default=False,
        update=update_active_image
    )
    node_tree: PointerProperty(
        name="Node Tree",
        type=NodeTree
    )
    edit_external_mode: EnumProperty(
        items=EDIT_EXTERNAL_MODE_ENUM,
        name="Edit External Mode",
        description="Edit external mode",
        default='IMAGE_EDIT'
    )
    external_image: PointerProperty(
        name="Edit External Image",
        type=Image,
    )
    is_expanded: BoolProperty(
        name="Expanded",
        description="Expand the layer",
        default=True,
        # update=select_layer
    )
    def update_is_clip(self, context: Context):
        self.update_node_tree(context)
        update_active_channel(self, context)
    is_clip: BoolProperty(
        name="Clip",
        description="Clip the layer",
        default=False,
        update=update_is_clip
    )
    enabled: BoolProperty(
        name="Enabled",
        description="Toggle layer visibility",
        default=True,
        update=update_node_tree,
        options=set()
    )
    lock_alpha: BoolProperty(
        name="Lock Alpha",
        description="Lock the alpha channel",
        default=False,
        update=update_brush_settings
    )
    
    # For parallax coordinate type
    parallax_space: EnumProperty(
        items=PARALLAX_TYPE_ENUM,
        name="Parallax Type",
        description="Parallax type",
        default='UV',
        update=update_node_tree
    )
    parallax_uv_map_name: StringProperty(
        name="Parallax UV Map",
        description="Name of the UV map to use for parallax",
        update=update_node_tree
    )
    
    # Decal properties
    use_decal_depth_clip: BoolProperty(
        name="Use Decal Depth Clip",
        description="Use the decal depth clip",
        default=True,
        update=update_node_tree
    )
    
    # Projection properties
    def update_projection_values(self, context):
        """투영 프로퍼티는 그래프 구조를 바꾸지 않고 proj_node 소켓 값만 바꾼다.

        전체 재컴파일 대신 소켓에 직접 기록해 드래그 중 매 샘플마다 발생하던
        노드 그래프 재빌드를 없앤다. 대상 노드를 찾지 못하면 기존 경로로 폴백한다.
        """
        if not self.auto_update_node_tree:
            return
        proj_node = None if self.is_linked else self.find_node("proj_node")
        if proj_node and self.coord_type == "PROJECT":
            # basic_layers.create_coord_graph 의 proj_node 기본값과 동일한 소켓·변환
            values = {
                "Vector": self.projection_position,
                "Rotation": self.projection_rotation,
                "FOV": self.projection_fov,
                "Object Space": self.projection_space == "OBJECT",
            }
            if all(name in proj_node.inputs for name in values):
                for socket_name, value in values.items():
                    proj_node.inputs[socket_name].default_value = value
                return
        self.update_node_tree(context)

    projection_position: FloatVectorProperty(
        name="Projection Position",
        description="Projection position",
        default=(0, 0, 0),
        update=update_projection_values,
        subtype='TRANSLATION'
    )
    projection_rotation: FloatVectorProperty(
        name="Projection Rotation",
        description="Projection rotation",
        default=(0, 0, 0),
        update=update_projection_values,
        subtype='EULER'
    )
    projection_fov: FloatProperty(
        name="Projection FOV",
        description="Projection FOV",
        default=40/180*math.pi,
        update=update_projection_values,
        subtype='ANGLE'
    )
    projection_space: EnumProperty(
        items=[
            ("WORLD", "World", "World Space Coordinates", "WORLD", 0),
            ("OBJECT", "Object", "Object Space Coordinates", "OBJECT_DATA", 1),
        ],
        name="Projection Mode",
        description="Projection mode",
        default="WORLD",
        update=update_projection_values
    )
    
    # Layer masks
    masks: CollectionProperty(
        name="Layer Masks",
        type=LayerMask,
    )
    
    active_mask_index: IntProperty(
        name="Active Mask Index",
        description="Active mask index",
        update=update_node_tree
    )
    
    def update_blend_mode(self, context: Context):
        from .data import find_channels_containing_layer
        layer_data = self.get_layer_data()
        layer_data.update_node_tree(context)
        for channel in find_channels_containing_layer(layer_data):
            channel.update_node_tree(context)
    def get_blend_mode_items(self, context: Context) -> list[tuple[str, str, str]]:
        return BLEND_MODE_ENUM if self.type == "FOLDER" else [blend_mode for blend_mode in BLEND_MODE_ENUM if blend_mode is None or blend_mode[0] != "PASSTHROUGH"]
    blend_mode: EnumProperty(
        items=get_blend_mode_items,
        name="Blend Mode",
        description="Blend mode",
        update=update_blend_mode
    )
    
    auto_update_node_tree: BoolProperty(
        name="Update Node Tree",
        description="Update the node tree",
        default=True,
        options={'SKIP_SAVE'}
    )
    
    # Linked layer data
    @property
    def is_linked(self) -> bool:
        # logger.debug(f"Linked layer {self.linked_layer_uid} to material {self.linked_material.name if self.linked_material else 'None'}")
        return bool(self.linked_layer_uid and self.linked_material)
    
    linked_layer_uid: StringProperty(
        name="Linked Layer ID",
        description="Linked layer ID",
        default="",
        update=update_node_tree
    )
    linked_material: PointerProperty(
        name="Linked Material",
        type=bpy.types.Material,
        update=update_node_tree
    )
    
    update_node_tree_flag: BoolProperty(
        name="Update Node Tree Flag",
        description="Update the node tree flag",
        default=True,
        options={'SKIP_SAVE'}
    )
    
    def create_mask(self, mask_type: str):
        mask = self.masks.add()
        mask.type = mask_type
        return mask
    
    def remove_mask(self, index: int):
        self.masks.remove(index)
    
    def remove_active_mask(self):
        self.masks.remove(self.active_mask_index)
        
    def add_action(self, name: str, action_bind: str, action_type: str, frame: int|None = None, marker_name: str|None = None):
        from .data import invalidate_action_layer_cache
        action = self.actions.add()
        action.name = name
        action.action_bind = action_bind
        action.action_type = action_type
        if action_bind == 'FRAME':
            if frame is None:
                raise ValueError("Frame is required")
            action.frame = frame
        elif action_bind == 'MARKER':
            if marker_name is None:
                raise ValueError("Marker name is required")
            action.marker_name = marker_name
        invalidate_action_layer_cache()
        return action

    def remove_action(self, index: int):
        from .data import invalidate_action_layer_cache
        self.actions.remove(index)
        invalidate_action_layer_cache()

    def remove_active_action(self):
        from .data import invalidate_action_layer_cache
        self.actions.remove(self.active_action_index)
        self.active_action_index = min(self.active_action_index, len(self.actions) - 1)
        invalidate_action_layer_cache()
    
    @property
    def uses_coord_type(self) -> bool:
        spec = get_layer_type(self.type)
        return bool(spec and spec.uses_coord)
    
    def get_layer_warnings(self, context: Context) -> List[str]:
        ps_ctx = parse_context(context)
        layer_data = self.get_layer_data()
        active_channel = ps_ctx.active_channel
        flattened = active_channel.flatten_hierarchy()
        current_flat_index = next(
            (i for i, (it, _) in enumerate(flattened) if it.id == self.id), -1)
        below_layer, next_index = active_channel.get_next_sibling_item(flattened, current_flat_index)
        # If below_layer have different parent below_layer = None
        if below_layer and active_channel.get_parent_layer_id(self, ignore_passthrough=True) != active_channel.get_parent_layer_id(below_layer, ignore_passthrough=True):
            below_layer = None
        warnings = []
        blend_mode = get_layer_blend_type(layer_data)
        group_node = find_node(ps_ctx.active_material.node_tree, {
            'bl_idname': 'ShaderNodeGroup', 'node_tree': ps_ctx.active_group.node_tree})
        color_channel_name = ps_ctx.active_channel.name
        alpha_channel_name = ps_ctx.active_channel.name + " Alpha"
        has_node_connected = any(input.is_linked for input in group_node.inputs if input.name in {color_channel_name, alpha_channel_name}) if group_node else False
        is_last_layer = current_flat_index == len(flattened) - 1
        # If no layer below
        if not below_layer:
            if not has_node_connected or not is_last_layer:
                is_in_folder = active_channel.get_parent_layer_id(self, ignore_passthrough=True) != -1
                if blend_mode != 'MIX':
                    if is_in_folder:
                        warnings.append("Last layer in folder. Blending may not work. Use folder with Passthrough blend mode.")
                    else:
                        warnings.append("No layer below. Blending may not work.")
                if layer_data.type == "ADJUSTMENT":
                    warnings.append("No layer below. Adjustment effects may not work.")
            else:
                # Check if alpha is 0
                if active_channel.use_alpha and group_node and group_node.inputs[alpha_channel_name].default_value == 0:
                    warnings.append(f"Input Alpha of {color_channel_name} channel is 0. Blending may not work.")
            
        return warnings
    
    def ensure_empty_object(self):
        context = bpy.context
        ps_ctx = parse_context(context)
        empty_name = f"{self.name} ({self.uid[:8]}) Empty"
        if empty_name in bpy.data.objects:
            empty_object = bpy.data.objects[empty_name]
            empty_object.parent = ps_ctx.ps_object
            add_empty_to_collection(context, empty_object)
        else:
            with bpy.context.temp_override():
                empty_object = bpy.data.objects.new(empty_name, None)
                empty_object.parent = ps_ctx.ps_object
                add_empty_to_collection(context, empty_object)
        self.empty_object = empty_object
        return empty_object
    
    def duplicate_layer_data(self, layer: "Layer"):
        self.uid = str(uuid.uuid4())
        if layer.node_tree:
            self.node_tree = layer.node_tree.copy()
        if layer.image:
            # if image is not saved, save it
            image: Image = layer.image
            save_image(image)
            self.image = image.copy()
        if layer.empty_object:
            self.empty_object = layer.empty_object.copy()
            self.empty_object.name = f"{self.name} ({self.uid[:8]}) Empty"
            self.ensure_empty_object()
    
    def link_layer_data(self, layer: "Layer"):
        self.apply_properties(layer, self, ignore_props=["name", "uid", "id", "order", "parent_id", "layer_name"])
    
    def unlink_layer_data(self):
        from .data import is_layer_linked
        layer = self.get_layer_data()
        if is_layer_linked(self) and not self.is_linked:
            # self owns the data
            self.transfer_linked_data()
            self.duplicate_layer_data(self)
        else:
            self.linked_layer_uid = ""
            self.linked_material = None
            self.copy_layer_data(layer)
    
    def copy_layer_data(self, layer: "Layer"):
        self.duplicate_layer_data(layer)
        self.apply_properties(layer, self, ignore_props=["name", "uid", "node_tree", "image", "empty_object", "type", "id", "order", "parent_id", "layer_name"])
    
    def get_layer_data(self) -> "Layer":
        from .data import _get_material_layer_uid_map
        if self.is_linked:
            if not self.linked_material or not self.linked_material.ps_mat_data:
                logger.error(f"Linked material {self.linked_material.name if self.linked_material else 'None'} not found")
                return None
            
            # Use cached UID lookup dictionary for O(1) access instead of nested loops
            uid_to_layer = _get_material_layer_uid_map(self.linked_material)
            layer = uid_to_layer.get(self.linked_layer_uid)
            if not layer:
                layer = _get_material_layer_uid_map(self.linked_material, force_refresh=True).get(self.linked_layer_uid)
            return layer
        return self
    
    def transfer_linked_data(self):
        linked_layer_uid_map = {}
        for material in bpy.data.materials:
            if hasattr(material, 'ps_mat_data'):
                for group in material.ps_mat_data.groups:
                    for channel in group.channels:
                        for layer in channel.layers:
                            if layer.is_linked and layer.linked_layer_uid == self.uid:
                                linked_layer_uid_map[layer.uid] = [layer, material]
        # Migrate layer data to one of the linked layers
        linked_layers = [layer for layer, _ in linked_layer_uid_map.values() if layer.is_linked and layer.linked_layer_uid == self.uid]
        new_main_layer, new_material = list(linked_layer_uid_map.values())[0]
        new_main_layer.link_layer_data(self)
        
        for linked_layer in linked_layers[1:]:
            linked_layer.linked_layer_uid = new_main_layer.uid
            linked_layer.linked_material = new_material
        
        return new_main_layer, new_material
    
    def delete_layer_data(self):
        """
        Delete the layer data. Transfer to a linked layer if it is linked.
        """
        from .data import is_layer_linked
        layer = self.get_layer_data()
        if is_layer_linked(layer) and not self.is_linked:
            logger.debug(f"Transferring layer data for {layer.name} to linked layers")
            self.transfer_linked_data()
        else:
            logger.debug(f"Deleting layer data for {self.name}")
            if self.empty_object:
                bpy.data.objects.remove(self.empty_object, do_unlink=True)
            # TODO: The following causes some issue when undoing
            if self.node_tree:
                bpy.data.node_groups.remove(self.node_tree)
    
    def apply_properties(self, from_layer: "Layer", to_layer: "Layer", ignore_props: list[str] = []):
        retry_props = []
        for prop in from_layer.bl_rna.properties:
            pid = getattr(prop, 'identifier', '')
            if not pid or getattr(prop, 'is_readonly', False):
                continue
            if pid in ignore_props:
                continue
            value = getattr(from_layer, pid)
            try:
                setattr(to_layer, pid, value)
            except Exception as e:
                retry_props.append(pid)
        # If some properties failed, force update_node_tree first and then apply the properties again
        failed_props = []
        if retry_props:
            logger.warning(f"Could not apply properties {retry_props} for {to_layer.name}, retrying...")
            original_auto_update_node_tree = bool(to_layer.auto_update_node_tree)
            to_layer.auto_update_node_tree = False
            to_layer.update_node_tree(bpy.context)
            for pid in retry_props:
                value = getattr(from_layer, pid)
                try:
                    setattr(to_layer, pid, value)
                except Exception as e:
                    failed_props.append(pid)
            to_layer.auto_update_node_tree = original_auto_update_node_tree
        if failed_props:
            logger.warning(f"Could not apply properties {failed_props} for {to_layer.name}")
    
    @property
    def modifies_color_data(self) -> bool:
        return layer_modifies_color(self) or self.blend_mode != "MIX"
