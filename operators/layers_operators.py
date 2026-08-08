import sys

import bpy
from bpy.props import (
    StringProperty, IntProperty, EnumProperty,
    BoolProperty
)
from bpy.types import Operator, Context, NodeTree
from bpy.utils import register_classes_factory
import mathutils

from ..paintsystem.data import (
    ACTION_BIND_ENUM,
    ACTION_TYPE_ENUM,
    ADJUSTMENT_TYPE_ENUM,
    ATTRIBUTE_TYPE_ENUM,
    TEXTURE_TYPE_ENUM,
    GRADIENT_TYPE_ENUM,
    GEOMETRY_TYPE_ENUM,
    add_empty_to_collection,
    get_layer_by_uid,
)
from ..paintsystem.image import save_image
from ..utils import get_next_unique_name
from ..utils.nodes import get_nodetree_socket_enum
from ..utils.registration import collect_classes
from .common import (
    PSContextMixin,
    scale_content,
    get_icon_from_socket_type,
    MultiMaterialOperator,
    PSUVOptionsMixin,
    PSImageCreateMixin,
    redraw_panel,
    intern_enum_items,
    label_for,
    )

def get_object_uv_maps(self, context: Context):
    items = [
        (uv_map.name, uv_map.name, "") for uv_map in context.object.data.uv_layers
    ]
    return intern_enum_items(items)

def ps_not_initialized(context: Context):
    ps_ctx = PSContextMixin.parse_context(context)
    return not (hasattr(context.active_object.active_material, "ps_mat_data") and ps_ctx.active_group and ps_ctx.active_channel)

class PSActiveChannelPoll:
    """활성 채널이 있어야 실행 가능한 오퍼레이터 공용 poll."""

    @classmethod
    def poll(cls, context):
        ps_ctx = cls.parse_context(context)
        return ps_ctx.active_channel is not None


class PSActiveLayerPoll:
    """활성 레이어가 있어야 실행 가능한 오퍼레이터 공용 poll."""

    @classmethod
    def poll(cls, context):
        return cls.parse_context(context).active_layer is not None


class PSNewLayerBase(PSActiveChannelPoll, PSContextMixin, MultiMaterialOperator):
    """레이어 생성 오퍼레이터 공용 베이스.

    서브클래스는 `layer_type`만 지정하면 되고, 이름·추가 인자가 필요하면
    `get_layer_name` / `get_layer_kwargs`를, 생성 전후 처리가 필요하면
    `before_create` / `after_create`를 오버라이드한다.
    """

    # 베이스 자신은 등록 대상이 아니다
    _ps_skip_register = True

    # create_layer에 넘길 레이어 타입 문자열
    layer_type = ""

    def get_layer_name(self, context):
        return self.layer_name

    def get_layer_kwargs(self, context):
        return {}

    def before_create(self, context):
        pass

    def after_create(self, context, layer):
        pass

    def process_material(self, context):
        if ps_not_initialized(context):
            return {'CANCELLED'}
        self.before_create(context)
        ps_ctx = self.parse_context(context)
        layer = ps_ctx.active_channel.create_layer(
            context,
            self.get_layer_name(context),
            self.layer_type,
            **self.get_layer_kwargs(context),
        )
        self.after_create(context, layer)
        return {'FINISHED'}


class PSNewMaskBase(PSActiveLayerPoll, PSContextMixin, Operator):
    """마스크 생성 오퍼레이터 공용 베이스. 서브클래스는 `mask_type`만 지정한다."""

    # 베이스 자신은 등록 대상이 아니다
    _ps_skip_register = True

    mask_type = ""

    def execute(self, context):
        ps_ctx = self.parse_context(context)
        ps_ctx.active_layer.create_mask(self.mask_type)
        return {'FINISHED'}


