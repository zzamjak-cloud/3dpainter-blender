# SPDX-License-Identifier: GPL-3.0-or-later
"""스포이드(컬러 샘플링) 오퍼레이터.

블렌더 네이티브 ``paint.sample_color`` 는 3D 뷰에서 두 가지 모드밖에 없다.

* ``merged=True``  — 화면 픽셀을 그대로 읽는다. 조명·그림자·톤매핑이 섞인다.
* ``merged=False`` — ``image_paint.canvas`` 한 장만 UV 로 샘플링한다.

Paint System 은 ``canvas`` 를 **활성 레이어의 이미지 한 장**으로 고정하므로
(``paintsystem/layer.py`` 의 ``update_active_image``), 초기 세팅처럼 활성 레이어가
아직 비어 있는(알파 0) 상태에서 ``merged=False`` 로 찍으면 투명 픽셀의 RGB 인
(0, 0, 0) — 즉 항상 검은색이 집힌다.

그래서 여기서는 직접 샘플링한다.

1. 마우스 아래 표면을 레이캐스트해 UV 를 구한다.
2. 활성 채널의 레이어 스택을 위에서 아래로 알파 합성해 "음영 없는 순수 색"을 얻는다.
3. 스택 전체가 투명하면 화면 합성 픽셀(``merged=True``)로 폴백한다 — 검은색 대신
   최소한 사용자가 보고 있는 색을 준다.
"""

import sys

import bpy
from bpy.props import IntProperty
from bpy.types import Operator
from bpy_extras import view3d_utils
from mathutils import Vector
from mathutils.geometry import barycentric_transform, intersect_point_tri

from bl_ui.properties_paint_common import UnifiedPaintPanel

from ..utils.registration import collect_classes
from ..utils.unified_brushes import get_unified_settings
from ..utils.logging import get_logger
from .common import PSContextMixin, hidden_faces_mask as _hidden_faces_mask

logger = get_logger(__name__)

# 스택 합성을 끝낼 누적 알파 임계값
_OPAQUE_EPS = 0.999
# 이 값보다 알파가 낮으면 "아무것도 칠해지지 않았다"고 본다
_TRANSPARENT_EPS = 1e-4
# 숨긴 페이스를 뚫고 다시 쏠 때의 시작점 오프셋과 최대 재시도 횟수
_RAY_SKIP_EPS = 1e-4
_RAY_MAX_HOPS = 64


def _ray_cast_visible(obj_eval, mesh, origin, direction):
    """숨긴 페이스(``.hide_poly``)는 건너뛰고 보이는 페이스를 맞힌다.

    ``Object.ray_cast`` 는 숨김 상태를 모르므로, 숨긴 페이스에 맞으면 그 지점
    바로 뒤에서 다시 쏜다 — 디테일 작업용으로 앞면을 숨겼을 때 스포이드가
    숨긴 면의 색을 집어 오는 문제를 막는다.
    """
    hidden = _hidden_faces_mask(mesh)
    origin = Vector(origin)
    for _ in range(_RAY_MAX_HOPS):
        hit, location, normal, face_index = obj_eval.ray_cast(origin, direction)
        if not hit:
            return False, None, None, -1
        if hidden is None or face_index < 0 or face_index >= len(hidden) or not hidden[face_index]:
            return hit, location, normal, face_index
        origin = location + direction * _RAY_SKIP_EPS
    return False, None, None, -1


def _srgb_to_linear(value: float) -> float:
    """sRGB 로 인코딩된 0..1 값을 씬 선형 값으로 변환한다."""
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def _sample_image_rgba(image: bpy.types.Image, u: float, v: float):
    """이미지의 (u, v) 픽셀을 선형 색공간 RGBA 튜플로 읽는다. 실패하면 None.

    ``image.pixels`` 접근 한 번마다 RNA 가 float 버퍼를 확보하므로(2048² 기준 약
    12ms) 성분을 하나씩 읽지 말고 **슬라이스로 4개를 한 번에** 가져와야 한다.
    """
    if image is None or not image.has_data:
        return None
    try:
        width, height = image.size
        if width <= 0 or height <= 0:
            return None
        pixels = image.pixels
        if len(pixels) < width * height * 4:
            return None
        # UV 반복(타일링)에 맞춰 래핑한다
        x = int(u * width) % width
        y = int(v * height) % height
        index = (y * width + x) * 4
        r, g, b, a = pixels[index:index + 4]
    except (RuntimeError, IndexError, ValueError, AttributeError):
        # UDIM 타일 이미지 등 pixels 접근이 불가능한 경우
        return None

    # 8비트 버퍼는 sRGB 로 인코딩된 값이 그대로 나오므로 선형으로 되돌린다.
    # (float 버퍼는 블렌더가 이미 씬 선형으로 보관한다)
    if not image.is_float and image.colorspace_settings.name in {'sRGB', 'Filmic sRGB'}:
        r, g, b = _srgb_to_linear(r), _srgb_to_linear(g), _srgb_to_linear(b)
    return (r, g, b, a)


