# SPDX-License-Identifier: GPL-3.0-or-later
# 3DPainter 포크 추가 기능: 라쏘 선택 → 스텐실 마스크 (선택 영역 안에만 페인팅)
#
# v1은 2D 텍스처 뷰(Flat UV Mesh 캔버스) 전용이다 — 정사영 평면이라
# 화면→UV 매핑이 선형이어서 정확하다. 3D 뷰 투영은 후속 과제.

import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

import bpy
from bpy.types import Operator
from bpy_extras import view3d_utils

from .view2d_operators import get_canvas_object, get_source_object

MASK_IMAGE_NAME = "PS Selection Mask"

# 블렌더 스텐실 레이어는 기본적으로 흰색 영역을 가린다(masked).
# 선택 영역 내부를 1(흰색)로 굽고 invert를 켜서 "내부만 칠해짐"으로 만든다.
INVERT_STENCIL = True


def _region_to_uv(context, region, coord) -> tuple[float, float]:
    """2D 캔버스 뷰의 리전 좌표를 UV 좌표로 변환한다."""
    canvas = get_canvas_object(context.scene)
    r3d = region.data
    world = view3d_utils.region_2d_to_location_3d(
        region, r3d, coord, canvas.location)
    return (world.x - canvas.location.x, world.y - canvas.location.y)


def _active_image_size(context) -> tuple[int, int]:
    """현재 페인트 캔버스 이미지 크기 (없으면 2048)."""
    img = context.tool_settings.image_paint.canvas
    if img is not None and img.size[0] > 0:
        return int(img.size[0]), int(img.size[1])
    return 2048, 2048


def _rasterize_polygon(width: int, height: int, uv_points) -> np.ndarray:
    """UV 폴리곤 내부를 1로 채운 (H, W) float 마스크 — 짝홀 스캔라인."""
    mask = np.zeros((height, width), dtype=np.float32)
    pts = [(u * width, v * height) for u, v in uv_points]
    n = len(pts)
    if n < 3:
        return mask
    ys = [p[1] for p in pts]
    y0 = max(int(min(ys)), 0)
    y1 = min(int(max(ys)) + 1, height)
    for y in range(y0, y1):
        cy = y + 0.5
        xs = []
        for i in range(n):
            ax, ay = pts[i]
            bx, by = pts[(i + 1) % n]
            if (ay <= cy < by) or (by <= cy < ay):
                t = (cy - ay) / (by - ay)
                xs.append(ax + t * (bx - ax))
        xs.sort()
        for j in range(0, len(xs) - 1, 2):
            xa = max(int(np.ceil(xs[j] - 0.5)), 0)
            xb = min(int(np.floor(xs[j + 1] - 0.5)) + 1, width)
            if xb > xa:
                mask[y, xa:xb] = 1.0
    return mask


def _get_mask_pixels(img) -> np.ndarray:
    w, h = int(img.size[0]), int(img.size[1])
    buf = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(buf)
    return buf.reshape(h, w, 4)


def _set_mask_pixels(img, rgba: np.ndarray) -> None:
    img.pixels.foreach_set(rgba.astype(np.float32).ravel())
    img.update()


def _ensure_mask_image(width: int, height: int):
    img = bpy.data.images.get(MASK_IMAGE_NAME)
    if img is None or int(img.size[0]) != width or int(img.size[1]) != height:
        if img is not None:
            bpy.data.images.remove(img)
        img = bpy.data.images.new(
            MASK_IMAGE_NAME, width=width, height=height, alpha=False)
        img.colorspace_settings.name = 'Non-Color'
    return img


def _apply_stencil(context, mask_img) -> None:
    """스텐실 마스크를 툴 세팅과 관련 메시(원본+캔버스)에 건다."""
    ip = context.tool_settings.image_paint
    ip.use_stencil_layer = True
    ip.stencil_image = mask_img
    ip.invert_stencil = INVERT_STENCIL
    for obj in (get_source_object(context.scene), get_canvas_object(context.scene)):
        if obj is not None and obj.type == 'MESH':
            mesh = obj.data
            # 스텐실 UV = 페인팅에 쓰는 활성 UV
            mesh.uv_layer_stencil_index = mesh.uv_layers.active_index