class PAINTSYSTEM_OT_NewImage(PSActiveChannelPoll, PSContextMixin, PSImageCreateMixin, MultiMaterialOperator):
    """Create a new image layer"""
    bl_idname = "paint_system.new_image_layer"
    bl_label = "New Image Layer"
    bl_options = {'REGISTER', 'UNDO'}

    image_name: StringProperty(
        name="Layer Name",
        description="Name of the new image layer",
        default="Image"
    )
    
    image_add_type: EnumProperty(
        name="Image Add Type",
        description="How to add the image layer",
        items=[
            ('NEW', "New Image", "Create a new image layer"),
            ('IMPORT', "Import Image", "Import an image from file"),
            ('EXISTING', "Existing Image", "Use an existing image from the blend file"),
        ],
        default='NEW'
    )
    filepath: StringProperty(
        subtype='FILE_PATH',
    )
    filter_glob: StringProperty(
        default='*.jpg;*.jpeg;*.png;*.tif;*.tiff;*.bmp',
        options={'HIDDEN'}
    )
    # 3DPainter 포크: 다이얼로그 없이 기본값으로 즉시 생성 (원클릭 드로잉 레이어)
    skip_dialog: BoolProperty(
        name="Skip Dialog",
        description="Create the layer immediately with default settings",
        default=False,
        options={'SKIP_SAVE'},
    )
            
    def get_next_image_name(self, context):
        """Get the next image name from the active channel"""
        ps_ctx = self.parse_context(context)
        if ps_ctx.active_channel:
            return get_next_unique_name("Image", [layer.name for layer in ps_ctx.active_channel.layers])

    def process_material(self, context):
        if ps_not_initialized(context):
            return {'CANCELLED'}
        self.store_coord_type(context)
        ps_ctx = self.parse_context(context)
        if self.image_add_type == 'NEW':
            img = self.create_image(context)
        elif self.image_add_type == 'IMPORT':
            img = bpy.data.images.load(self.filepath, check_existing=True)
            if not img:
                self.report({'ERROR'}, "Failed to load image")
                return False
            self.image_name = img.name
        elif self.image_add_type == 'EXISTING':
            if not self.image_name:
                self.report({'ERROR'}, "No image selected")
                return False
            img = bpy.data.images.get(self.image_name)
            save_image(img)
            if not img:
                self.report({'ERROR'}, "Image not found")
                return False
        ps_ctx.active_channel.create_layer(
            context, 
            layer_name=self.image_name, 
            layer_type="IMAGE", 
            image=img,
            coord_type=self.coord_type,
            uv_map_name=self.uv_map_name
        )
        return {'FINISHED'}
    
    def invoke(self, context, event):
        self.get_coord_type(context)
        self.image_name = self.get_next_image_name(context)
        if self.image_resolution != 'CUSTOM':
            self.image_width = int(self.image_resolution)
            self.image_height = int(self.image_resolution)
        if self.image_add_type == 'IMPORT':
            context.window_manager.fileselect_add(self)
            return {'RUNNING_MODAL'}
        if self.image_add_type == 'EXISTING':
            self.image_name = ""
        # 3DPainter 포크: 원클릭 생성 경로 — invoke에서 이름/해상도가 이미 채워져 있음
        if self.image_add_type == 'NEW' and self.skip_dialog:
            # Solid+Texture 셰이딩이면 새 빈 레이어만 보여 혼란 → 합성 표시로 교정
            from .view2d_operators import ensure_composite_shading
            ensure_composite_shading(context)
            return self.execute(context)
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        self.multiple_objects_ui(layout, context)
        if self.image_add_type == 'NEW':
            self.image_create_ui(layout, context)
        elif self.image_add_type == 'EXISTING':
            layout.prop_search(self, "image_name", bpy.data,
                           "images", text="Image")
            
        box = layout.box()
        self.select_coord_type_ui(box, context)


class PAINTSYSTEM_OT_NewFolder(PSNewLayerBase):
    """Create a new folder layer"""
    bl_idname = "paint_system.new_folder_layer"
    bl_label = "New Folder"
    bl_options = {'REGISTER', 'UNDO'}
    layer_type = "FOLDER"

    layer_name: StringProperty(
        name="Layer Name",
        description="Name of the new folder",
        default="Folder"
    )


class PAINTSYSTEM_OT_NewSolidColor(PSNewLayerBase):
    """Create a new solid color layer"""
    bl_idname = "paint_system.new_solid_color_layer"
    bl_label = "New Solid Color Layer"
    bl_options = {'REGISTER', 'UNDO'}
    layer_type = "SOLID_COLOR"

    layer_name: StringProperty(
        name="Layer Name",
        description="Name of the new solid color layer",
        default="Solid Color"
    )


class PAINTSYSTEM_OT_NewAttribute(PSNewLayerBase):
    """Create a new attribute layer"""
    bl_idname = "paint_system.new_attribute_layer"
    bl_label = "New Attribute Layer"
    bl_options = {'REGISTER', 'UNDO'}
    layer_type = "ATTRIBUTE"

    attribute_name: StringProperty(
        name="Attribute Name",
        description="Name of the attribute to use"
    )
    attribute_type: EnumProperty(
        name="Attribute Type",
        items=ATTRIBUTE_TYPE_ENUM,
        default='GEOMETRY'
    )

    layer_name: StringProperty(
        name="Layer Name",
        description="Name of the new attribute layer",
        default="Attribute"
    )


