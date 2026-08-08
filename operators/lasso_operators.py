# SPDX-License-Identifier: GPL-3.0-or-later
# 3DPainter 포크 추가 기능: 라쏘 선택 → 스텐실 마스크 (포토샵식 — 선택 안쪽만 페인팅)
#
# 인코딩: 스텐실 이미지 = "차단 맵" (1=페인팅 차단). 선택 영역 안쪽 = 0.
# 블렌더 스텐실 표시가 차단 영역(선택 바깥)을 어둡게 틴트하므로
# "선택 밖은 보호됨"이라는 포토샵과 유사한 시각 피드백이 된다.
# v1은 2D 텍스처 뷰(Flat UV Mesh 캔버스) 전용.

import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

import bpy
from bpy.types import Operator
from bpy_extras import view3d_utils

from .view2d_operators import get_canvas_object, get_source_object

MASK_IMAGE_NAME = "PS Selection Mask"

# 커밋 후에도 유지되는 선택 윤곽선 (세션 한정)
_outline = {"polys": [], "handle": None}


def _draw_polyline(region, coords, color) -> None:
    """닫힌 폴리라인을 그린다 — macOS Metal 호환 POLYLINE 셰이더 우선."""
    gpu.state.blend_set('ALPHA')
    try:
        shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": coords})
        shader.uniform_float("viewportSize", (region.width, region.height))
        shader.uniform_float("lineWidth", 2.0)
        shader.uniform_float("color", color)
    except (ValueError, KeyError):
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": coords})
        shader.uniform_float("color", color)
    batch.draw(shader)
    gpu.state.blend_set('NONE')


def _in_canvas_view(space, canvas) -> bool:
    try:
        return bool(space.local_view) and canvas.local_view_get(space)
    except (AttributeError, RuntimeError):
        return False


def _set_stencil_overlay(context, visible: bool) -> None:
    """스텐실 오버레이(차단 영역을 어둡게 틴트)를 켜거나 끈다.

    포토샵은 선택 밖을 어둡게 표시하지 않으므로 기본은 끔 — 마스킹 기능은
    그대로 동작하고, 시각 피드백은 점선 윤곽선이 담당한다.
    """
    for area in context.screen.areas:
        if area.type != 'VIEW_3D':
            continue
        overlay = area.spaces.active.overlay
        if hasattr(overlay, 'texture_paint_mode_opacity'):
            overlay.texture_paint_mode_opacity = 1.0 if visible else 0.0


def _draw_dashed(coords, dash: float = 5.0):
    """닫힌 폴리라인을 점선 세그먼트 두 묶음(흑/백 교차)으로 나눈다."""
    import math
    segs_white, segs_black = [], []
    acc = 0.0
    for i in range(len(coords) - 1):
        ax, ay = coords[i][0], coords[i][1]
        bx, by = coords[i + 1][0], coords[i + 1][1]
        seg_len = math.hypot(bx - ax, by - ay)
        if seg_len < 1e-6:
            continue
        dx, dy = (bx - ax) / seg_len, (by - ay) / seg_len
        t = 0.0
        while t < seg_len:
            t2 = min(t + dash, seg_len)
            pair = (
                (ax + dx * t, ay + dy * t, 0.0),
                (ax + dx * t2, ay + dy * t2, 0.0),
            )
            if int((acc + t) / dash) % 2 == 0:
                segs_white.extend(pair)
            else:
                segs_black.extend(pair)
            t = t2
        acc += seg_len
    return segs_white, segs_black


def _draw_marching_ants(coords) -> None:
    """포토샵식 흑백 교차 점선 (1px, Metal 호환)."""
    segs_white, segs_black = _draw_dashed(coords)
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    for segs, color in ((segs_black, (0.0, 0.0, 0.0, 1.0)),
                        (segs_white, (1.0, 1.0, 1.0, 1.0))):
        if not segs:
            continue
        batch = batch_for_shader(shader, 'LINES', {"pos": segs})
        shader.uniform_float("color", color)
        batch.draw(shader)
    gpu.state.blend_set('NONE')


def _outline_draw():
    ctx = bpy.context
    region = ctx.region
    space = ctx.space_data
    scene = ctx.scene
    if region is None or space is None or scene is None:
        return
    if getattr(space, 'type', None) != 'VIEW_3D':
        return
    canvas = get_canvas_object(scene)
    if canvas is None or not _in_canvas_view(space, canvas):
        return
    rv3d = region.data
    loc = canvas.location
    for mode, uv_pts in _outline["polys"]:
        coords = []
        for u, v in uv_pts:
            p2 = view3d_utils.location_3d_to_region_2d(
                region, rv3d, (loc.x + u, loc.y + v, loc.z))
            if p2 is None:
                coords = []
                break
            coords.append((p2.x, p2.y, 0.0))
        if len(coords) < 3:
            continue
        coords.append(coords[0])
        _draw_marching_ants(coords)


