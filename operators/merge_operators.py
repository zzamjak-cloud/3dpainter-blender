# SPDX-License-Identifier: GPL-3.0-or-later
# 3DPainter 포크 추가 기능: Quick Merge Down — 베이크 없이 즉시 레이어 병합
#
# 업스트림 Merge Down은 채널 전체를 렌더 베이크해서 느리고 해상도
# 다이얼로그를 띄운다. 일반적인 "이미지 레이어 위에 이미지 레이어"는
# PSD처럼 픽셀 합성만 하면 되므로 numpy로 즉시 처리하고, 복잡한 경우
# (조정/절차 레이어, 클립, 다른 UV, 미지원 블렌드)만 기존 베이크로 폴백한다.

import sys

import numpy as np

import bpy
from bpy.types import Operator

from .common import PSContextMixin
from ..paintsystem.image import read_rgba, write_rgba
from ..utils.imaging import bilinear_resize
from ..utils.registration import collect_classes


def _blend_rgb(mode: str, cb: np.ndarray, ct: np.ndarray) -> np.ndarray | None:
    """블렌드 모드별 RGB 결합 (0~1, sRGB 값 기준 — 포토샵과 동일 공간)."""
    if mode == 'MIX':
        return ct
    if mode == 'MULTIPLY':
        return cb * ct
    if mode == 'SCREEN':
        return 1.0 - (1.0 - cb) * (1.0 - ct)
    if mode == 'OVERLAY':
        return np.where(cb <= 0.5, 2.0 * cb * ct,
                        1.0 - 2.0 * (1.0 - cb) * (1.0 - ct))
    if mode == 'ADD':
        return np.clip(cb + ct, 0.0, 1.0)
    if mode == 'SUBTRACT':
        return np.clip(cb - ct, 0.0, 1.0)
    if mode == 'DIFFERENCE':
        return np.abs(cb - ct)
    if mode == 'DARKEN':
        return np.minimum(cb, ct)
    if mode == 'LIGHTEN':
        return np.maximum(cb, ct)
    if mode == 'DIVIDE':
        return np.clip(cb / np.maximum(ct, 1e-4), 0.0, 1.0)
    if mode == 'SOFT_LIGHT':
        return (1.0 - 2.0 * ct) * cb * cb + 2.0 * ct * cb
    return None  # 미지원 → 폴백


_SUPPORTED_BLENDS = {
    'MIX', 'MULTIPLY', 'SCREEN', 'OVERLAY', 'ADD', 'SUBTRACT',
    'DIFFERENCE', 'DARKEN', 'LIGHTEN', 'DIVIDE', 'SOFT_LIGHT',
}


class PAINTSYSTEM_OT_QuickMergeDown(PSContextMixin, Operator):
    """아래 레이어와 즉시 병합한다 (이미지 레이어끼리는 다이얼로그·베이크 없음)"""
    bl_idname = "paint_system.quick_merge_down"
    bl_label = "Merge Down"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def _below_layer(cls, ps_ctx):
        channel = ps_ctx.active_channel
        layer = ps_ctx.active_layer
        if channel is None or layer is None:
            return None
        flat = channel.flattened_layers
        idx = flat.index(layer) if layer in flat else -1
        if 0 <= idx < len(flat) - 1:
            return flat[idx + 1]
        return None

    @classmethod
    def poll(cls, context):
        ps_ctx = cls.parse_context(context)
        layer = ps_ctx.active_layer
        below = cls._below_layer(ps_ctx)
        return (
            layer is not None and below is not None
            and layer.type != 'FOLDER' and below.type != 'FOLDER'
            and layer.parent_id == below.parent_id
            and layer.enabled and below.enabled
        )

    def _fallback(self, context):
        # 복잡한 케이스는 업스트림 베이크 병합으로 (다이얼로그 포함)
        return bpy.ops.paint_system.merge_down('INVOKE_DEFAULT')

    def execute(self, context):
        ps_ctx = self.parse_context(context)
        channel = ps_ctx.active_channel
        top = ps_ctx.active_layer
        below = self._below_layer(ps_ctx)
        if top is None or below is None:
            return {'CANCELLED'}

        # 빠른 경로 조건: 둘 다 일반 이미지 레이어 + 같은 UV + 지원 블렌드
        simple = (
            top.type == 'IMAGE' and below.type == 'IMAGE'
            and top.image is not None and below.image is not None
            and not top.is_clip and not below.is_clip
            and getattr(top, 'coord_type', 'UV') in {'UV', 'AUTO'}
            and getattr(below, 'coord_type', 'UV') in {'UV', 'AUTO'}
            and top.uv_map_name == below.uv_map_name
            and not below.modifies_color_data
        )
        if not simple or top.blend_mode not in _SUPPORTED_BLENDS:
            return self._fallback(context)

        bw, bh = int(below.image.size[0]), int(below.image.size[1])
        tw, th = int(top.image.size[0]), int(top.image.size[1])
        if bw == 0 or bh == 0 or tw == 0 or th == 0:
            return self._fallback(context)

        bottom_np = read_rgba(below.image)
        top_np = read_rgba(top.image)
        if (th, tw) != (bh, bw):
            # 아래 레이어 해상도 기준으로 통일 (PSD와 동일한 규칙)
            top_np = bilinear_resize(top_np, bh, bw)

        cb = bottom_np[:, :, :3]
        ct = top_np[:, :, :3]
        ba = bottom_np[:, :, 3]
        ta = np.clip(top_np[:, :, 3] * float(top.opacity), 0.0, 1.0)

        mixed = _blend_rgb(top.blend_mode, cb, ct)
        ta3 = ta[:, :, None]
        ba3 = ba[:, :, None]
        # W3C/포토샵 합성: 상단이 불투명한 곳은 블렌드 결과,
        # 하단만 있는 곳은 하단색, 상단만 있는 곳은 상단색
        premult = (
            ta3 * (1.0 - ba3) * ct
            + ta3 * ba3 * mixed
            + (1.0 - ta3) * ba3 * cb
        )
        out_a = ta + ba - ta * ba
        safe = np.maximum(out_a[:, :, None], 1e-6)
        result = np.empty_like(bottom_np)
        result[:, :, :3] = premult / safe
        result[:, :, 3] = out_a

        write_rgba(below.image, result)

        # 위 레이어 제거 → 아래 레이어가 병합 결과를 갖고 살아남는다 (PS와 동일)
        channel.delete_layer(context, top)
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
        return {'FINISHED'}


classes = collect_classes(sys.modules[__name__])

register, unregister = bpy.utils.register_classes_factory(classes)
