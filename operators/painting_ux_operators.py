# SPDX-License-Identifier: GPL-3.0-or-later
# 3DPainter 포크 추가 기능: 포토샵식 페인팅 조작 (Q 팔레트 토글, F 누른 채 드래그 브러시 크기)

import math
import sys

import bpy
import gpu
from bpy.types import Operator
from gpu_extras.batch import batch_for_shader

from ..utils.registration import collect_classes
from ..utils.unified_brushes import get_unified_settings

# Q 팔레트 팝오버를 같은 키로 닫기 위한 전용 키맵 — 팝오버가 이 키맵과 맞는
# 이벤트를 받으면 닫힌다 (keymaps.py 에서 Q 항목을 등록)
PALETTE_POPOVER_KEYMAP = "3DPainter Palette Popover"


class PAINTSYSTEM_OT_TogglePalettePopup(Operator):
    """전경색 피커·팔레트 팝업을 연다 (열린 상태에서 Q를 다시 누르면 닫힌다)"""
    bl_idname = "paint_system.toggle_palette_popup"
    bl_label = "Palette"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'PAINT_TEXTURE'

    def invoke(self, context, event):
        from ..panels.extras_panels import draw_palette_quick_picker
        kc = context.window_manager.keyconfigs.addon
        keymap = kc.keymaps.get(PALETTE_POPOVER_KEYMAP) if kc else None
        context.window_manager.popover(
            lambda popup, ctx: draw_palette_quick_picker(popup.layout, ctx),
            ui_units_x=12, keymap=keymap)
        return {'FINISHED'}

    def execute(self, context):
        return {'CANCELLED'}


def _size_owner(context):
    """브러시 크기를 가진 쪽 (통합 크기면 UnifiedPaintSettings, 아니면 브러시)."""
    return get_unified_settings(context, "use_unified_size")


class PAINTSYSTEM_OT_DragBrushSize(Operator):
    """F 를 누른 채 위아래로 움직여 브러시 크기를 바꾼다 — 떼면 확정 (위 = 커짐, Shift = 미세 조정, ESC = 취소)"""
    bl_idname = "paint_system.drag_brush_size"
    bl_label = "Drag Brush Size"
    # 브러시 크기는 undo 대상이 아니다 — 스텝을 만들면 페인팅 undo 가 오염된다
    bl_options = {'REGISTER'}

    MIN_SIZE = 1
    MAX_SIZE = 5000

    @classmethod
    def poll(cls, context):
        return (
            context.mode == 'PAINT_TEXTURE'
            and context.area is not None and context.area.type == 'VIEW_3D'
            and _size_owner(context) is not None
        )

    def invoke(self, context, event):
        owner = _size_owner(context)
        self._owner = owner
        self._start_size = int(owner.size)
        self._start_unprojected = getattr(owner, "unprojected_radius", None)
        self._start_y = event.mouse_y
        # 이 키를 떼면 확정한다 (키맵에서 바꿔도 동작하도록 트리거 키를 기억)
        self._trigger = event.type
        self._center = (event.mouse_region_x, event.mouse_region_y)
        self._region_ptr = context.region.as_pointer()
        # 드래그 중엔 블렌더 브러시 커서가 마우스를 따라가므로 끄고 시작점에 원을 고정해 그린다
        paint = context.tool_settings.image_paint
        self._show_brush = paint.show_brush
        paint.show_brush = False
        self._handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw, (), 'WINDOW', 'POST_PIXEL')
        context.window.cursor_modal_set('NONE')
        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def _apply(self, size: int) -> None:
        owner = self._owner
        size = max(self.MIN_SIZE, min(self.MAX_SIZE, int(size)))
        if owner.size != size:
            owner.size = size
        # 크기를 월드 단위로 고정한 브러시는 비율대로 함께 조정한다
        if (self._start_unprojected is not None
                and getattr(owner, "use_locked_size", 'VIEW') == 'SCENE'):
            owner.unprojected_radius = self._start_unprojected * size / max(self._start_size, 1)

    def _finish(self, context) -> None:
        if self._handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self._handle = None
        context.tool_settings.image_paint.show_brush = self._show_brush
        context.window.cursor_modal_restore()
        context.area.tag_redraw()

    def modal(self, context, event):
        if event.type == 'MOUSEMOVE':
            dy = event.mouse_y - self._start_y
            scale = 0.25 if event.shift else 1.0
            self._apply(self._start_size + dy * scale)
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == self._trigger:
            if event.value == 'RELEASE':
                self._finish(context)
                return {'FINISHED'}
            return {'RUNNING_MODAL'}  # 누르고 있는 동안의 키 반복 입력은 무시
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self._finish(context)
            return {'FINISHED'}
        if event.type == 'ESC' or (event.type == 'RIGHTMOUSE' and event.value == 'PRESS'):
            self._apply(self._start_size)
            if self._start_unprojected is not None and hasattr(self._owner, "unprojected_radius"):
                self._owner.unprojected_radius = self._start_unprojected
            self._finish(context)
            return {'CANCELLED'}
        # 다른 앱으로 전환하면 키 RELEASE 를 못 받으므로 현재 크기로 확정하고 끝낸다
        if event.type == 'WINDOW_DEACTIVATE':
            self._finish(context)
            return {'FINISHED'}
        return {'RUNNING_MODAL'}

    def _draw(self):
        region = bpy.context.region
        if region is None or region.as_pointer() != self._region_ptr:
            return
        # 블렌더 브러시 커서와 같은 기준 — 반지름 px 에 UI 배율을 곱한다
        radius = float(self._owner.size) * bpy.context.preferences.system.pixel_size
        cx, cy = self._center
        segments = max(32, min(256, int(radius * 0.5)))
        coords = [
            (cx + radius * math.cos(t), cy + radius * math.sin(t))
            for t in (i * math.tau / segments for i in range(segments + 1))
        ]
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        gpu.state.line_width_set(1.0)
        # 밝은/어두운 배경 모두에서 보이도록 흑백 두 겹
        for offset, color in ((1.0, (0.0, 0.0, 0.0, 0.8)), (0.0, (1.0, 1.0, 1.0, 0.95))):
            pts = [(x + offset, y - offset) for x, y in coords]
            batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": pts})
            shader.uniform_float("color", color)
            batch.draw(shader)
        gpu.state.blend_set('NONE')


classes = collect_classes(sys.modules[__name__])

register, unregister = bpy.utils.register_classes_factory(classes)