class PAINTSYSTEM_OT_NewAdjustment(PSNewLayerBase):
    """Create a new adjustment layer"""
    bl_idname = "paint_system.new_adjustment_layer"
    bl_label = "New Adjustment Layer"
    bl_options = {'REGISTER', 'UNDO'}
    layer_type = "ADJUSTMENT"

    adjustment_type: EnumProperty(
        name="Adjustment Type",
        items=ADJUSTMENT_TYPE_ENUM,
    )

    def get_layer_name(self, context):
        return label_for(ADJUSTMENT_TYPE_ENUM, self.adjustment_type)

    def get_layer_kwargs(self, context):
        return {"adjustment_type": self.adjustment_type}


class PAINTSYSTEM_OT_NewShader(PSNewLayerBase):
    """Create a new shader layer"""
    bl_idname = "paint_system.new_shader_layer"
    bl_label = "New Shader Layer"
    bl_options = {'REGISTER', 'UNDO'}
    layer_type = "SHADER"

    layer_name: StringProperty(
        name="Layer Name",
        description="Name of the new shader layer",
        default="Shader"
    )


class PAINTSYSTEM_OT_NewGradient(PSNewLayerBase):
    """Create a new gradient layer"""
    bl_idname = "paint_system.new_gradient_layer"
    bl_label = "New Gradient Layer"
    bl_options = {'REGISTER', 'UNDO'}
    layer_type = "GRADIENT"

    layer_name: StringProperty(
        name="Layer Name",
        description="Name of the new gradient layer",
        default="Gradient"
    )

    gradient_type: EnumProperty(
        name="Gradient Type",
        items=GRADIENT_TYPE_ENUM,
        default='LINEAR'
    )

    def get_layer_name(self, context):
        return "Fake Light" if self.gradient_type == 'FAKE_LIGHT' else self.gradient_type.title()

    def get_layer_kwargs(self, context):
        return {"gradient_type": self.gradient_type}

    def after_create(self, context, layer):
        if self.gradient_type == 'FAKE_LIGHT':
            layer.blend_mode = "MULTIPLY"


class PAINTSYSTEM_OT_NewGeometry(PSNewLayerBase):
    """Create a new geometry layer"""
    bl_idname = "paint_system.new_geometry_layer"
    bl_label = "New Geometry Layer"
    bl_options = {'REGISTER', 'UNDO'}
    layer_type = "GEOMETRY"

    geometry_type: EnumProperty(
        name="Geometry Type",
        items=GEOMETRY_TYPE_ENUM,
        default='WORLD_NORMAL'
    )

    def get_layer_name(self, context):
        return label_for(GEOMETRY_TYPE_ENUM, self.geometry_type)

    def get_layer_kwargs(self, context):
        active_channel = self.parse_context(context).active_channel
        return {
            "geometry_type": self.geometry_type,
            "normalize_normal": active_channel.normalize_input if active_channel.type == 'VECTOR' else False,
        }


class PAINTSYSTEM_OT_FixMissingGradientEmpty(PSContextMixin, Operator):
    """Fix missing gradient empty"""
    bl_idname = "paint_system.fix_missing_gradient_empty"
    bl_label = "Fix Missing Gradient Empty"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        ps_ctx = self.parse_context(context)
        for layer in ps_ctx.active_channel.layers:
            if layer.type == 'GRADIENT':
                layer.update_node_tree(context)
        ps_ctx.active_layer.update_node_tree(context)
        return {'FINISHED'}


class PAINTSYSTEM_OT_SelectEmpty(PSContextMixin, Operator):
    """Select the empty"""
    bl_idname = "paint_system.select_empty"
    bl_label = "Select Empty"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        ps_ctx = self.parse_context(context)
        empty_object = ps_ctx.active_layer.empty_object
        if empty_object and empty_object.name not in context.view_layer.objects:
            add_empty_to_collection(context, empty_object)
        if empty_object:
            bpy.ops.object.mode_set(mode='OBJECT')
            bpy.ops.object.select_all(action='DESELECT')
            bpy.context.view_layer.objects.active = empty_object
            empty_object.select_set(True)
        return {'FINISHED'}


class PAINTSYSTEM_OT_NewRandomColor(PSNewLayerBase):
    """Create a new random color layer"""
    bl_idname = "paint_system.new_random_color_layer"
    bl_label = "New Random Color Layer"
    bl_options = {'REGISTER', 'UNDO'}
    layer_type = "RANDOM"

    layer_name: StringProperty(
        name="Layer Name",
        description="Name of the new random color layer",
        default="Random Color"
    )