def _layer_rgba(layer, u: float, v: float):
    """레이어 한 장의 (u, v) 색을 RGBA 로 구한다. 색을 알 수 없으면 None.

    노드 그래프 평가가 필요한 타입(조정·그라디언트·텍스처 등)은 건너뛴다 —
    스포이드는 아래 레이어의 실제 색을 집는 쪽이 페인팅에 유용하다.
    """
    layer_type = getattr(layer, 'type', None)
    if layer_type == 'IMAGE':
        return _sample_image_rgba(layer.image, u, v)
    if layer_type == 'SOLID_COLOR':
        node = layer.source_node
        if node is None or not node.outputs:
            return None
        value = node.outputs[0].default_value
        return (value[0], value[1], value[2], 1.0)
    return None


def _layer_opacity(layer) -> float:
    """레이어 불투명도. 노드가 아직 없으면 1.0 으로 본다."""
    try:
        return max(0.0, min(1.0, float(layer.opacity)))
    except (AttributeError, TypeError, KeyError):
        return 1.0


def composite_layer_stack(channel, u: float, v: float):
    """채널의 레이어 스택을 위에서 아래로 알파 합성해 순수 색 RGB 를 구한다.

    블렌드 모드는 무시하고 알파 오버로만 합성한다 — 스포이드가 필요로 하는 건
    "여기 칠해진 색" 이지 최종 셰이딩 결과가 아니기 때문이다.
    완전히 투명해서 집을 색이 없으면 None 을 돌려준다.
    """
    if channel is None:
        return None
    try:
        layers = channel.flattened_layers
    except (AttributeError, RuntimeError):
        return None

    acc_r = acc_g = acc_b = acc_a = 0.0
    for layer in layers:
        if layer is None or not getattr(layer, 'enabled', True):
            continue
        rgba = _layer_rgba(layer, u, v)
        if rgba is None:
            continue
        alpha = rgba[3] * _layer_opacity(layer)
        if alpha <= 0.0:
            continue
        weight = (1.0 - acc_a) * alpha
        acc_r += rgba[0] * weight
        acc_g += rgba[1] * weight
        acc_b += rgba[2] * weight
        acc_a += weight
        if acc_a >= _OPAQUE_EPS:
            break

    if acc_a <= _TRANSPARENT_EPS:
        return None
    # 프리멀티플라이 해제 — 반투명 레이어에서도 원래 색이 어두워지지 않게 한다
    return (acc_r / acc_a, acc_g / acc_a, acc_b / acc_a)


