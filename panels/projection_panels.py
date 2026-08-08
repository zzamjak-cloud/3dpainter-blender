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

        # 항상 펼쳐진 썸네일 그리드 — 호버 시 이름, 클릭(체크 버튼)으로 선택
        from ..operators.projection_operators import _thumb_icon_id
        items = scene.ps_projection_textures
        if len(items):
            scale = scene.ps_projection_thumb_scale
            grid = layout.grid_flow(
                row_major=True, columns=0, even_columns=True, align=False)
            active = scene.ps_projection_active_index
            for i, item in enumerate(items):
                cell = grid.box() if i == active else grid.column()
                col = cell.column(align=True)
                col.template_icon(
                    icon_value=_thumb_icon_id(item), scale=scale)
                op = col.operator(
                    "paint_system.projection_select",
                    text="",
                    icon='RADIOBUT_ON' if i == active else 'RADIOBUT_OFF',
                    depress=(i == active),
                )
                op.index = i
        layout.prop(scene, "ps_projection_thumb_scale", slider=True)

        row = layout.row(align=True)
        row.operator("paint_system.projection_import", text="Import", icon='IMPORT')
        row.operator("paint_system.projection_remove", text="", icon='REMOVE')

        layout.operator(
            "paint_system.projection_place",
            text="Place & Apply",
            icon='MOD_UVPROJECT',
        )


classes = collect_classes(sys.modules[__name__])

register, unregister = bpy.utils.register_classes_factory(classes)