def _outline_ensure_handler() -> None:
    if _outline["handle"] is None:
        _outline["handle"] = bpy.types.SpaceView3D.draw_handler_add(
            _outline_draw, (), 'WINDOW', 'POST_PIXEL')


def _outline_clear() -> None:
    if _outline["handle"] is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_outline["handle"], 'WINDOW')
        except ValueError:
            pass
        _outline["handle"] = None
    _outline["polys"].clear()


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


def _dilate3(m: np.ndarray) -> np.ndarray:
    """3x3 최대값 필터 — 희소 텍셀 마크의 구멍 메움(클로징)용."""
    pad = np.pad(m, 1, mode='constant')
    out = pad[1:-1, 1:-1].copy()
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            if dy == 1 and dx == 1:
                continue
            np.maximum(out, pad[dy:dy + m.shape[0], dx:dx + m.shape[1]], out=out)
    return out


_uv_shader_cache = {}


def _get_uv_shader():
    """UV를 색으로 출력하는 셰이더 (지연 생성 — GPU 컨텍스트 필요)."""
    if 'shader' not in _uv_shader_cache:
        from gpu.types import GPUShaderCreateInfo, GPUStageInterfaceInfo
        iface = GPUStageInterfaceInfo("ps_uv_iface")
        iface.smooth('VEC2', "uvInterp")
        info = GPUShaderCreateInfo()
        info.push_constant('MAT4', "mvp")
        info.vertex_in(0, 'VEC3', "pos")
        info.vertex_in(1, 'VEC2', "uv")
        info.vertex_out(iface)
        info.fragment_out(0, 'VEC4', "fragColor")
        info.vertex_source(
            "void main() { uvInterp = uv;"
            " gl_Position = mvp * vec4(pos, 1.0); }")
        info.fragment_source(
            "void main() { fragColor = vec4(uvInterp, 1.0, 1.0); }")
        _uv_shader_cache['shader'] = gpu.shader.create_from_info(info)
    return _uv_shader_cache['shader']


def _mesh_uv_batch(obj, shader):
    """오브젝트의 삼각화된 (pos, uv) 배치를 만든다."""
    mesh = obj.data
    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        return None
    mesh.calc_loop_triangles()
    n_loops = len(mesh.loops)
    loop_v = np.empty(n_loops, dtype=np.int32)
    mesh.loops.foreach_get('vertex_index', loop_v)
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get('co', co)
    co = co.reshape(-1, 3)
    uvs = np.empty(n_loops * 2, dtype=np.float32)
    uv_layer.data.foreach_get('uv', uvs)
    uvs = uvs.reshape(-1, 2)
    n_tris = len(mesh.loop_triangles)
    tl = np.empty(n_tris * 3, dtype=np.int32)
    mesh.loop_triangles.foreach_get('loops', tl)
    return batch_for_shader(
        shader, 'TRIS',
        {"pos": co[loop_v[tl]], "uv": uvs[tl]})