class PAINTSYSTEM_OT_NewCustomNodeGroup(PSActiveChannelPoll, PSContextMixin, MultiMaterialOperator):
    """Create a new custom node group layer"""
    bl_idname = "paint_system.new_custom_node_group_layer"
    bl_label = "New Custom Node Group Layer"
    bl_options = {'REGISTER', 'UNDO'}
    
    def get_node_groups(self, context: Context):
        node_groups = []
        for node_group in bpy.data.node_groups:
            if node_group.bl_idname == 'ShaderNodeTree' and not node_group.name.startswith(".PS") and not node_group.name.startswith("Paint System") and not node_group.name.startswith("PS "):
                node_groups.append((node_group.name, node_group.name, ""))
        return node_groups
    
    def get_inputs_enum(self, context: Context):
        if not self.node_tree_name:
            return []
        custom_node_tree = bpy.data.node_groups.get(self.node_tree_name)
        inputs = get_nodetree_socket_enum(custom_node_tree, in_out='INPUT')
        inputs.append(('_NONE_', 'None', '', 'BLANK1', len(inputs)))
        return inputs
    
    def get_outputs_without_none(self, context: Context):
        if not self.node_tree_name:
            return []
        custom_node_tree = bpy.data.node_groups.get(self.node_tree_name)
        outputs = get_nodetree_socket_enum(custom_node_tree, in_out='OUTPUT')
        return outputs
    
    def get_outputs_enum(self, context: Context):
        if not self.node_tree_name:
            return []
        custom_node_tree = bpy.data.node_groups.get(self.node_tree_name)
        outputs = get_nodetree_socket_enum(custom_node_tree, in_out='OUTPUT')
        outputs.append(('_NONE_', 'None', '', 'BLANK1', len(outputs)))
        return outputs
    
    def auto_select_sockets(self, context: Context):
        if not self.node_tree_name:
            return
        custom_node_tree = bpy.data.node_groups.get(self.node_tree_name)
        input_sockets = get_nodetree_socket_enum(custom_node_tree, in_out='INPUT')
        output_sockets = get_nodetree_socket_enum(custom_node_tree, in_out='OUTPUT')
        found_color_input = False
        found_alpha_input = False
        # found_color_output = False
        found_alpha_output = False
        for input_socket in input_sockets:
            if input_socket[1] == 'Color':
                self.color_input_name = input_socket[0]
                found_color_input = True
            elif input_socket[1] == 'Alpha':
                self.alpha_input_name = input_socket[0]
                found_alpha_input = True
        for output_socket in output_sockets:
            # if output_socket[1] == 'Color':
            #     self.color_output_name = output_socket[0]
            #     found_color_output = True
            if output_socket[1] == 'Alpha':
                self.alpha_output_name = output_socket[0]
                found_alpha_output = True
        if not found_color_input:
            self.color_input_name = '_NONE_'
        if not found_alpha_input:
            self.alpha_input_name = '_NONE_'
        # Color should not have none output
        # if not found_color_output:
        #     self.color_output_name = '_NONE_'
        if not found_alpha_output:
            self.alpha_output_name = '_NONE_'
    def has_unsupported_sockets(self, node_tree: NodeTree):
        for socket in node_tree.interface.items_tree:
            if socket.item_type == 'SOCKET' and socket.socket_type not in ['NodeSocketColor', 'NodeSocketFloat', 'NodeSocketVector']:
                return True
        return False
    
    node_tree_name: EnumProperty(
        name="Node Tree",
        description="Name of the node tree to use",
        items=get_node_groups,
        update=auto_select_sockets
    )
    
    color_input_name: EnumProperty(
        name="Custom Color Input",
        description="Custom color input",
        items=get_inputs_enum,
    )
    alpha_input_name: EnumProperty(
        name="Custom Alpha Input",
        description="Custom alpha input",
        items=get_inputs_enum,
        
    )
    color_output_name: EnumProperty(
        name="Custom Color Output",
        description="Custom color output",
        items=get_outputs_without_none
    )
    alpha_output_name: EnumProperty(
        name="Custom Alpha Output",
        description="Custom alpha output",
        items=get_outputs_enum
    )

    def process_material(self, context):
        if ps_not_initialized(context):
            return {'CANCELLED'}
        if not self.node_tree_name:
            return {'CANCELLED'}
        # Must have at least one output socket
        if (not self.color_output_name or self.color_output_name == '_NONE_') and (not self.alpha_output_name or self.alpha_output_name == '_NONE_'):
            self.report({'ERROR'}, "Node tree must have at least one output socket")
            return {'CANCELLED'}
        ps_ctx = self.parse_context(context)
        custom_node_tree = bpy.data.node_groups.get(self.node_tree_name)
        ps_ctx.active_channel.create_layer(
            context,
            layer_name=self.node_tree_name,
            layer_type="NODE_GROUP",
            custom_node_tree=custom_node_tree,
            color_input_name=self.color_input_name,
            alpha_input_name=self.alpha_input_name,
            color_output_name=self.color_output_name,
            alpha_output_name=self.alpha_output_name
        )
        return {'FINISHED'}
    
    def invoke(self, context, event):
        self.auto_select_sockets(context)
        return context.window_manager.invoke_props_dialog(self)
    
    def draw(self, context):
        layout = self.layout
        layout.label(text="Select node tree:", icon='NODETREE')
        available_node_trees = self.get_node_groups(context)
        if not available_node_trees:
            layout.label(text="No supported node trees found", icon='ERROR')
            return
        row = layout.row()
        scale_content(context, row, 1.5, 1.5)
        row.prop(self, "node_tree_name", text="")
        if self.has_unsupported_sockets(bpy.data.node_groups.get(self.node_tree_name)):
            box = layout.box()
            box.alert = True
            row = box.row()
            row.label(icon='ERROR')
            col = row.column()
            col.label(text="Node has unsupported sockets (Shader)")
        if self.node_tree_name:
            box = layout.box()
            row = box.row()
            row.alignment = 'CENTER'
            row.label(text="Socket Connection", icon='NODETREE')
            row = box.row()
            box = row.box()
            text_row = box.row()
            text_row.alignment = 'CENTER'
            text_row.label(text="Input")
            box.prop(self, "color_input_name", text="Color")
            box.prop(self, "alpha_input_name", text="Alpha")
            box = row.box()
            text_row = box.row()
            text_row.alignment = 'CENTER'
            text_row.label(text="Output")
            box.prop(self, "color_output_name", text="Color")
            box.prop(self, "alpha_output_name", text="Alpha")


