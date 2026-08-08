"""Channel PropertyGroup 정의 및 베이크 관련 헬퍼.

Channel은 클래스 정의 시점에 CollectionProperty(type=Layer)와 item_type에서
Layer가 필요하므로 .layer를 최상위에서 임포트한다. data.py에만 남아있는
헬퍼(get_udim_tiles 등)가 필요한 곳은 순환 임포트를 피하기 위해
메서드/함수 내부에서 지연 임포트한다.
"""
from typing import Dict, List, Literal
from ..utils.logging import get_logger

logger = get_logger(__name__)
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
)
from bpy.types import (
    Context,
    Image,
    Material,
    NodeTree,
    Object,
)
from bpy_extras.node_utils import connect_sockets

from .context import parse_context
from .enums import *
from .graph import build_channel_graph, get_layer_blend_type
from .image import blender_image_to_numpy, set_image_pixels, save_image
from .layer import Layer, update_active_group, update_active_image
from .nested_list_manager import BaseNestedListManager
from ..utils import get_next_unique_name
from ..utils.nodes import find_node, get_material_output


def save_cycles_settings():
    settings = {}
    scene = bpy.context.scene
    settings['render_engine'] = scene.render.engine
    settings['device'] = scene.cycles.device
    settings['samples'] = scene.cycles.samples
    settings['preview_samples'] = scene.cycles.preview_samples
    settings['denoiser'] = scene.cycles.denoiser
    settings['use_denoising'] = scene.cycles.use_denoising
    settings['use_adaptive_sampling'] = scene.cycles.use_adaptive_sampling
    return settings

def restore_cycles_settings(settings):
    scene = bpy.context.scene
    scene.render.engine = settings['render_engine']
    scene.cycles.device = settings['device']
    scene.cycles.samples = settings['samples']
    scene.cycles.preview_samples = settings['preview_samples']
    scene.cycles.denoiser = settings['denoiser']
    scene.cycles.use_denoising = settings['use_denoising']
    scene.cycles.use_adaptive_sampling = settings['use_adaptive_sampling']

def ps_bake(context, objects: list[Object], mat: Material, uv_layer, bake_image, use_gpu=True, use_clear=True, margin=8, margin_type='ADJACENT_FACES'):
    from .data import ensure_udim_tiles
    bake_objects = []
    
    ensure_udim_tiles(bake_image, objects, uv_layer)
    
    for obj in objects:
        if mat.name in obj.data.materials:
            bake_objects.append(obj)
    
    cycles_settings = save_cycles_settings()
    # Switch to Cycles if needed
    
    orig_view_transform = str(context.scene.view_settings.view_transform)
    
    node_tree = mat.node_tree
    
    image_node = node_tree.nodes.new(type='ShaderNodeTexImage')
    image_node.image = bake_image
    with context.temp_override(active_object=bake_objects[0], selected_objects=bake_objects):
        bake_params = {
            "type": 'EMIT',
            "margin": margin,
            "margin_type": margin_type,
        }
        if context.scene.render.engine != 'CYCLES':
            context.scene.render.engine = 'CYCLES'
        context.scene.view_settings.view_transform = "Standard"
        cycles = context.scene.cycles
        cycles.device = 'GPU' if use_gpu else 'CPU'
        cycles.samples = 1
        cycles.use_denoising = False
        cycles.use_adaptive_sampling = False
        for node in node_tree.nodes:
            node.select = False

        image_node.select = True
        node_tree.nodes.active = image_node
        try:
            bpy.ops.object.bake(**bake_params, uv_layer=uv_layer, use_clear=use_clear)
        except Exception as e:
            # Try baking with CPU if GPU fails
            logger.debug(f"GPU baking failed, trying CPU")
            cycles.device = 'CPU'
            bpy.ops.object.bake(**bake_params, uv_layer=uv_layer, use_clear=use_clear)

    # Delete bake nodes
    node_tree.nodes.remove(image_node)
    
    context.scene.view_settings.view_transform = orig_view_transform
    
    restore_cycles_settings(cycles_settings)

    return bake_image