def _poly_from_3d_view(context, region, points, w: int, h: int):
    """3D 뷰 라쏘: UV를 오프스크린에 렌더해 화면 폴리곤 → 텍스처 마스크 변환.

    깊이 테스트로 가려진 면은 자동 제외된다 (보이는 면만 선택).
    """
    obj = context.view_layer.objects.active
    if obj is None or obj.type != 'MESH' or obj.data.uv_layers.active is None:
        return None
    shader = _get_uv_shader()
    batch = _mesh_uv_batch(obj, shader)
    if batch is None:
        return None
    rw, rh = region.width, region.height
    rv3d = region.data
    mvp = rv3d.window_matrix @ rv3d.view_matrix @ obj.matrix_world

    offscreen = gpu.types.GPUOffScreen(rw, rh, format='RGBA32F')
    try:
        with offscreen.bind():
            fb = gpu.state.active_framebuffer_get()
            fb.clear(color=(0.0, 0.0, 0.0, 0.0), depth=1.0)
            gpu.state.depth_test_set('LESS')
            gpu.state.depth_mask_set(True)
            shader.uniform_float("mvp", mvp)
            batch.draw(shader)
            gpu.state.depth_test_set('NONE')
            gpu.state.depth_mask_set(False)
            buf = fb.read_color(0, 0, rw, rh, 4, 0, 'FLOAT')
    finally:
        offscreen.free()
    buf.dimensions = rw * rh * 4
    uvbuf = np.array(buf, dtype=np.float32).reshape(rh, rw, 4)

    # 화면 공간에서 라쏘 내부 + 메시가 렌더된 픽셀만 취한다
    norm_pts = [(p[0] / rw, p[1] / rh) for p in points]
    screen_mask = _rasterize_polygon(rw, rh, norm_pts) > 0.5
    valid = (uvbuf[:, :, 2] > 0.5) & screen_mask
    if not valid.any():
        return None
    us = uvbuf[:, :, 0][valid]
    vs = uvbuf[:, :, 1][valid]
    xs = np.clip((us * w).astype(np.int32), 0, w - 1)
    ys = np.clip((vs * h).astype(np.int32), 0, h - 1)
    poly = np.zeros((h, w), dtype=np.float32)
    poly[ys, xs] = 1.0
    # 화면 픽셀보다 텍셀이 촘촘하면 마크가 희소해진다 → 클로징으로 메움
    for _ in range(3):
        poly = _dilate3(poly)
    inv = 1.0 - poly
    for _ in range(3):
        inv = _dilate3(inv)
    return 1.0 - inv


def _box3(m: np.ndarray) -> np.ndarray:
    """3x3 박스 블러 — 라쏘 외곽 안티앨리어스/페더용."""
    pad = np.pad(m, 1, mode='edge')
    return (
        pad[0:-2, 0:-2] + pad[0:-2, 1:-1] + pad[0:-2, 2:]
        + pad[1:-1, 0:-2] + pad[1:-1, 1:-1] + pad[1:-1, 2:]
        + pad[2:, 0:-2] + pad[2:, 1:-1] + pad[2:, 2:]
    ) / 9.0


def _get_mask_pixels(img) -> np.ndarray:
    w, h = int(img.size[0]), int(img.size[1])
    buf = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(buf)
    return buf.reshape(h, w, 4)


def _set_masked_map(img, masked: np.ndarray) -> None:
    """차단 맵(1=차단)을 RGB와 알파에 동일하게 기록한다.

    스텐실이 어떤 채널을 읽든 일관되게 동작하도록 전 채널을 맞춘다.
    """
    h, w = masked.shape
    rgba = np.empty((h, w, 4), dtype=np.float32)
    rgba[:, :, 0] = masked
    rgba[:, :, 1] = masked
    rgba[:, :, 2] = masked
    rgba[:, :, 3] = masked
    img.pixels.foreach_set(rgba.ravel())
    img.update()


def _ensure_mask_image(width: int, height: int):
    img = bpy.data.images.get(MASK_IMAGE_NAME)
    if img is None or int(img.size[0]) != width or int(img.size[1]) != height:
        if img is not None:
            bpy.data.images.remove(img)
        img = bpy.data.images.new(
            MASK_IMAGE_NAME, width=width, height=height, alpha=True)
        img.colorspace_settings.name = 'Non-Color'
    return img


def _apply_stencil(context, mask_img) -> None:
    """스텐실 마스크를 툴 세팅과 관련 메시(원본+캔버스)에 건다."""
    ip = context.tool_settings.image_paint
    ip.use_stencil_layer = True
    ip.stencil_image = mask_img
    # 이미지 자체가 "차단 맵"이므로 반전 없이 사용한다
    ip.invert_stencil = False
    for obj in (get_source_object(context.scene), get_canvas_object(context.scene)):
        if obj is not None and obj.type == 'MESH':
            mesh = obj.data
            # 스텐실 UV = 페인팅에 쓰는 활성 UV
            mesh.uv_layer_stencil_index = mesh.uv_layers.active_index
    # 포토샵처럼 화면 틴트(검게 표시) 없이 마스킹만 동작시킨다
    _set_stencil_overlay(context, False)