class PAINTSYSTEM_OT_NewTexture(PSUVOptionsMixin, PSNewLayerBase):
    """Create a new texture layer"""
    bl_idname = "paint_system.new_texture_layer"
    bl_label = "New Texture Layer"
    bl_options = {'REGISTER', 'UNDO'}
    layer_type = "TEXTURE"

    texture_type: EnumProperty(
        name="Texture Type",
        description="Type of texture to create",
        items=TEXTURE_TYPE_ENUM,
    )

    def invoke(self, context, event):
        self.get_coord_type(context)
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        self.multiple_objects_ui(layout, context)
        box = layout.box()
        self.select_coord_type_ui(box, context, show_warning=False)

    def before_create(self, context):
        self.store_coord_type(context)

    def get_layer_name(self, context):
        return label_for(TEXTURE_TYPE_ENUM, self.texture_type)

    def get_layer_kwargs(self, context):
        return {
            "texture_type": self.texture_type,
            "coord_type": self.coord_type,
            "uv_map_name": self.uv_map_name,
        }


class PAINTSYSTEM_OT_DeleteItem(PSContextMixin, MultiMaterialOperator):
    """Remove the active item"""
    bl_idname = "paint_system.delete_item"
    bl_label = "Remove Item"
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Remove the active item"

    @classmethod
    def poll(cls, context):
        ps_ctx = cls.parse_context(context)
        return ps_ctx.unlinked_layer is not None

    def process_material(self, context):
        if ps_not_initialized(context):
            return {'CANCELLED'}
        ps_ctx = self.parse_context(context)
        active_channel = ps_ctx.active_channel
        unlinked_layer = ps_ctx.unlinked_layer
        active_channel.delete_layer(context, unlinked_layer)
        
        redraw_panel(context)
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        ps_ctx = self.parse_context(context)
        layout = self.layout
        unlinked_layer = ps_ctx.unlinked_layer
        layout.label(
            text=f"Delete '{unlinked_layer.name}' ?", icon='ERROR')
        layout.label(
            text="Click OK to delete, or cancel to keep the layer")


