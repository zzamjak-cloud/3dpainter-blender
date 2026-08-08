# SPDX-License-Identifier: GPL-3.0-or-later
# 3DPainter 포크 추가 기능: Projection Tex 패널

import bpy
from bpy.types import Panel, UIList


class PAINTSYSTEM_UL_ProjectionTexList(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_prop, index):
        row = layout.row(align=True)
        row.prop(item, "name", text="", emboss=False, icon='IMAGE_DATA')


class MAT_PT_PaintSystemProjectionTex(Panel):
    bl_idname = "MAT_PT_PaintSystemProjectionTex"
    bl_label = "Projection Tex"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Paint System'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        row = layout.row()
        row.template_list(
            "PAINTSYSTEM_UL_ProjectionTexList", "",
            scene, "ps_projection_textures",
            scene, "ps_projection_active_index",
            rows=3,
        )
        col = row.column(align=True)
        col.operator("paint_system.projection_import", text="", icon='IMPORT')
        col.operator("paint_system.projection_remove", text="", icon='REMOVE')

        layout.operator(
            "paint_system.projection_place",
            text="Place & Apply",
            icon='MOD_UVPROJECT',
        )


classes = (
    PAINTSYSTEM_UL_ProjectionTexList,
    MAT_PT_PaintSystemProjectionTex,
)

register, unregister = bpy.utils.register_classes_factory(classes)
