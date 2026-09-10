# SPDX-License-Identifier: GPL-3.0-or-later
"""페인팅 정밀도 관련 오퍼레이터.

포토샵과 인상이 달라지는 원인은 크게 둘이다.

* **텍셀 보간** — 이미지 레이어 노드가 ``Closest`` 로 만들어지면 텍셀 경계가
  그대로 드러나 획이 계단처럼 보인다. ``Linear`` 로 바꾸면 포토샵에 가깝게
  부드러워진다.
* **필압** — 블렌더 기본 브러시는 크기·불투명도 필압이 **둘 다 꺼져 있다**.
"""

import sys

import bpy
from bpy.types import Operator

from ..paintsystem.data import iter_all_layers
from ..paintsystem.graph.basic_layers import IMAGE_INTERPOLATIONS
from ..preferences import get_preferences
from ..utils.registration import collect_classes
from .brushes import apply_brush_precision
from .common import PSContextMixin


class PAINTSYSTEM_OT_ApplyTextureInterpolation(PSContextMixin, Operator):
    """모든 이미지 레이어의 텍셀 보간 방식을 프리퍼런스 설정으로 통일한다"""
    bl_idname = "paint_system.apply_texture_interpolation"
    bl_label = "Apply Texture Filtering"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        interpolation = get_preferences(context).texture_interpolation
        if interpolation not in IMAGE_INTERPOLATIONS:
            self.report({'ERROR'}, f"알 수 없는 보간 방식: {interpolation}")
            return {'CANCELLED'}

        changed = 0
        for _material, _group, _channel, layer in iter_all_layers():
            if layer.type != 'IMAGE':
                continue
            node = layer.source_node
            if node is None or not hasattr(node, 'interpolation'):
                continue
            if node.interpolation != interpolation:
                node.interpolation = interpolation
                changed += 1

        self.report({'INFO'}, f"텍스처 필터링 {interpolation} 적용: 레이어 {changed}개")
        return {'FINISHED'}


class PAINTSYSTEM_OT_SetupBrushPrecision(PSContextMixin, Operator):
    """현재 브러시에 포토샵식 필압·정밀도 기본값을 적용한다"""
    bl_idname = "paint_system.setup_brush_precision"
    bl_label = "Photoshop-style Brush Precision"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        paint = getattr(context.tool_settings, "image_paint", None)
        return getattr(paint, "brush", None) is not None

    def execute(self, context):
        brush = context.tool_settings.image_paint.brush
        prefs = get_preferences(context)
        apply_brush_precision(brush, prefs)
        self.report(
            {'INFO'},
            "브러시 정밀도 적용: 필압(크기·불투명도) ON, "
            f"입력 샘플 {prefs.brush_input_samples}, 간격 {prefs.brush_spacing}%")
        return {'FINISHED'}


classes = collect_classes(sys.modules[__name__])

register, unregister = bpy.utils.register_classes_factory(classes)