def _commit_selection(op, context, region, screen_points, mode):
    """화면 폴리곤을 선택 마스크로 커밋한다 — 2D 캔버스/3D 뷰 공용."""
    if len(screen_points) < 3:
        return {'CANCELLED'}
    canvas = get_canvas_object(context.scene)
    space = context.space_data
    w, h = _active_image_size(context)
    in_canvas = canvas is not None and _in_canvas_view(space, canvas)

    uv_points = None
    if in_canvas:
        uv_points = [_region_to_uv(context, region, p) for p in screen_points]
        poly = _rasterize_polygon(w, h, uv_points)
    else:
        poly = _poly_from_3d_view(context, region, screen_points, w, h)
        if poly is None:
            op.report({'WARNING'}, "선택할 표면이 없습니다 — UV가 있는 메시 위에서 사용하세요")
            return {'CANCELLED'}
    # 외곽 안티앨리어스 + 약한 페더 (~2px)
    poly = _box3(_box3(poly))

    mask_img = _ensure_mask_image(w, h)
    ip = context.tool_settings.image_paint
    if ip.use_stencil_layer and ip.stencil_image == mask_img:
        prev_masked = _get_mask_pixels(mask_img)[:, :, 0]
    else:
        prev_masked = np.ones((h, w), dtype=np.float32)  # 선택 없음 = 전면 차단

    if mode == 'SUBTRACT':
        masked = np.maximum(prev_masked, poly)
    else:  # REPLACE
        masked = 1.0 - poly

    selected_ratio = float(1.0 - masked.mean())
    if selected_ratio <= 0.0:
        op.report({'WARNING'}, "선택 영역이 비어 있습니다")
        return {'CANCELLED'}

    _set_masked_map(mask_img, masked)
    _apply_stencil(context, mask_img)

    # 선택 윤곽선(점선)은 UV 좌표가 확정되는 2D 캔버스 뷰에서만 유지 표시
    if uv_points is not None:
        if mode == 'REPLACE':
            _outline["polys"] = [(mode, uv_points)]
        else:
            _outline["polys"].append((mode, uv_points))
        _outline_ensure_handler()
    elif mode == 'REPLACE':
        _outline["polys"].clear()
    context.area.tag_redraw()
    op.report({'INFO'}, f"선택 영역: 텍스처의 {selected_ratio * 100.0:.1f}%")
    return {'FINISHED'}


class PAINTSYSTEM_OT_LassoSelect(Operator):
    """자유곡선 라쏘 선택 — 2D 캔버스·3D 뷰 모두 지원 (+Alt: 선택에서 제외)"""
    bl_idname = "paint_system.lasso_select"
    bl_label = "Lasso Select"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (
            context.mode == 'PAINT_TEXTURE'
            and context.space_data is not None
            and context.space_data.type == 'VIEW_3D'
        )

    def invoke(self, context, event):
        if context.region is None or context.region.data is None:
            return {'CANCELLED'}
        self._region_ptr = context.region.as_pointer()
        self._points = [(event.mouse_region_x, event.mouse_region_y)]
        # 트리거 콤보에 Ctrl/Shift가 포함되므로 Alt만 합성 모드 판정에 사용
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
        coords = [(p[0], p[1], 0.0) for p in self._points]
        coords.append(coords[0])  # 루프 닫기
        color = (1.0, 0.4, 0.4, 0.9) if self._mode == 'SUBTRACT' else (1.0, 1.0, 1.0, 0.9)
        _draw_polyline(region, coords, color)

    def _finish_draw(self, context):
        if self._handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self._handle = None
        context.area.tag_redraw()

    def _commit(self, context):
        return _commit_selection(
            self, context, context.region, self._points, self._mode)