class Channel(BaseNestedListManager):
    """A paint channel (e.g. Color, Roughness, Normal) that owns a hierarchy of layers.
    
    Compiles its layer graph into a single node tree that can be used by a Group.
    """
    
    def get_parent_layer_id(self, layer: "Layer", ignore_passthrough: bool = False) -> int:
        if layer.parent_id == -1:
            return -1
        parent_layer = self.get_item_by_id(layer.parent_id)
        if ignore_passthrough:
            parent_layer_linked = parent_layer.get_layer_data()
            if parent_layer_linked.blend_mode == "PASSTHROUGH":
                return self.get_parent_layer_id(parent_layer)
        return parent_layer.id

    def get_all_layer_warnings(self, context: Context, ps_ctx=None) -> Dict[int, List[str]]:
        """채널의 모든 레이어 경고를 한 번에 계산해 ``{layer.id: [warning, ...]}``로 반환한다.

        판정 로직은 Layer.get_layer_warnings와 동일하다. 다만 레이어마다 반복되던
        parse_context / flatten_hierarchy / find_node 같은 채널 공통 비용을 1회로 줄인다.
        """
        if ps_ctx is None:
            ps_ctx = parse_context(context)
        flattened = self.flatten_hierarchy()

        # 부모 추적용 id 맵 (get_item_by_id 선형 스캔 대체)
        item_by_id = {}
        for item, _level in flattened:
            item_by_id.setdefault(item.id, item)

        def parent_layer_id(layer: "Layer", ignore_passthrough: bool = False) -> int:
            # Channel.get_parent_layer_id와 동일한 규칙(패스스루는 한 단계만 건너뜀)
            if layer.parent_id == -1:
                return -1
            parent_layer = item_by_id.get(layer.parent_id)
            if parent_layer is None:
                return -1
            if ignore_passthrough:
                parent_layer_linked = parent_layer.get_layer_data()
                if parent_layer_linked and parent_layer_linked.blend_mode == "PASSTHROUGH":
                    return parent_layer_id(parent_layer)
            return parent_layer.id

        group_node = find_node(ps_ctx.active_material.node_tree, {
            'bl_idname': 'ShaderNodeGroup', 'node_tree': ps_ctx.active_group.node_tree})
        color_channel_name = self.name
        alpha_channel_name = self.name + " Alpha"
        has_node_connected = any(input.is_linked for input in group_node.inputs if input.name in {
                                 color_channel_name, alpha_channel_name}) if group_node else False
        last_flat_index = len(flattened) - 1

        warnings_by_id: Dict[int, List[str]] = {}
        for current_flat_index, (layer, _level) in enumerate(flattened):
            layer_data = layer.get_layer_data()
            if not layer_data:
                # 링크가 깨진 레이어는 UIList가 그리지 않으므로 경고도 없다
                warnings_by_id[layer.id] = []
                continue
            below_layer, _next_index = self.get_next_sibling_item(flattened, current_flat_index)
            own_parent_id = parent_layer_id(layer, ignore_passthrough=True)
            # 부모가 다르면 아래 레이어로 치지 않는다
            if below_layer and own_parent_id != parent_layer_id(below_layer, ignore_passthrough=True):
                below_layer = None
            warnings: List[str] = []
            if not below_layer:
                blend_mode = get_layer_blend_type(layer_data)
                if not has_node_connected or current_flat_index != last_flat_index:
                    if blend_mode != 'MIX':
                        if own_parent_id != -1:
                            warnings.append("Last layer in folder. Blending may not work. Use folder with Passthrough blend mode.")
                        else:
                            warnings.append("No layer below. Blending may not work.")
                    if layer_data.type == "ADJUSTMENT":
                        warnings.append("No layer below. Adjustment effects may not work.")
                else:
                    if self.use_alpha and group_node and group_node.inputs[alpha_channel_name].default_value == 0:
                        warnings.append(f"Input Alpha of {color_channel_name} channel is 0. Blending may not work.")
            warnings_by_id[layer.id] = warnings
        return warnings_by_id

    def update_node_tree(self, context:Context):
        build_channel_graph(self, context)

    def update_channel_name(self, context):
        """Update the channel name to ensure uniqueness."""
        if self.updating_name_flag:
            return
        if not self.node_tree:
            return
        self.node_tree.name = f".PS_Channel ({self.name})"
        self.updating_name_flag = True
        parsed_context = parse_context(context)
        active_group = parsed_context.active_group
        new_name = get_next_unique_name(self.name, [channel.name for channel in active_group.channels if channel != self])
        if new_name != self.name:
            self.name = new_name
        self.updating_name_flag = False
        update_active_group(self, context)
    
    def create_layer(
        self, 
        context,
        layer_name: str = "Layer Name",
        layer_type: str = "BLANK", # "BLANK" is a special type that creates a blank layer with no node tree
        update_active_index: bool = True, 
        insert_at: Literal["TOP", "BOTTOM", "CURSOR", "BEFORE", "AFTER"] = "CURSOR", 
        handle_folder: bool = True,
        **kwargs
    ) -> 'Layer':
        from .data import create_ps_image, get_udim_tiles, invalidate_layer_uid_channel_index
        parent_id, insert_order = self.get_insertion_data(handle_folder=handle_folder, insert_at=insert_at)
        # Adjust existing items' order
        self.adjust_sibling_orders(parent_id, insert_order)
        layer = self.add_item(
                layer_name,
                "BLANK",
                parent_id=parent_id,
                order=insert_order
            )
        layer.auto_update_node_tree = False
        layer.type = layer_type
        layer.uid = str(uuid.uuid4())
        for key, value in kwargs.items():
            setattr(layer, key, value)
        
        # Layer type specific setup
        match layer.type:
            case "IMAGE":
                if not layer.image:
                    if layer.coord_type == 'UV':
                        ps_ctx = parse_context(context)
                        use_udim_tiles = get_udim_tiles(ps_ctx.ps_object, layer.uv_map_name) != {1001}
                        layer.image = create_ps_image(layer.name, use_udim_tiles=use_udim_tiles, objects=[ps_ctx.ps_object], uv_layer_name=layer.uv_map_name)
                    else:
                        layer.image = create_ps_image(layer.name)
        
        # Update active index
        if update_active_index:
            new_id = layer.id
            if new_id != -1:
                for i, item in enumerate(self.layers):
                    if item.id == new_id:
                        self.active_index = i
                        break
        layer.auto_update_node_tree = True
        layer.update_node_tree(context)
        self.update_node_tree(context)
        invalidate_layer_uid_channel_index()
        return layer
    
    def set_active_index_to_layer(self, context, layer: "Layer"):
        self.normalize_orders()
        order = int(layer.order)
        parent_id = int(layer.parent_id)
        for i, item in enumerate(self.layers):
            self.active_index = i
            if item.order == order and item.parent_id == parent_id:
                break
        self.active_index = min(
            self.active_index, len(self.layers) - 1)
        self.update_node_tree(context)
    
    def delete_layer(self, context, layer: "Layer"):
        from .data import invalidate_layer_uid_channel_index
        item_id = layer.id
        order = int(layer.order)
        parent_id = int(layer.parent_id)
        logger.debug(f"Deleting layer {layer.name} with id {item_id} and order {order} and parent_id {parent_id}")
        def on_delete(item: "Layer"):
            item.delete_layer_data()
        if item_id != -1 and self.remove_item_and_children(item_id, on_delete):
            # Update active_index
            self.normalize_orders()
            for i, item in enumerate(self.layers):
                self.active_index = i
                if item.order == order and item.parent_id == parent_id:
                    break
        self.active_index = min(
            self.active_index, len(self.layers) - 1)
        self.update_node_tree(context)
        invalidate_layer_uid_channel_index()
    
    def delete_layers(self, context, layers: list["Layer"]):
        # Sort layer by index in descending order
        layers.sort(key=lambda x: self.get_collection_index_from_id(x.id), reverse=True)
        for layer in layers:
            self.delete_layer(context, layer)
    
    def bake(
            self,
            context: Context,
            mat: Material,
            bake_image: Image,
            uv_layer: str,
            use_gpu: bool = True,
            use_group_tree: bool = True,
            force_alpha: bool = True, # Force to use alpha
            as_tangent_normal: bool = False, # Bake as tangent normal
            margin: int = 8, # Margin
            margin_type: Literal['ADJACENT_FACES', 'EXTEND'] = "ADJACENT_FACES", # Margin type
            disable_deform_modifiers: bool = False, # Disable deform modifiers
            ):
        """Bake the channel

        Args:
            context (Context): The context
            mat (Material): The material
            bake_image (Image): The bake image
            uv_layer (str): The UV layer
            use_gpu (bool, optional): Whether to use the GPU. Defaults to True.
            use_group_tree (bool, optional): Whether to use the group tree if found. Defaults to True.

        Raises:
            ValueError: If the node tree is not found
        """
        node_tree = mat.node_tree
        if not node_tree:
            raise ValueError("Node tree not found")
        ps_context = parse_context(context)
        
        if context.active_object and ps_context.active_object.type != "MESH" and ps_context.ps_object.type == "MESH":
            # Change the active object to the ps_object
            ps_context.active_object.select_set(False)
            ps_context.ps_object.select_set(True)
            context.view_layer.objects.active = ps_context.ps_object
        
        ps_context = parse_context(context)
        
        orig_preview_channel = False
        if ps_context.ps_mat_data.preview_channel:
            orig_preview_channel = bool(ps_context.ps_mat_data.preview_channel)
            self.isolate_channel(context)
        
        # 복원 코드가 분기와 무관하게 읽으므로, 변경 대상 프로퍼티는 조건 밖에서 전부 스냅샷한다
        orig_use_alpha = bool(self.use_alpha)
        orig_tangent_uv_map = str(self.tangent_uv_map)
        orig_output_vector_space = str(self.output_vector_space)
        orig_disable_output_transform = bool(self.disable_output_transform)
        saved_modifier_states = []

        if force_alpha:
            self.use_alpha = True

        if as_tangent_normal:
            self.tangent_uv_map = self.bake_uv_map
            self.output_vector_space = "TANGENT"
        else:
            self.disable_output_transform = True
        try:
            ps_objects = ps_context.ps_objects

            # Disable deform modifiers if requested
            if disable_deform_modifiers:
                DEFORM_MODIFIER_TYPES = {
                    'ARMATURE', 'CAST', 'CURVE', 'DISPLACE', 'HOOK', 'LAPLACIANDEFORM',
                    'LATTICE', 'MESH_DEFORM', 'SHRINKWRAP', 'SIMPLE_DEFORM', 'SMOOTH',
                    'CORRECTIVE_SMOOTH', 'LAPLACIANSMOOTH', 'SURFACE_DEFORM', 'WARP', 'WAVE'
                }
                for obj in ps_objects:
                    for mod in obj.modifiers:
                        if mod.type in DEFORM_MODIFIER_TYPES:
                            saved_modifier_states.append((obj, mod.name, mod.show_render))
                            mod.show_render = False
            
            material_output = get_material_output(node_tree)
            surface_socket = material_output.inputs['Surface']
            from_socket = surface_socket.links[0].from_socket if surface_socket.links else None
            
            # Bake as output of group ps if exists in the node tree
            bake_node = None
            to_be_deleted_nodes = []
            color_output = None
            alpha_output = None
            
            if not self.use_alpha:
                # Use the value node set to 1 as alpha output
                value_node = node_tree.nodes.new('ShaderNodeValue')
                value_node.outputs['Value'].default_value = 1.0
                alpha_output = value_node.outputs['Value']
                to_be_deleted_nodes.append(value_node)
            
            if hasattr(mat, "ps_mat_data") and mat.ps_mat_data.groups and use_group_tree:
                for group in mat.ps_mat_data.groups:
                    if group.node_tree and self.name in group.channels:
                        bake_node = find_node(node_tree, {'bl_idname': 'ShaderNodeGroup', 'node_tree': group.node_tree})
                        if bake_node:
                            color_output = bake_node.outputs[self.name]
                            if self.use_alpha:
                                alpha_output = bake_node.outputs[f'{self.name} Alpha']
                            # if orig_use_alpha is False, set alpha socket to 1
                            if self.use_alpha and not orig_use_alpha and bake_node.inputs[self.name].is_linked:
                                bake_node.inputs[f'{self.name} Alpha'].default_value = 1.0
                        break
            
            if not bake_node:
                # Use channel node group instead
                bake_node = node_tree.nodes.new(type='ShaderNodeGroup')
                bake_node.node_tree = self.node_tree
                color_output = bake_node.outputs['Color']
                if self.use_alpha:
                    alpha_output = bake_node.outputs['Alpha']
                to_be_deleted_nodes.append(bake_node)
            
            # Bake image
            connect_sockets(surface_socket, color_output)
            temp_alpha_image = bake_image.copy()
            bake_image = ps_bake(context, ps_objects, mat, uv_layer, bake_image, use_gpu, margin=margin, margin_type=margin_type)
            
            temp_alpha_image.colorspace_settings.name = 'Non-Color'
            connect_sockets(surface_socket, alpha_output)
            temp_alpha_image = ps_bake(context, ps_objects, mat, uv_layer, temp_alpha_image, use_gpu, margin=margin, margin_type=margin_type)

            if bake_image and temp_alpha_image:
                # pixels_bake = np.empty(len(bake_image.pixels), dtype=np.float32)
                # pixels_temp_alpha = np.empty(len(temp_alpha_image.pixels), dtype=np.float32)
                pixels_bake = blender_image_to_numpy(bake_image)
                pixels_temp_alpha = blender_image_to_numpy(temp_alpha_image)
                
                if pixels_bake is None or pixels_temp_alpha is None:
                    return
                
                # Process tiles - handle both UDIM and non-UDIM cases
                temp_alpha_single = pixels_temp_alpha.get_single_tile()
                
                # Update alpha channel for each tile in bake_image
                for tile_num in pixels_bake.tiles.keys():
                    bake_tile = pixels_bake.tiles[tile_num]
                    # Use corresponding tile if available, otherwise use single tile
                    if tile_num in pixels_temp_alpha.tiles:
                        temp_tile = pixels_temp_alpha.tiles[tile_num]
                        bake_tile[:, :, 3] = temp_tile[:, :, 0]
                    else:
                        bake_tile[:, :, 3] = temp_alpha_single[:, :, 0]
                
                set_image_pixels(bake_image, pixels_bake)
                save_image(bake_image)
            bpy.data.images.remove(temp_alpha_image)

            for node in to_be_deleted_nodes:
                node_tree.nodes.remove(node)
            
            # Restore surface socket
            if from_socket:
                connect_sockets(surface_socket, from_socket)
            
        except Exception as e:
            logger.error(f"Error baking channel: {e}")
        finally:
            # 성공·실패·조기 return 모두 동일하게 복원. 값이 그대로면 불필요한 update 콜백을 피한다
            try:
                if self.use_alpha != orig_use_alpha:
                    self.use_alpha = orig_use_alpha
                if self.tangent_uv_map != orig_tangent_uv_map:
                    self.tangent_uv_map = orig_tangent_uv_map
                if self.output_vector_space != orig_output_vector_space:
                    self.output_vector_space = orig_output_vector_space
                if self.disable_output_transform != orig_disable_output_transform:
                    self.disable_output_transform = orig_disable_output_transform
                if orig_preview_channel:
                    self.isolate_channel(context)
            except Exception as e:
                logger.error(f"Error restoring channel settings: {e}")
            # Restore deform modifiers
            for obj, mod_name, orig_show_render in saved_modifier_states:
                if mod_name in obj.modifiers:
                    obj.modifiers[mod_name].show_render = orig_show_render

    @property
    def item_type(self):
        return Layer
    
    @property
    def collection_name(self):
        return "layers"
            
    name: StringProperty(
        name="Name",
        description="Channel name",
        default="Channel",
        update=update_channel_name
    )
    updating_name_flag: BoolProperty(
        default=False,
        options={'SKIP_SAVE'}  # Don't save this flag in the .blend file
    )
    node_tree: PointerProperty(
        name="Node Tree",
        type=NodeTree
    )
    layers: CollectionProperty(
        type=Layer,
        name="Material Layers",
        description="Collection of material layers in the Paint System"
    )
    
    @property
    def flattened_layers(self):
        return [layer.get_layer_data() for layer, _ in self.flatten_hierarchy()]

    @property
    def flattened_unlinked_layers(self):
        return [layer for layer, _ in self.flatten_hierarchy()]
    
    active_index: IntProperty(name="Active Material Layer Index", update=update_active_image)
    def update_type(self, context):
        self.update_node_tree(context)
        update_active_group(self, context)
    type: EnumProperty(
        items=CHANNEL_TYPE_ENUM,
        name="Channel Type",
        description="Type of the channel",
        default='COLOR',
        update=update_type
    )
    color_space: EnumProperty(
        items=COLOR_SPACE_ENUM,
        name="Color Space",
        description="Color space",
        default='COLOR'
    )
    use_alpha: BoolProperty(
        name="Expose Alpha Socket",
        description="Expose alpha socket in the Paint System group",
        default=True,
        update=update_active_group
    )
    use_max_min: BoolProperty(
        name="Use Max Min",
        description="Use max min for the channel",
        default=False,
        update=update_active_group
    )
    factor_min: FloatProperty(
        name="Factor Min",
        description="Minimum factor value",
        default=0,
        update=update_active_group
    )
    factor_max: FloatProperty(
        name="Factor Max",
        description="Maximum factor value",
        default=1,
        update=update_active_group
    )
    normalize_input: BoolProperty(
        name="Normalize",
        description="Normalize the channel",
        default=False,
        update=update_node_tree
    )
    use_space_transform_input: BoolProperty(
        name="Use Space Transform Input",
        description="Use space transform for the channel",
        default=True,
        update=update_node_tree
    )
    use_space_transform_output: BoolProperty(
        name="Use Space Transform Output",
        description="Use space transform for the channel",
        default=False,
        update=update_node_tree
    )
    def update_default_value(self, context):
        update_active_group(self, context)
        self.update_node_tree(context)
    default_value: EnumProperty(
        items=[
            ('NONE', "None", "None"),
            ('NORMAL', "Normal", "Normal"),
            ('WORLD_POSITION', "World Position", "World Position"),
            ('OBJECT_POSITION', "Object Position", "Object Position"),
        ],
        name="Default Value",
        description="Default value of the channel",
        default='NONE',
        update=update_default_value
    )
    vector_type: EnumProperty(
        items=[
            ('POINT', "Point", "Point"),
            ('VECTOR', "Vector", "Vector"),
            ('NORMAL', "Normal", "Normal"),
        ],
        name="Vector Type",
        description="Type of the vector",
        default='VECTOR',
        update=update_node_tree
    )
    input_vector_space: EnumProperty(
        items=[
            ('WORLD', "World", "World Space", "WORLD", 0),
            ('OBJECT', "Object", "Object Space", "OBJECT_DATA", 1),
        ],
        name="Input Vector Space",
        description="Space of the input",
        default='WORLD',
        update=update_node_tree
    )
    vector_space: EnumProperty(
        items=[
            ('WORLD', "World", "World Space", "WORLD", 0),
            ('OBJECT', "Object", "Object Space", "OBJECT_DATA", 1),
            ('TANGENT', "Tangent", "Tangent Space", "MESH_DATA", 2)
        ],
        name="Vector Space",
        description="Space used when painting",
        default='OBJECT',
        update=update_node_tree
    )
    output_vector_space: EnumProperty(
        items=[
            ('WORLD', "World", "World Space", "WORLD", 0),
            ('OBJECT', "Object", "Object Space", "OBJECT_DATA", 1),
            ('TANGENT', "Tangent", "Tangent Space", "MESH_DATA", 2)
        ],
        name="Output Vector Space",
        description="Space of the output vector",
        default='WORLD',
        update=update_node_tree
    )
    tangent_uv_map: StringProperty(
        name="Tangent UV Map",
        default="UVMap",
        update=update_node_tree
    )
    # Used when isolating the channel
    disable_output_transform: BoolProperty(
        name="Disable Output Transform",
        description="Disable the output transform for the channel",
        default=True, # For legacy reasons, the default is True
        update=update_node_tree
    )
    
    # Used when deleting group
    bake_channel: BoolProperty(
        name="Bake Channel",
        description="Bake the channel",
        default=False,
        options={'SKIP_SAVE'}
    )
    
    def update_bake_image(self, context):
        if self.use_bake_image:
            # Force to object mode
            bpy.ops.object.mode_set(mode="OBJECT")
        self.update_node_tree(context)
    # Bake settings
    bake_image: PointerProperty(
        name="Bake Image",
        type=Image,
        update=update_bake_image
    )
    bake_uv_map: StringProperty(
        name="Bake Image UV Map",
        default="UVMap",
        update=update_bake_image
    )
    use_bake_image: BoolProperty(
        name="Use Bake Image",
        default=False,
        update=update_bake_image
    )
    bake_vector_space: EnumProperty(
        items=[
            ('WORLD', "World Space", "World Space"),
            ('OBJECT', "Object Space", "Object Space"),
            ('TANGENT', "Tangent Space", "Tangent Space")
        ],
        name="Bake Vector Space",
        description="Space of the vector",
        default='OBJECT',
        update=update_bake_image
    )
    
    def get_movement_menu_items(self, item_id, direction):
        """
        Get menu items for movement options.
        Returns list of tuples (identifier, label, description)
        """
        options = self.get_movement_options(item_id, direction)
        menu_items = []

        # Map option identifiers to their operators
        operator_map = {
            'UP': 'paint_system.move_up',
            'DOWN': 'paint_system.move_down'
        }

        for identifier, description in options:
            menu_items.append((
                operator_map[direction],
                description,
                {'action': identifier}
            ))

        return menu_items
    
    def isolate_channel(self, context):
        ps_ctx = parse_context(context)
        active_group = ps_ctx.active_group
        active_channel = ps_ctx.active_channel
        ps_mat_data = ps_ctx.ps_mat_data
        mat = ps_ctx.active_material
        mat_output = get_material_output(mat.node_tree)
        if not ps_mat_data.preview_channel:
            ps_mat_data.preview_channel = True
            # Store the node connected to material output
            connected_link = mat_output.inputs[0].links[0]
            ps_ctx.ps_mat_data.original_node_name = connected_link.from_node.name
            ps_ctx.ps_mat_data.original_socket_name = connected_link.from_socket.name
            ps_ctx.ps_mat_data.original_view_transform = str(context.scene.view_settings.view_transform) # bpy.data.scenes["Scene"].view_settings.view_transform
            
            # Find channel node tree
            node = find_node(mat.node_tree, {'bl_idname': 'ShaderNodeGroup', 'node_tree': active_group.node_tree})
            if node:
                # Connect node tree to material output
                connect_sockets(mat_output.inputs[0], node.outputs[active_channel.name])
            
            context.scene.view_settings.view_transform = "Standard"
            active_channel.disable_output_transform = True
        else:
            ps_mat_data.preview_channel = False
            # Find node by name
            node = mat.node_tree.nodes.get(ps_mat_data.original_node_name)
            if node:
                connect_sockets(node.outputs[ps_mat_data.original_socket_name], mat_output.inputs[0])
            
            context.scene.view_settings.view_transform = ps_ctx.ps_mat_data.original_view_transform
            active_channel.disable_output_transform = False