class PSMoveLayerBase(PSContextMixin, MultiMaterialOperator):
    """레이어 이동 오퍼레이터 공용 베이스. 서브클래스는 `direction`만 지정한다."""

    # 베이스 자신은 등록 대상이 아니다
    _ps_skip_register = True

    # 'UP' 또는 'DOWN'
    direction = 'UP'

    @classmethod
    def get_active_item(cls, context):
        """(활성 채널, 활성 항목 id)를 돌려준다. 채널이 없으면 (None, None)."""
        active_channel = cls.parse_context(context).active_channel
        if not active_channel:
            return None, None
        return active_channel, active_channel.get_id_from_flattened_index(active_channel.active_index)

    @classmethod
    def poll(cls, context):
        active_channel, item_id = cls.get_active_item(context)
        if not active_channel:
            return False
        return bool(active_channel.get_movement_options(item_id, cls.direction))

    def invoke(self, context, event):
        active_channel, item_id = self.get_active_item(context)
        if not active_channel:
            return {'CANCELLED'}

        options = active_channel.get_movement_options(item_id, self.direction)
        if not options:
            return {'CANCELLED'}

        if len(options) == 1 and options[0][0] == 'SKIP':
            self.action = 'SKIP'
            return self.process_material(context)

        context.window_manager.popup_menu(
            self.draw_menu,
            title="Move Options"
        )
        return {'FINISHED'}

    def draw_menu(self, self_menu, context):
        active_channel, item_id = self.get_active_item(context)
        if not active_channel:
            return {'CANCELLED'}

        for op_id, label, props in active_channel.get_movement_menu_items(item_id, self.direction):
            op = self_menu.layout.operator(op_id, text=label)
            for key, value in props.items():
                setattr(op, key, value)

    def process_material(self, context):
        if ps_not_initialized(context):
            return {'CANCELLED'}
        active_channel, item_id = self.get_active_item(context)
        if not active_channel:
            return {'CANCELLED'}

        if active_channel.execute_movement(item_id, self.direction, self.action):
            active_channel.update_node_tree(context)
            redraw_panel(context)
            return {'FINISHED'}

        return {'CANCELLED'}


class PAINTSYSTEM_OT_MoveUp(PSMoveLayerBase):
    """Move the active item up"""
    bl_idname = "paint_system.move_up"
    bl_label = "Move Item Up"
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Move the active item up"
    direction = 'UP'

    action: EnumProperty(
        items=[
            ('MOVE_INTO', "Move Into", "Move into folder"),
            ('MOVE_ADJACENT', "Move Adjacent", "Move as sibling"),
            ('MOVE_OUT', "Move Out", "Move out of folder"),
            ('SKIP', "Skip", "Skip over item"),
        ]
    )


class PAINTSYSTEM_OT_MoveDown(PSMoveLayerBase):
    """Move the active item down"""
    bl_idname = "paint_system.move_down"
    bl_label = "Move Item Down"
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Move the active item down"
    direction = 'DOWN'

    action: EnumProperty(
        items=[
            ('MOVE_OUT_BOTTOM', "Move Out Bottom", "Move out of folder"),
            ('MOVE_INTO_TOP', "Move Into Top", "Move to top of folder"),
            ('MOVE_ADJACENT', "Move Adjacent", "Move as sibling"),
            ('SKIP', "Skip", "Skip over item"),
        ]
    )


class PAINTSYSTEM_OT_CopyLayer(PSActiveLayerPoll, PSContextMixin, Operator):
    """Copy the active layer"""
    bl_idname = "paint_system.copy_layer"
    bl_label = "Copy Layer"
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Copy the active layer"

    def execute(self, context):
        ps_ctx = self.parse_context(context)
        unlinked_layer = ps_ctx.unlinked_layer
        ps_scene_data = ps_ctx.ps_scene_data
        if not ps_scene_data:
            return {'CANCELLED'}
        ps_scene_data.clear_clipboard()
        ps_scene_data.add_layer_to_clipboard(unlinked_layer)
        return {'FINISHED'}


class PAINTSYSTEM_OT_CopyAllLayers(PSActiveChannelPoll, PSContextMixin, Operator):
    """Copy all layers"""
    bl_idname = "paint_system.copy_all_layers"
    bl_label = "Copy All Layers"
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Copy all layers"

    def execute(self, context):
        ps_ctx = self.parse_context(context)
        active_channel = ps_ctx.active_channel
        ps_scene_data = ps_ctx.ps_scene_data
        if not ps_scene_data:
            return {'CANCELLED'}
        ps_scene_data.clear_clipboard()
        for layer in active_channel.flattened_unlinked_layers:
            ps_scene_data.add_layer_to_clipboard(layer)
        return {'FINISHED'}


