"""레거시 Paint System 데이터 레이아웃 (.blend 호환용)."""

from typing import Optional

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import (
    Image,
    Material,
    Node,
    NodeTree,
    PropertyGroup,
)


# ---- Legacy / backward-compatibility classes ----
# These classes exist solely to allow reading older .blend files that still
# contain the old data layout. They are registered so Blender can deserialize
# the data, but they are NOT used for any new functionality.

class LegacyPaintSystemLayer(PropertyGroup):
    """DEPRECATED -- Old-format layer data, kept for .blend file compatibility."""

    name: StringProperty(
        name="Name",
        description="Layer name",
        default="Layer",
    )
    enabled: BoolProperty(
        name="Enabled",
        description="Toggle layer visibility",
        default=True,
    )
    image: PointerProperty(
        name="Image",
        type=Image
    )
    type: EnumProperty(
        items=[
        ('FOLDER', "Folder", "Folder layer"),
        ('IMAGE', "Image", "Image layer"),
        ('SOLID_COLOR', "Solid Color", "Solid Color layer"),
        ('ATTRIBUTE', "Attribute", "Attribute layer"),
        ('ADJUSTMENT', "Adjustment", "Adjustment layer"),
        ('SHADER', "Shader", "Shader layer"),
        ('NODE_GROUP', "Node Group", "Node Group layer"),
        ('GRADIENT', "Gradient", "Gradient layer"),
    ],
        default='IMAGE'
    )
    sub_type: StringProperty(
        name="Sub Type",
        default="",
    )
    clip: BoolProperty(
        name="Clip to Below",
        description="Clip the layer to the one below",
        default=False,
    )
    lock_alpha: BoolProperty(
        name="Lock Alpha",
        description="Lock the alpha channel",
        default=False,
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
    edit_mask: BoolProperty(
        name="Edit Mask",
        description="Edit mask",
        default=False,
    )
    mask_image: PointerProperty(
        name="Mask Image",
        type=Image,
    )
    enable_mask: BoolProperty(
        name="Enabled Mask",
        description="Toggle mask visibility",
        default=False,
    )
    mask_uv_map: StringProperty(
        name="Mask UV Map",
        default="",
    )
    external_image: PointerProperty(
        name="Edit External Image",
        type=Image,
    )
    expanded: BoolProperty(
        name="Expanded",
        description="Expand the layer",
        default=True,
    )

class LegacyPaintSystemGroup(PropertyGroup):
# Define the collection property directly in the class
    items: CollectionProperty(type=LegacyPaintSystemLayer)
    name: StringProperty(
        name="Name",
        description="Group name",
        default="Group",
    )
    active_index: IntProperty(
        name="Active Index",
        description="Active layer index",
    )
    node_tree: PointerProperty(
        name="Node Tree",
        type=bpy.types.NodeTree
    )
    bake_image: PointerProperty(
        name="Bake Image",
        type=Image
    )
    bake_uv_map: StringProperty(
        name="Bake Image UV Map",
        default="UVMap",
    )
    use_bake_image: BoolProperty(
        name="Use Bake Image",
        default=False,
    )


class LegacyPaintSystemGroups(PropertyGroup):
    name: StringProperty(
        name="Name",
        description="Paint system name",
        default="Paint System"
    )
    groups: CollectionProperty(type=LegacyPaintSystemGroup)
    active_index: IntProperty(
        name="Active Index",
        description="Active group index",
    )
    use_paintsystem_uv: BoolProperty(
        name="Use Paint System UV",
        description="Use the Paint System UV Map",
        default=True
    )

class LegacyPaintSystemContextParser:
    def __init__(self, context: bpy.types.Context):
        self.context = context
        self.active_object = context.object
        self.groups = self.get_groups()
        # layer_node_tree = self.get_active_layer().node_tree
        # self.layer_node_group = self.get_active_layer_node_group()
        self.color_mix_node = self.find_color_mix_node()
        self.uv_map_node = self.find_uv_map_node()
        self.opacity_mix_node = self.find_opacity_mix_node()
        self.clip_mix_node = self.find_clip_mix_node()
        self.rgb_node = self.find_rgb_node()
        
    def get_active_material(self) -> Optional[Material]:
        if not self.active_object or self.active_object.type != 'MESH':
            return None

        return self.active_object.active_material

    def get_material_settings(self):
        mat = self.get_active_material()
        if not mat or not hasattr(mat, "paint_system"):
            return None
        return mat.paint_system

    def get_groups(self) -> Optional[PropertyGroup]:
        paint_system = self.get_material_settings()
        if not paint_system:
            return None
        return paint_system.groups

    def get_active_group(self) -> Optional[PropertyGroup]:
        paint_system = self.get_material_settings()
        if not paint_system or len(paint_system.groups) == 0:
            return None
        active_group_idx = int(paint_system.active_index)
        if active_group_idx >= len(paint_system.groups):
            return None  # handle cases where active index is invalid
        return paint_system.groups[active_group_idx]

    def get_active_layer(self) -> Optional[PropertyGroup]:
        active_group = self.get_active_group()
        if not active_group or len(active_group.items) == 0 or active_group.active_index >= len(active_group.items):
            return None

        return active_group.items[active_group.active_index]

    def get_layer_node_tree(self) -> Optional[NodeTree]:
        active_layer = self.get_active_layer()
        if not active_layer:
            return None
        return active_layer.node_tree

    def get_active_layer_node_group(self) -> Optional[Node]:
        active_group = self.get_active_group()
        layer_node_tree = self.get_layer_node_tree()
        if not layer_node_tree:
            return None
        node_details = {'type': 'GROUP', 'node_tree': layer_node_tree}
        node = self.find_node(active_group.node_tree, node_details)
        return node

    def find_color_mix_node(self) -> Optional[Node]:
        layer_node_tree = self.get_layer_node_tree()
        node_details = {'type': 'MIX', 'data_type': 'RGBA'}
        return self.find_node(layer_node_tree, node_details)

    def find_uv_map_node(self) -> Optional[Node]:
        layer_node_tree = self.get_layer_node_tree()
        node_details = {'type': 'UVMAP'}
        return self.find_node(layer_node_tree, node_details)

    def find_opacity_mix_node(self) -> Optional[Node]:
        layer_node_tree = self.get_layer_node_tree()
        node_details = {'type': 'MIX', 'name': 'Opacity'}
        return self.find_node(layer_node_tree, node_details) or self.find_color_mix_node()

    def find_clip_mix_node(self) -> Optional[Node]:
        layer_node_tree = self.get_layer_node_tree()
        node_details = {'type': 'MIX', 'name': 'Clip'}
        return self.find_node(layer_node_tree, node_details)

    def find_image_texture_node(self) -> Optional[Node]:
        layer_node_tree = self.get_layer_node_tree()
        node_details = {'type': 'TEX_IMAGE'}
        return self.find_node(layer_node_tree, node_details)

    def find_rgb_node(self) -> Optional[Node]:
        layer_node_tree = self.get_layer_node_tree()
        node_details = {'name': 'RGB'}
        return self.find_node(layer_node_tree, node_details)

    def find_adjustment_node(self) -> Optional[Node]:
        layer_node_tree = self.get_layer_node_tree()
        node_details = {'label': 'Adjustment'}
        return self.find_node(layer_node_tree, node_details)

    def find_node_group(self, node_tree: NodeTree) -> Optional[Node]:
        node_tree = self.get_active_group().node_tree
        for node in node_tree.nodes:
            if hasattr(node, 'node_tree') and node.node_tree and node.node_tree.name == node_tree.name:
                return node
        return None
    
    def find_attribute_node(self) -> Optional[Node]:
        layer_node_tree = self.get_active_layer().node_tree
        node_details = {'type': 'ATTRIBUTE'}
        return self.find_node(layer_node_tree, node_details)
    
    def find_node(self, node_tree, node_details):
        if not node_tree:
            return None
        for node in node_tree.nodes:
            match = True
            for key, value in node_details.items():
                if getattr(node, key) != value:
                    match = False
                    break
            if match:
                return node
        return None
