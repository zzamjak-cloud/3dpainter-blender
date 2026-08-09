# SPDX-License-Identifier: GPL-3.0-or-later
# 3DPainter 포크 추가 기능: Projection Tex 패널

import sys

import bpy
from bpy.types import Panel

from ..utils.registration import collect_classes


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

        # 활성 썸네일 표시 — 클릭 시 프리뷰 팝업 그리드에서 선택
        items = scene.ps_projection_textures
        if len(items):
            col = layout.column(align=True)
            col.template_icon_view(
                scene, "ps_projection_enum",
                show_labels=True,
                scale=scene.ps_projection_thumb_scale,
                scale_popup=5.0,
            )
            active = scene.ps_projection_active_index
            if 0 <= active < len(items):
                col.label(text=items[active].name)
            layout.prop(scene, "ps_projection_thumb_scale", slider=True)

        row = layout.row(align=True)
        row.operator("paint_system.projection_import", text="Import", icon='IMPORT')
        row.operator("paint_system.projection_remove", text="Remove", icon='REMOVE')

        layout.operator(
            "paint_system.projection_place",
            text="Place",
            icon='MOD_UVPROJECT',
        )


classes = collect_classes(sys.modules[__name__])

register, unregister = bpy.utils.register_classes_factory(classes)