class PAINTSYSTEM_OT_PasteLayer(PSContextMixin, Operator):
    """Paste the copied layer"""
    bl_idname = "paint_system.paste_layer"
    bl_label = "Paste Layer"
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Paste the copied layer"
    
    linked: BoolProperty(
        name="Linked",
        description="Paste the copied layer as linked",
        default=False,
        options={'SKIP_SAVE'}
    )
    
    @classmethod
    def poll(cls, context):
        return len(bpy.context.scene.ps_scene_data.clipboard_layers) > 0
    
    def execute(self, context):
        ps_ctx = self.parse_context(context)
        active_layer = ps_ctx.active_layer
        clipboard_layers = bpy.context.scene.ps_scene_data.clipboard_layers
        new_layer_id_map = {}
        if active_layer:
            is_folder = active_layer and active_layer.type == "FOLDER"
            base_parent_id = active_layer.id if is_folder else active_layer.parent_id
        else:
            base_parent_id = -1
        for idx, clipboard_layer in enumerate(clipboard_layers):
            layer = get_layer_by_uid(clipboard_layer.material, clipboard_layer.uid)
            if not layer:
                continue
            if self.linked:
                new_layer = ps_ctx.active_channel.create_layer(context, layer.layer_name, "BLANK" if layer.type != "FOLDER" else "FOLDER", insert_at="CURSOR" if idx == 0 else "AFTER", linked_layer_uid=clipboard_layer.uid, linked_material=clipboard_layer.material)
            else:
                new_layer = ps_ctx.active_channel.create_layer(context, layer.layer_name, layer.type, insert_at="CURSOR" if idx == 0 else "AFTER")
                new_layer.copy_layer_data(layer)
            new_layer_id_map[layer.id] = new_layer
            if layer.parent_id != -1:
                new_layer.parent_id = new_layer_id_map[layer.parent_id].id
            else:
                new_layer.parent_id = base_parent_id
            new_layer.update_node_tree(context)
        ps_ctx.active_channel.update_node_tree(context)
        
        return {'FINISHED'}


class PAINTSYSTEM_OT_UnlinkLayer(PSActiveLayerPoll, PSContextMixin, Operator):
    """Unlink the active layer"""
    bl_idname = "paint_system.unlink_layer"
    bl_label = "Unlink Layer"
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Unlink the active layer"

    def execute(self, context):
        ps_ctx = self.parse_context(context)
        unlinked_layer = ps_ctx.unlinked_layer
        unlinked_layer.unlink_layer_data()
        ps_ctx.active_channel.update_node_tree(context)
        return {'FINISHED'}


class PAINTSYSTEM_OT_AddAction(PSActiveLayerPoll, PSContextMixin, Operator):
    """Add an action to the active layer"""
    bl_idname = "paint_system.add_action"
    bl_label = "Add Action"
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Add an action to the active layer"
    
    action_bind: EnumProperty(
        name="Action Type",
        description="Action type",
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
    
    def get_next_action_name(self, context):
        ps_ctx = self.parse_context(context)
        active_layer = ps_ctx.active_layer
        return get_next_unique_name("Action", [action.name for action in active_layer.actions])

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, "action_bind", text="Bind to")
        layout.prop(self, "action_type", text="Once reached")
        if self.action_bind == 'FRAME':
            layout.prop(self, "frame", text="Frame")
        elif self.action_bind == 'MARKER':
            layout.prop_search(self, "marker_name", context.scene, "timeline_markers", text="Marker", icon="MARKER_HLT")
    
    def execute(self, context):
        ps_ctx = self.parse_context(context)
        active_layer = ps_ctx.active_layer
        active_layer.add_action(self.get_next_action_name(context), self.action_bind, self.action_type, self.frame, self.marker_name)
        redraw_panel(context)
        return {'FINISHED'}
    
    def invoke(self, context, event):
        # Get current frame
        self.frame = bpy.context.scene.frame_current
        return context.window_manager.invoke_props_dialog(self)


class PAINTSYSTEM_OT_DeleteAction(PSActiveLayerPoll, PSContextMixin, Operator):
    """Delete the active action"""
    bl_idname = "paint_system.delete_action"
    bl_label = "Delete Action"
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Delete the active action"

    def execute(self, context):
        ps_ctx = self.parse_context(context)
        ps_ctx.active_layer.remove_active_action()
        return {'FINISHED'}