class PAINTSYSTEM_OT_LassoSelect(Operator):
    """2D 뷰에서 라쏘로 선택 영역을 만든다 (+Alt: 선택에서 제외)"""
    bl_idname = "paint_system.lasso_select"
    bl_label = "Lasso Select"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        # 2D 뷰 밖에서도 매칭시켜 이벤트를 소비한다 — 조용히 페인팅되는 것 방지
        return (
            context.mode == 'PAINT_TEXTURE'
            and get_canvas_object(context.scene) is not None
            and context.space_data is not None
            and context.space_data.type == 'VIEW_3D'
        )

    def invoke(self, context, event):
        canvas = get_canvas_object(context.scene)
        space = context.space_data
        try:
            in_canvas_view = bool(space.local_view) and canvas.local_view_get(space)
        except (AttributeError, RuntimeError):
            in_canvas_view = False
        if not in_canvas_view:
            self.report({'WARNING'}, "라쏘 선택은 2D 뷰에서 사용하세요 (v1 제약)")
            return {'CANCELLED'}
        self._region_ptr = context.region.as_pointer()
        self._points = [(event.mouse_region_x, event.mouse_region_y)]
        # 트리거 콤보에 Ctrl/Shift가 포함되므로 Alt만 합성 모드 판정에 사용:
        # 기본 = 새 선택(REPLACE), +Alt = 기존 선택에서 제외(SUBTRACT)
        self._mode = 'SUBTRACT' if event.alt else 'REPLACE'
        self._handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw, (context,), 'WINDOW', 'POST_PIXEL')
        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'MOUSEMOVE':
            pt = (event.mouse_region_x, event.mouse_region_y)
            if abs(pt[0] - self._points[-1][0]) + abs(pt[1] - self._points[-1][1]) >= 2:
                self._points.append(pt)
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type in {'LEFTMOUSE'} and event.value == 'RELEASE':
            self._finish_draw(context)
            return self._commit(context)
        if event.type in {'RIGHTMOUSE', 'ESC'}:
            self._finish_draw(context)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def _draw(self, context):
        # 다른 3D 뷰포트에 잘못 그려지지 않도록 시작한 리전에서만 그린다
        region = bpy.context.region
        if region is None or region.as_pointer() != self._region_ptr:
            return
        if len(self._points) < 2:
            return
        # macOS Metal에서 LINE_LOOP + line_width가 불안정해 POLYLINE 셰이더 사용
        coords = [(p[0], p[1], 0.0) for p in self._points]
        coords.append(coords[0])  # 루프 닫기
        gpu.state.blend_set('ALPHA')
        try:
            shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
            batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": coords})
            shader.uniform_float("viewportSize", (region.width, region.height))
            shader.uniform_float("lineWidth", 2.0)
            shader.uniform_float("color", (1.0, 1.0, 1.0, 0.9))
        except (ValueError, KeyError):
            shader = gpu.shader.from_builtin('UNIFORM_COLOR')
            batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": coords})
            shader.uniform_float("color", (1.0, 1.0, 1.0, 0.9))
        batch.draw(shader)
        gpu.state.blend_set('NONE')

    def _finish_draw(self, context):
        if self._handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self._handle = None
        context.area.tag_redraw()

    def _commit(self, context):
        if len(self._points) < 3:
            return {'CANCELLED'}
        region = context.region
        uv_points = [_region_to_uv(context, region, p) for p in self._points]
        w, h = _active_image_size(context)
        poly = _rasterize_polygon(w, h, uv_points)

        mask_img = _ensure_mask_image(w, h)
        rgba = _get_mask_pixels(mask_img)
        if self._mode == 'REPLACE':
            new = poly
        elif self._mode == 'ADD':
            new = np.maximum(rgba[:, :, 0], poly)
        else:  # SUBTRACT
            new = np.clip(rgba[:, :, 0] - poly, 0.0, 1.0)
        rgba[:, :, 0] = new
        rgba[:, :, 1] = new
        rgba[:, :, 2] = new
        rgba[:, :, 3] = 1.0
        _set_mask_pixels(mask_img, rgba)
        _apply_stencil(context, mask_img)
        return {'FINISHED'}


class PAINTSYSTEM_OT_ClearSelection(Operator):
    """선택 영역을 해제한다 (스텐실 마스크 끄기)"""
    bl_idname = "paint_system.clear_selection"
    bl_label = "Clear Selection"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.tool_settings.image_paint.use_stencil_layer

    def execute(self, context):
        context.tool_settings.image_paint.use_stencil_layer = False
        return {'FINISHED'}


class PAINTSYSTEM_OT_InvertSelection(Operator):
    """선택 영역을 반전한다"""
    bl_idname = "paint_system.invert_selection"
    bl_label = "Invert Selection"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ip = context.tool_settings.image_paint
        return ip.use_stencil_layer and ip.stencil_image is not None

    def execute(self, context):
        img = context.tool_settings.image_paint.stencil_image
        rgba = _get_mask_pixels(img)
        rgba[:, :, :3] = 1.0 - rgba[:, :, :3]
        _set_mask_pixels(img, rgba)
        return {'FINISHED'}


classes = (
    PAINTSYSTEM_OT_LassoSelect,
    PAINTSYSTEM_OT_ClearSelection,
    PAINTSYSTEM_OT_InvertSelection,
)

register, unregister = bpy.utils.register_classes_factory(classes)
