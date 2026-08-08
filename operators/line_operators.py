# SPDX-License-Identifier: GPL-3.0-or-later
# 3DPainter 포크 추가 기능: 포토샵식 Shift+클릭 직선 스트로크
#
# bpy.ops.paint.image_paint 위임은 스트로크 요소의 크기 해석(화면↔월드 변환)을
# 통제할 수 없어 카메라 줌에 따라 두께가 널뛰었다. 대신 활성 레이어 이미지에
# 직접 래스터라이즈한다: 도장 마스크를 max로 합성해 균일한 두께의 선을 만들고,
# 브러시 화면 반경을 도장 위치의 실제 UV 스케일로 환산해 어떤 뷰·줌에서도
# 커서 원 크기와 일치시킨다.

import math

import numpy as np

import bpy
from bpy.types import Operator
from bpy_extras import view3d_utils
from mathutils import Vector
from mathutils.geometry import barycentric_transform, intersect_point_tri

# 마지막 스트로크 시작점(앵커). (region 포인터, x, y) — 다른 리전에서는 무효 처리
_anchor: tuple[int, float, float] | None = None

# 선 가장자리 소프트니스 (반경 대비 비율, 0=하드)
EDGE_SOFTNESS = 0.25


def _set_anchor(region, x: float, y: float) -> None:
    global _anchor
    _anchor = (region.as_pointer(), float(x), float(y))


def _get_anchor(region) -> tuple[float, float] | None:
    if _anchor is None or _anchor[0] != region.as_pointer():
        return None
    return (_anchor[1], _anchor[2])


def _brush_settings(context) -> tuple[float, float]:
    """(브러시 화면 반경 px, 강도 0~1)을 unified 설정을 존중해 반환한다."""
    ip = context.tool_settings.image_paint
    brush = ip.brush
    # 5.x에서 unified_paint_settings가 ToolSettings → Paint로 이동해 양쪽을 가드
    ups = getattr(context.tool_settings, 'unified_paint_settings', None)
    if ups is None:
        ups = getattr(ip, 'unified_paint_settings', None)
    size = brush.size
    strength = getattr(brush, 'strength', 1.0)
    if ups is not None:
        if getattr(ups, 'use_unified_size', False):
            size = ups.size
        if getattr(ups, 'use_unified_strength', False):
            strength = ups.strength
    return float(size), float(strength)


def _uv_from_hit(obj, face_index: int, hit_local) -> tuple[float, float] | None:
    """레이캐스트 히트 지점의 UV를 페이스 삼각 분할 + 무게중심 보간으로 구한다."""
    mesh = obj.data
    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        return None
    poly = mesh.polygons[face_index]
    loops = list(poly.loop_indices)
    verts = [mesh.vertices[mesh.loops[li].vertex_index].co for li in loops]
    uvs = [uv_layer.data[li].uv for li in loops]

    def transform(i0, i1, i2):
        uv3 = barycentric_transform(
            hit_local, verts[i0], verts[i1], verts[i2],
            Vector((uvs[i0].x, uvs[i0].y, 0.0)),
            Vector((uvs[i1].x, uvs[i1].y, 0.0)),
            Vector((uvs[i2].x, uvs[i2].y, 0.0)),
        )
        return (uv3.x, uv3.y)

    # 팬 삼각분할에서 히트를 포함하는 삼각형을 찾는다
    for i in range(1, len(verts) - 1):
        if intersect_point_tri(hit_local, verts[0], verts[i], verts[i + 1]):
            return transform(0, i, i + 1)
    return transform(0, 1, len(verts) - 1)  # 수치 오차 폴백


def _screen_to_uv(context, region, coord) -> tuple[float, float] | None:
    """리전 좌표 → 활성 오브젝트 표면의 UV 좌표 (빗나가면 None)."""
    obj = context.view_layer.objects.active
    if obj is None or obj.type != 'MESH':
        return None
    rv3d = region.data
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
    mw_inv = obj.matrix_world.inverted()
    local_origin = mw_inv @ origin
    local_dir = (mw_inv.to_3x3() @ direction).normalized()
    hit, loc, _n, face_index = obj.ray_cast(local_origin, local_dir)
    if not hit:
        return None
    return _uv_from_hit(obj, face_index, loc)


def _texel_radius_at(context, region, coord, screen_radius: float,
                     img_w: int) -> float | None:
    """도장 위치에서 브러시 화면 반경을 텍스처 픽셀 반경으로 환산한다."""
    uv0 = _screen_to_uv(context, region, coord)
    if uv0 is None:
        return None
    for dx, dy in ((screen_radius, 0), (-screen_radius, 0),
                   (0, screen_radius), (0, -screen_radius)):
        uv1 = _screen_to_uv(context, region, (coord[0] + dx, coord[1] + dy))
        if uv1 is not None:
            return math.hypot(uv1[0] - uv0[0], uv1[1] - uv0[1]) * img_w
    return None