class PAINTSYSTEM_OT_PolyLassoSelect(Operator):
    """다각형 라쏘 선택 — 클릭으로 꼭짓점 추가, 시작점 클릭/Enter로 닫기,
    Backspace로 마지막 점 취소, ESC/우클릭 취소 (+Alt 시작: 선택에서 제외)"""
    bl_idname = "paint_system.poly_lasso_select"
    bl_label = "Polygon Lasso Select"
    bl_options = {'REGISTER', 'UNDO'}

    CLOSE_RADIUS = 12.0  # 시작점 클릭 판정 반경 (px)

    @classmethod
    def poll(cls, context):
        return (
            context.mode == 'PAINT_TEXTURE'
            and context.space_data is not None
            and context.space_data.type == 'VIEW_3D'
        )

    def invoke(self, context, event):
        if context.region is None or context.region.data is None:
            return {'CANCELLED'}
        self._region_ptr = context.region.as_pointer()
        pt = (float(event.mouse_region_x), float(event.mouse_region_y))
        self._points = [pt]
        self._preview = pt
        self._mode = 'SUBTRACT' if event.alt else 'REPLACE'
        self._handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw, (context,), 'WINDOW', 'POST_PIXEL')
        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        import math
        if event.type == 'MOUSEMOVE':
            self._preview = (float(event.mouse_region_x), float(event.mouse_region_y))
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            pt = (float(event.mouse_region_x), float(event.mouse_region_y))
            first = self._points[0]
            near_start = (
                len(self._points) >= 3
                and math.hypot(pt[0] - first[0], pt[1] - first[1]) <= self.CLOSE_RADIUS
            )
            if near_start:
                self._finish_draw(context)
                return _commit_selection(
                    self, context, context.region, self._points, self._mode)
            self._points.append(pt)
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            self._finish_draw(context)
            return _commit_selection(
                self, context, context.region, self._points, self._mode)
        if event.type == 'BACK_SPACE' and event.value == 'PRESS':
            if len(self._points) > 1:
                self._points.pop()
                context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type in {'RIGHTMOUSE', 'ESC'}:
            self._finish_draw(context)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def _draw(self, context):
        region = bpy.context.region
        if region is None or region.as_pointer() != self._region_ptr:
            return
        coords = [(p[0], p[1], 0.0) for p in self._points]
        coords.append((self._preview[0], self._preview[1], 0.0))
        if len(coords) >= 3:
            coords.append(coords[0])  # 닫힘 미리보기
        if len(coords) < 2:
            return
        _draw_polyline(region, coords, (1.0, 1.0, 1.0, 0.9))
        # 시작점 표시 (닫기 판정 반경)
        first = coords[0]
        r = self.CLOSE_RADIUS
        square = [
            (first[0] - r, first[1] - r, 0.0), (first[0] + r, first[1] - r, 0.0),
            (first[0] + r, first[1] + r, 0.0), (first[0] - r, first[1] + r, 0.0),
            (first[0] - r, first[1] - r, 0.0),
        ]
        _draw_polyline(region, square, (1.0, 1.0, 1.0, 0.4))

    def _finish_draw(self, context):
        if self._handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self._handle = None
        context.area.tag_redraw()


