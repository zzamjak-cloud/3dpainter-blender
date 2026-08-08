# SPDX-License-Identifier: GPL-3.0-or-later
# 3DPainter 포크 추가 기능: 포토샵식 Shift+클릭 직선 스트로크

import math

import bpy
from bpy.types import Operator

# 마지막 스트로크 시작점(앵커). (region 포인터, x, y) — 다른 리전에서는 무효 처리
_anchor: tuple[int, float, float] | None = None


def _set_anchor(region, x: float, y: float) -> None:
    global _anchor
    _anchor = (region.as_pointer(), float(x), float(y))


def _get_anchor(region) -> tuple[float, float] | None:
    if _anchor is None or _anchor[0] != region.as_pointer():
        return None
    return (_anchor[1], _anchor[2])


def _brush_step_px(context) -> tuple[float, float]:
    """(브러시 반경 px, 스탬프 간격 px)을 반환한다."""
    ip = context.tool_settings.image_paint
    brush = ip.brush
    # 5.x에서 unified_paint_settings가 ToolSettings → Paint로 이동해 양쪽을 가드
    ups = getattr(context.tool_settings, 'unified_paint_settings', None)
    if ups is None:
        ups = getattr(ip, 'unified_paint_settings', None)
    size = brush.size
    if ups is not None and getattr(ups, 'use_unified_size', False):
        size = ups.size
    spacing = max(getattr(brush, 'spacing', 10), 1)
    # 네이티브 스트로크의 스탬프 간격은 지름 기준 퍼센트다.
    # 반지름 기준으로 계산하면 2배 촘촘해져 알파가 누적돼 선이 굵어 보인다.
    return float(size), max(1.0, float(size) * 2.0 * spacing / 100.0)


class PAINTSYSTEM_OT_RecordStrokeAnchor(Operator):
    """일반 클릭 위치를 직선 앵커로 기록하고 이벤트는 그대로 통과시킨다"""
    bl_idname = "paint_system.record_stroke_anchor"
    bl_label = "Record Stroke Anchor"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'PAINT_TEXTURE'

    def invoke(self, context, event):
        if context.region is not None:
            _set_anchor(context.region, event.mouse_region_x, event.mouse_region_y)
        return {'PASS_THROUGH'}


class PAINTSYSTEM_OT_LineStroke(Operator):
    """앵커에서 클릭 지점까지 현재 브러시로 직선을 긋는다 (포토샵 Shift+클릭)"""
    bl_idname = "paint_system.line_stroke"
    bl_label = "Line Stroke"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (
            context.mode == 'PAINT_TEXTURE'
            and context.tool_settings.image_paint.brush is not None
        )

    def invoke(self, context, event):
        region = context.region
        if region is None:
            return {'CANCELLED'}
        # 펜이면 클릭 시점의 필압을 따라간다 — 항상 1.0이면 평소 스트로크보다 굵어 보임
        self._pressure = float(getattr(event, 'pressure', 1.0)) or 1.0
        end = (float(event.mouse_region_x), float(event.mouse_region_y))
        start = _get_anchor(region)

        if start is None:
            points = [end]
        else:
            size, step = _brush_step_px(context)
            dist = math.hypot(end[0] - start[0], end[1] - start[1])
            count = max(int(math.ceil(dist / step)), 1)
            points = [
                (
                    start[0] + (end[0] - start[0]) * i / count,
                    start[1] + (end[1] - start[1]) * i / count,
                )
                for i in range(count + 1)
            ]

        size, _ = _brush_step_px(context)
        # 버전에 따라 스트로크 요소 필드가 달라서(예: 5.x에서 pen_flip 제거)
        # 실제 RNA에 존재하는 키만 보낸다
        allowed = {
            p.identifier
            for p in bpy.types.OperatorStrokeElement.bl_rna.properties
        } - {'rna_type'}
        stroke = []
        for i, (x, y) in enumerate(points):
            element = {
                "name": "",
                "location": (0.0, 0.0, 0.0),
                "mouse": (x, y),
                "mouse_event": (x, y),
                "pen_flip": False,
                "pressure": self._pressure,
                "size": size,
                "time": float(i) * 0.01,
                "is_start": i == 0,
                "x_tilt": 0.0,
                "y_tilt": 0.0,
            }
            stroke.append({k: v for k, v in element.items() if k in allowed})
        try:
            bpy.ops.paint.image_paint(stroke=stroke, mode='NORMAL')
        except RuntimeError:
            # 캔버스 미설정 등 페인트 불가 상태면 조용히 취소
            return {'CANCELLED'}

        _set_anchor(region, end[0], end[1])
        return {'FINISHED'}


classes = (
    PAINTSYSTEM_OT_RecordStrokeAnchor,
    PAINTSYSTEM_OT_LineStroke,
)

register, unregister = bpy.utils.register_classes_factory(classes)
