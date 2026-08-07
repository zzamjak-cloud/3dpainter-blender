# SPDX-License-Identifier: GPL-3.0-or-later
# 3DPainter 포크 추가 기능: 2D 텍스처 뷰 패널

import bpy
from bpy.types import Panel

from ..operators.view2d_operators import get_canvas_object


class MAT_PT_PaintSystem2DView(Panel):
    bl_idname = "MAT_PT_PaintSystem2DView"
    bl_label = "2D View"
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
        is_open = get_canvas_object(context.scene) is not None
        layout.operator(
            "paint_system.toggle_2d_view",
            text="Close 2D View" if is_open else "Open 2D View",
            icon='UV' if not is_open else 'PANEL_CLOSE',
        )
        if is_open:
            layout.operator(
                "paint_system.refresh_2d_canvas",
                text="Refresh Canvas (UV Changed)",
                icon='FILE_REFRESH',
            )


classes = (
    MAT_PT_PaintSystem2DView,
)

register, unregister = bpy.utils.register_classes_factory(classes)