class PAINTSYSTEM_OT_FillSelection(Operator):
    """선택 영역을 브러시 색으로 채운다 (포토샵 Alt+Backspace).
    선택이 없으면 활성 레이어 전체를 채운다"""
    bl_idname = "paint_system.fill_selection"
    bl_label = "Fill with Brush Color"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (
            context.mode == 'PAINT_TEXTURE'
            and context.tool_settings.image_paint.canvas is not None
        )

    def execute(self, context):
        import mathutils

        ip = context.tool_settings.image_paint
        img = ip.canvas
        w, h = int(img.size[0]), int(img.size[1])
        if w == 0 or h == 0:
            return {'CANCELLED'}

        # 브러시 색 (unified 설정 우선) → 이미지 픽셀은 리니어라 변환 필요
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
        # 브러시 색은 리니어 값이고 레이어 이미지 픽셀 배열은 sRGB 인코딩이므로
        # 리니어→sRGB 인코딩을 거쳐야 브러시 스트로크와 같은 색이 된다
        fill_rgb = mathutils.Color(color)
        if hasattr(fill_rgb, 'from_scene_linear_to_srgb'):
            fill_rgb = fill_rgb.from_scene_linear_to_srgb()

        # 선택 영역: 스텐실(차단 맵)의 반전을 연속값(0~1)으로 사용 —
        # 페더된 외곽이 부드럽게 섞이도록 소프트 알파 합성한다
        selected = None
        if ip.use_stencil_layer and ip.stencil_image is not None:
            mask_img = ip.stencil_image
            mh, mw = int(mask_img.size[1]), int(mask_img.size[0])
            masked = _get_mask_pixels(mask_img)[:, :, 0]
            if (mh, mw) != (h, w):  # 최근접 리샘플
                ys = (np.arange(h) * mh // h).clip(0, mh - 1)
                xs = (np.arange(w) * mw // w).clip(0, mw - 1)
                masked = masked[np.ix_(ys, xs)]
            selected = np.clip(1.0 - masked, 0.0, 1.0)

        buf = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(buf)
        rgba = buf.reshape(h, w, 4)
        fill = np.array([fill_rgb.r, fill_rgb.g, fill_rgb.b, 1.0], dtype=np.float32)
        if selected is None:
            rgba[:, :, :] = fill
        else:
            a = selected
            src_a = rgba[:, :, 3]
            out_a = a + src_a * (1.0 - a)
            safe = np.maximum(out_a, 1e-6)
            for c in range(3):
                rgba[:, :, c] = (
                    fill[c] * a + rgba[:, :, c] * src_a * (1.0 - a)
                ) / safe
            rgba[:, :, 3] = out_a
        img.pixels.foreach_set(rgba.ravel())
        img.update()
        # 뎁스그래프에 변경을 통지해야 EEVEE가 GPU 텍스처를 재업로드한다
        # (없으면 다음 뎁스그래프 자극 때까지 2D/3D 뷰 갱신이 밀린다)
        if hasattr(img, 'update_tag'):
            img.update_tag()
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
        return {'FINISHED'}


class PAINTSYSTEM_OT_DeselectOnEmptyClick(Operator):
    """2D 뷰의 캔버스 밖 빈 공간을 클릭하면 선택을 해제한다 (포토샵과 동일).
    이벤트는 그대로 통과시킨다"""
    bl_idname = "paint_system.deselect_on_empty_click"
    bl_label = "Deselect on Empty Click"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return (
            context.mode == 'PAINT_TEXTURE'
            and context.tool_settings.image_paint.use_stencil_layer
            and get_canvas_object(context.scene) is not None
        )

    def invoke(self, context, event):
        space = context.space_data
        canvas = get_canvas_object(context.scene)
        if (
            space is not None and space.type == 'VIEW_3D'
            and context.region is not None
            and _in_canvas_view(space, canvas)
        ):
            u, v = _region_to_uv(
                context, context.region,
                (event.mouse_region_x, event.mouse_region_y))
            if not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0):
                bpy.ops.paint_system.clear_selection()
        return {'PASS_THROUGH'}


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
        _outline_clear()
        _set_stencil_overlay(context, True)
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
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
        masked = _get_mask_pixels(img)[:, :, 0]
        _set_masked_map(img, 1.0 - masked)
        return {'FINISHED'}


class PS_ToolLassoSelect(bpy.types.WorkSpaceTool):
    """텍스처 페인트 툴바의 자유곡선 라쏘 툴."""
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'PAINT_TEXTURE'
    bl_idname = "paint_system.lasso_select_tool"
    bl_label = "PS Lasso"
    bl_description = "라쏘 선택 — 드래그로 자유곡선, Alt=선택에서 제외"
    bl_icon = "ops.generic.select_lasso"
    bl_keymap = (
        ("paint_system.lasso_select",
         {"type": 'LEFTMOUSE', "value": 'PRESS'}, None),
        ("paint_system.lasso_select",
         {"type": 'LEFTMOUSE', "value": 'PRESS', "alt": True}, None),
    )


class PS_ToolPolyLassoSelect(bpy.types.WorkSpaceTool):
    """텍스처 페인트 툴바의 다각형 라쏘 툴."""
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'PAINT_TEXTURE'
    bl_idname = "paint_system.poly_lasso_select_tool"
    bl_label = "PS Polygon Lasso"
    bl_description = (
        "다각형 라쏘 — 클릭으로 꼭짓점, 시작점 클릭/Enter로 닫기, "
        "Backspace로 점 취소, Alt=선택에서 제외")
    bl_icon = "ops.generic.select_lasso"
    bl_keymap = (
        ("paint_system.poly_lasso_select",
         {"type": 'LEFTMOUSE', "value": 'PRESS'}, None),
        ("paint_system.poly_lasso_select",
         {"type": 'LEFTMOUSE', "value": 'PRESS', "alt": True}, None),
    )


classes = (
    PAINTSYSTEM_OT_LassoSelect,
    PAINTSYSTEM_OT_PolyLassoSelect,
    PAINTSYSTEM_OT_FillSelection,
    PAINTSYSTEM_OT_DeselectOnEmptyClick,
    PAINTSYSTEM_OT_ClearSelection,
    PAINTSYSTEM_OT_InvertSelection,
)

_register, _unregister = bpy.utils.register_classes_factory(classes)


def register():
    _register()
    try:
        bpy.utils.register_tool(
            PS_ToolLassoSelect, after={"builtin_brush.paint"},
            separator=True, group=True)
        bpy.utils.register_tool(
            PS_ToolPolyLassoSelect, after={PS_ToolLassoSelect.bl_idname})
    except Exception:
        # 툴바 등록 실패는 치명적이지 않음 (키맵으로 계속 사용 가능)
        pass


def unregister():
    for tool in (PS_ToolPolyLassoSelect, PS_ToolLassoSelect):
        try:
            bpy.utils.unregister_tool(tool)
        except Exception:
            pass
    _outline_clear()
    _unregister()