def uv_under_cursor(context, region, coord):
    """리전 좌표 아래 표면의 UV 를 구한다. 맞은 게 없으면 None.

    모디파이어가 적용된 지오메트리와 인덱스를 맞추려고 평가된 오브젝트를 쓴다.
    """
    obj = context.view_layer.objects.active
    if obj is None or obj.type != 'MESH':
        return None
    rv3d = getattr(region, 'data', None)
    if rv3d is None:
        return None

    depsgraph = context.evaluated_depsgraph_get()
    obj_eval = obj.evaluated_get(depsgraph)
    mesh = obj_eval.data
    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        return None

    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
    matrix_inv = obj_eval.matrix_world.inverted()
    local_origin = matrix_inv @ origin
    local_dir = (matrix_inv.to_3x3() @ direction).normalized()

    hit, location, _normal, face_index = _ray_cast_visible(
        obj_eval, mesh, local_origin, local_dir)
    if not hit or face_index < 0 or face_index >= len(mesh.polygons):
        return None

    loop_indices = list(mesh.polygons[face_index].loop_indices)
    if len(loop_indices) < 3:
        return None
    verts = [mesh.vertices[mesh.loops[i].vertex_index].co for i in loop_indices]
    uvs = [uv_layer.data[i].uv for i in loop_indices]

    # 폴리곤을 팬 삼각분할해 히트 지점을 포함하는 삼각형을 찾는다
    fallback = None
    for i in range(1, len(verts) - 1):
        tri = (verts[0], verts[i], verts[i + 1])
        tri_uv = (
            Vector((uvs[0][0], uvs[0][1], 0.0)),
            Vector((uvs[i][0], uvs[i][1], 0.0)),
            Vector((uvs[i + 1][0], uvs[i + 1][1], 0.0)),
        )
        if fallback is None:
            fallback = (tri, tri_uv)
        if intersect_point_tri(location, *tri):
            result = barycentric_transform(location, *tri, *tri_uv)
            return (result.x, result.y)

    if fallback is None:
        return None
    # 부동소수 오차로 어느 삼각형에도 안 걸리면 첫 삼각형 기준으로 근사한다
    result = barycentric_transform(location, *fallback[0], *fallback[1])
    return (result.x, result.y)


def apply_brush_color(context, color) -> None:
    """샘플링한 색을 브러시(및 통합 색상)에 반영한다."""
    tool_settings = UnifiedPaintPanel.paint_settings(context)
    brush = tool_settings.brush if tool_settings else None
    if brush is not None:
        brush.color = color
    unified_settings = get_unified_settings(context, "use_unified_color")
    if unified_settings is not None:
        unified_settings.color = color
    context.scene.ps_scene_data.update_hsv_color(context)


class PAINTSYSTEM_OT_ColorSample(PSContextMixin, Operator):
    """Sample the color under the mouse cursor"""
    bl_idname = "paint_system.color_sample"
    bl_label = "Color Sample"

    # 리전(영역) 좌표
    x: IntProperty()
    y: IntProperty()

    @classmethod
    def poll(cls, context):
        return context.mode == 'PAINT_TEXTURE'

    def _native_sample(self, context, merged: bool) -> bool:
        """네이티브 샘플러를 모달 없이(EXEC) 호출한다.

        INVOKE 로 부르면 네이티브 오퍼레이터가 모달 핸들러를 붙이는데,
        파이썬 오퍼레이터 안에서 중첩되면 릴리스 이벤트를 놓쳐 커서가 갇힌다.
        """
        try:
            result = bpy.ops.paint.sample_color(
                'EXEC_DEFAULT', location=(self.x, self.y),
                merged=merged, palette=False)
        except RuntimeError as exc:
            logger.debug("native sample_color failed: %s", exc)
            return False
        return 'FINISHED' in result

    def execute(self, context):
        region = context.region
        area = context.area
        in_view_3d = (
            area is not None and area.type == 'VIEW_3D'
            and region is not None and region.type == 'WINDOW'
        )

        if in_view_3d:
            uv = uv_under_cursor(context, region, (self.x, self.y))
            if uv is not None:
                ps_ctx = self.parse_context(context)
                color = composite_layer_stack(ps_ctx.active_channel, uv[0], uv[1])
                if color is not None:
                    apply_brush_color(context, color)
                    return {'FINISHED'}
            # 레이어에서 색을 못 얻었으면 화면 픽셀로 폴백한다.
            # (검은색을 집는 것보단 보이는 색을 주는 편이 낫다)
            if self._native_sample(context, merged=True):
                context.scene.ps_scene_data.update_hsv_color(context)
                return {'FINISHED'}
            return {'CANCELLED'}

        # 이미지 에디터 등에서는 네이티브 UV 샘플링이 이미 정확하다
        if self._native_sample(context, merged=False):
            context.scene.ps_scene_data.update_hsv_color(context)
            return {'FINISHED'}
        return {'CANCELLED'}

    def invoke(self, context, event):
        self.x = event.mouse_region_x
        self.y = event.mouse_region_y
        return self.execute(context)


classes = collect_classes(sys.modules[__name__])

register, unregister = bpy.utils.register_classes_factory(classes)
