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

        # 선택 영역 (라쏘 → 스텐실 마스크)
        ip = context.tool_settings.image_paint
        box = layout.box()
        col = box.column(align=True)
        col.label(text="Selection", icon='SELECT_SET')
        row = col.row(align=True)
        row.operator("paint_system.invert_selection", text="Invert")
        row.operator("paint_system.clear_selection", text="Clear")
        col.operator(
            "paint_system.fill_selection",
            text="Fill (Alt+Del)",
            icon='SNAP_FACE',
        )

        # PSD 왕복 연동
        from ..operators.psd_operators import KEY_PSD_PATH, is_sync_running
        box = layout.box()
        col = box.column(align=True)
        col.label(text="Photoshop (PSD)", icon='FILE_IMAGE')
        row = col.row(align=True)
        row.operator("paint_system.export_psd", text="Export")
        row.operator("paint_system.import_psd", text="Import")
        row.operator("paint_system.open_psd_in_photoshop", text="Open PS")
        psd_path = context.scene.get(KEY_PSD_PATH)
        if psd_path:
            import os
            col.label(text=os.path.basename(psd_path))
            col.operator(
                "paint_system.toggle_psd_sync",
                text="Stop Live Sync" if is_sync_running() else "Start Live Sync",
                icon='PAUSE' if is_sync_running() else 'PLAY',
                depress=is_sync_running(),
            )


classes = (
    MAT_PT_PaintSystem2DView,
)

register, unregister = bpy.utils.register_classes_factory(classes)