def _selected_map(context, w: int, h: int) -> np.ndarray | None:
    """활성 스텐실(차단 맵)에서 선택 영역(bool)을 얻는다. 없으면 None."""
    ip = context.tool_settings.image_paint
    if not ip.use_stencil_layer or ip.stencil_image is None:
        return None
    m = ip.stencil_image
    mw, mh = int(m.size[0]), int(m.size[1])
    buf = np.empty(mw * mh * 4, dtype=np.float32)
    m.pixels.foreach_get(buf)
    masked = buf.reshape(mh, mw, 4)[:, :, 0]
    if (mh, mw) != (h, w):
        ys = (np.arange(h) * mh // h).clip(0, mh - 1)
        xs = (np.arange(w) * mw // w).clip(0, mw - 1)
        masked = masked[np.ix_(ys, xs)]
    return masked < 0.5


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
            and context.tool_settings.image_paint.canvas is not None
        )

    def invoke(self, context, event):
        region = context.region
        if region is None or region.data is None:
            return {'CANCELLED'}
        end = (float(event.mouse_region_x), float(event.mouse_region_y))
        start = _get_anchor(region) or end

        ip = context.tool_settings.image_paint
        img = ip.canvas
        w, h = int(img.size[0]), int(img.size[1])
        if w == 0 or h == 0:
            return {'CANCELLED'}

        size_px, strength = _brush_settings(context)
        # 펜이면 클릭 시점의 필압으로 반경 스케일 (포토샵과 동일한 감각)
        pressure = float(getattr(event, 'pressure', 1.0)) or 1.0
        if getattr(ip.brush, 'use_pressure_size', False):
            size_px *= max(pressure, 0.05)

        # 화면 좌표를 따라 도장 중심을 촘촘히 샘플 (마스크 max 합성이라 과밀 무해)
        dist = math.hypot(end[0] - start[0], end[1] - start[1])
        step = max(size_px * 0.25, 2.0)
        count = max(int(math.ceil(dist / step)), 1)

        mask = np.zeros((h, w), dtype=np.float32)
        last_radius = None
        stamped = 0
        for i in range(count + 1):
            t = i / count
            coord = (start[0] + (end[0] - start[0]) * t,
                     start[1] + (end[1] - start[1]) * t)
            uv = _screen_to_uv(context, region, coord)
            if uv is None:
                continue
            radius = _texel_radius_at(context, region, coord, size_px, w)
            if radius is None:
                radius = last_radius
            if radius is None or radius < 0.5:
                continue
            last_radius = radius
            self._stamp(mask, uv, radius, w, h)
            stamped += 1
        if stamped == 0:
            _set_anchor(region, end[0], end[1])
            return {'CANCELLED'}

        selected = _selected_map(context, w, h)
        if selected is not None:
            mask *= selected.astype(np.float32)

        alpha = mask * max(min(strength, 1.0), 0.0)

        # 브러시 색은 sRGB 그대로 (레이어 이미지 픽셀도 sRGB 인코딩)
        color = None
        try:
            from ..utils.unified_brushes import get_unified_settings
            owner = get_unified_settings(context, 'use_unified_color')
            if owner is not None:
                color = tuple(owner.color)
        except Exception:
            pass
        if color is None:
            color = tuple(ip.brush.color)

        buf = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(buf)
        rgba = buf.reshape(h, w, 4)
        src_a = rgba[:, :, 3]
        out_a = alpha + src_a * (1.0 - alpha)
        safe = np.maximum(out_a, 1e-6)
        for c in range(3):
            rgba[:, :, c] = (
                color[c] * alpha + rgba[:, :, c] * src_a * (1.0 - alpha)
            ) / safe
        rgba[:, :, 3] = out_a
        img.pixels.foreach_set(rgba.ravel())
        img.update()
        if hasattr(img, 'update_tag'):
            img.update_tag()
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()

        _set_anchor(region, end[0], end[1])
        return {'FINISHED'}

    @staticmethod
    def _stamp(mask: np.ndarray, uv, radius: float, w: int, h: int) -> None:
        """도장 하나를 마스크에 max 합성한다 (부드러운 원형 폴오프)."""
        cx, cy = uv[0] * w, uv[1] * h
        r = radius
        x0 = max(int(cx - r - 1), 0)
        x1 = min(int(cx + r + 2), w)
        y0 = max(int(cy - r - 1), 0)
        y1 = min(int(cy + r + 2), h)
        if x1 <= x0 or y1 <= y0:
            return
        ys, xs = np.mgrid[y0:y1, x0:x1]
        d = np.sqrt((xs + 0.5 - cx) ** 2 + (ys + 0.5 - cy) ** 2)
        inner = r * (1.0 - EDGE_SOFTNESS)
        a = np.clip((r - d) / max(r - inner, 1e-3), 0.0, 1.0)
        region = mask[y0:y1, x0:x1]
        np.maximum(region, a, out=region)


classes = (
    PAINTSYSTEM_OT_RecordStrokeAnchor,
    PAINTSYSTEM_OT_LineStroke,
)

register, unregister = bpy.utils.register_classes_factory(classes)