class PAINTSYSTEM_OT_ShowLayerWarnings(PSContextMixin, Operator):
    """Show layer warnings"""
    bl_idname = "paint_system.show_layer_warnings"
    bl_label = "Layer Warnings"
    bl_options = {'REGISTER'}
    bl_description = "Show layer warnings"
    
    layer_id: IntProperty(
        name="Warnings",
        description="Layer ID to display warnings for",
        default=-1
    )
    
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=260)
    
    def draw(self, context):
        layout = self.layout
        ps_ctx = self.parse_context(context)
        active_channel = ps_ctx.active_channel
        layer = active_channel.get_item_by_id(self.layer_id)
        warnings = layer.get_layer_data().get_layer_warnings(context)
        warnings_box = layout.box()
        warnings_col = warnings_box.column(align=True)
        for warning in warnings:
            # Split warning into chunks of 6 words
            words = warning.split()
            chunks = [' '.join(words[j:j+6]) for j in range(0, len(words), 6)]
            for i, chunk in enumerate(chunks):
                warnings_col.label(text=chunk, icon='ERROR' if not i else 'BLANK1')
    
    def execute(self, context):
        return {'FINISHED'}


class PAINTSYSTEM_OT_SetProjectionView(PSContextMixin, Operator):
    """Set the projection view"""
    bl_idname = "paint_system.set_projection_view"
    bl_label = "Set Projection View"
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Set the projection view"
    
    @classmethod
    def poll(cls, context):
        ps_ctx = cls.parse_context(context)
        return ps_ctx.active_layer is not None and ps_ctx.active_layer.coord_type == 'PROJECT'
    
    def execute(self, context):
        ps_ctx = self.parse_context(context)
        ps_ctx.active_layer.set_projection_view(context)
        return {'FINISHED'}
    
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)
    
    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.alert = True
        col = box.column(align=True)
        col.label(text="Override the projection view?", icon='ERROR')
        col.label(text="Projection will be overridden with the current view", icon='BLANK1')


class PAINTSYSTEM_OT_ProjectionViewReset(PSContextMixin, Operator):
    """Reset the projection to the stored view"""
    bl_idname = "paint_system.projection_view_reset"
    bl_label = "Projection View Reset"
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Reset the projection to the stored view"
    
    @classmethod
    def poll(cls, context):
        ps_ctx = cls.parse_context(context)
        
        return ps_ctx.active_layer is not None and ps_ctx.active_layer.coord_type == 'PROJECT'
    
    def execute(self, context):
        ps_ctx = self.parse_context(context)
        active_layer = ps_ctx.active_layer
        active_space = context.area.spaces.active
        if active_space.type == 'VIEW_3D':
            region_3d = active_space.region_3d
            if region_3d:
                location = mathutils.Vector(active_layer.projection_position)
                rotation = mathutils.Euler(active_layer.projection_rotation, 'XYZ')
                scale = mathutils.Vector((1.0, 1.0, 1.0))
                match region_3d.view_perspective:
                    case 'PERSP':
                        view_matrix = mathutils.Matrix.LocRotScale(location, rotation, scale)
                        if active_layer.projection_space == "OBJECT":
                            view_matrix = ps_ctx.ps_object.matrix_world @ view_matrix
                        view_matrix.invert()
                        region_3d.view_matrix = view_matrix
                    case "CAMERA":
                        # Set active camera position and rotation 
                        active_camera = bpy.context.scene.camera
                        active_camera.location = location
                        active_camera.rotation_euler = rotation
                    case _:
                        self.report({'WARNING'}, "This view perspective is not supported")
                        return {'CANCELLED'}
                return {'FINISHED'}
        return {'FINISHED'}


# Masks
class PAINTSYSTEM_OT_NewValueMask(PSNewMaskBase):
    """Create a new value mask"""
    bl_idname = "paint_system.new_value_mask"
    bl_label = "New Value Mask"
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Create a new value mask"
    mask_type = "VALUE"


class PAINTSYSTEM_OT_NewImageMask(PSNewMaskBase):
    """Create a new image mask"""
    bl_idname = "paint_system.new_image_mask"
    bl_label = "New Image Mask"
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Create a new image mask"
    mask_type = "IMAGE"


class PAINTSYSTEM_OT_NewAttributeMask(PSNewMaskBase):
    """Create a new attribute mask"""
    bl_idname = "paint_system.new_attribute_mask"
    bl_label = "New Attribute Mask"
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Create a new attribute mask"
    mask_type = "ATTRIBUTE"


class PAINTSYSTEM_OT_NewTextureMask(PSNewMaskBase):
    """Create a new texture mask"""
    bl_idname = "paint_system.new_texture_mask"
    bl_label = "New Texture Mask"
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Create a new texture mask"
    mask_type = "TEXTURE"


classes = collect_classes(sys.modules[__name__])

register, unregister = register_classes_factory(classes)