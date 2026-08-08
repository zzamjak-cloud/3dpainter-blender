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

        # 썸네일 뷰: 클릭하면 등록 이미지 전체가 그리드로 펼쳐짐 (호버 시 이름)
        if len(scene.ps_projection_textures):
            layout.template_icon_view(
                scene, "ps_projection_enum",
                show_labels=False,
                scale=scene.ps_projection_thumb_scale,
                scale_popup=scene.ps_projection_thumb_scale,
            )
        layout.prop(scene, "ps_projection_thumb_scale", slider=True)

        row = layout.row(align=True)
        row.operator("paint_system.projection_import", text="Import", icon='IMPORT')
        row.operator("paint_system.projection_remove", text="", icon='REMOVE')

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
