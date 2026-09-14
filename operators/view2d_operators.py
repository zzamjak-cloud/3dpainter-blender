# SPDX-License-Identifier: GPL-3.0-or-later
# 3DPainter 포크 추가 기능: Flat UV Mesh 기반 2D 텍스처 뷰
#
# 원리: UV 좌표를 XY 평면 지오메트리로 펼친 캔버스 오브젝트를 만들고
# 원본과 같은 머티리얼·같은 이름의 UV맵을 공유시킨다. 로컬 뷰로 격리한
# 상단 정사영 뷰포트가 곧 2D 텍스처 뷰가 되며, 어느 뷰에서 칠해도
# 같은 레이어 이미지에 기록되므로 3D/2D가 실시간 상호 반영된다.

import sys

import bpy
from bpy.types import Operator
from mathutils import Vector

from ..utils.registration import collect_classes

# 캔버스 오브젝트를 원본 바운딩 박스 바깥으로 밀어내 메인 뷰에서 모델과
# 겹치지 않게 한다 (고정 오프셋은 모델이 크면 모델 안에 파묻혔다)
CANVAS_MARGIN = 1.0

# 씬 커스텀 프로퍼티 키 (파일 저장 후에도 유지)
KEY_CANVAS = "ps_2d_canvas_obj"
KEY_SOURCE = "ps_2d_source_obj"


def _signed_uv_area(coords) -> float:
    """UV 폴리곤의 부호 있는 면적 — 음수면 뒤집힌(미러) 페이스."""
    area = 0.0
    n = len(coords)
    for i in range(n):
        x0, y0 = coords[i]
        x1, y1 = coords[(i + 1) % n]
        area += x0 * y1 - x1 * y0
    return area * 0.5


def _build_canvas_mesh(src_obj: bpy.types.Object, mesh: bpy.types.Mesh) -> None:
    """src_obj의 활성 UV 레이아웃을 mesh(캔버스용)에 평면 지오메트리로 굽는다."""
    src_mesh = src_obj.data
    uv_layer = src_mesh.uv_layers.active
    if uv_layer is None:
        raise RuntimeError("원본 메시에 UV맵이 없습니다")

    verts: list[tuple[float, float, float]] = []
    faces: list[list[int]] = []
    mat_indices: list[int] = []
    face_uvs: list[list[tuple[float, float]]] = []

    for poly in src_mesh.polygons:
        uvs = [tuple(uv_layer.data[li].uv) for li in poly.loop_indices]
        # 뒤집힌 UV 페이스는 정점 순서를 반전해 노멀을 +Z로 통일
        if _signed_uv_area(uvs) < 0.0:
            uvs.reverse()
        base = len(verts)
        verts.extend((u, v, 0.0) for u, v in uvs)
        faces.append(list(range(base, base + len(uvs))))
        mat_indices.append(poly.material_index)
        face_uvs.append(uvs)

    mesh.clear_geometry()
    mesh.from_pydata(verts, [], faces)

    # 동일한 이름의 UV맵을 만들어 텍셀 매핑을 원본과 일치시킨다
    for src_uv in src_mesh.uv_layers:
        new_uv = mesh.uv_layers.new(name=src_uv.name)
    active_new = mesh.uv_layers.get(uv_layer.name)
    if active_new is not None:
        mesh.uv_layers.active = active_new
        i = 0
        for f_uvs in face_uvs:
            for uv in f_uvs:
                active_new.data[i].uv = uv
                i += 1

    # 머티리얼 슬롯 공유 + 페이스 인덱스 복사
    mesh.materials.clear()
    for mat in src_mesh.materials:
        mesh.materials.append(mat)
    for poly, mi in zip(mesh.polygons, mat_indices):
        poly.material_index = mi

    mesh.update()


def _canvas_location(src_obj: bpy.types.Object, mesh: bpy.types.Mesh):
    """캔버스를 원본의 월드 바운딩 박스 +X 바깥에 놓을 위치.

    캔버스 지오메트리는 UV 범위(보통 0~1)를 그대로 쓰므로 캔버스 자신의
    로컬 min 도 빼서 왼쪽 가장자리가 정확히 여백 지점에 오게 한다.
    """
    try:
        corners = [src_obj.matrix_world @ Vector(c) for c in src_obj.bound_box]
        max_x = max(c.x for c in corners)
        min_y = min(c.y for c in corners)
    except (AttributeError, TypeError, ValueError):
        max_x, min_y = 1.0, 0.0
    canvas_min_x = min((v.co.x for v in mesh.vertices), default=0.0)
    canvas_min_y = min((v.co.y for v in mesh.vertices), default=0.0)
    return (max_x + CANVAS_MARGIN - canvas_min_x, min_y - canvas_min_y, 0.0)


def _isolate_canvas_in_local_view(context, space, canvas) -> None:
    """로컬 뷰에 캔버스만 남긴다 — 다른 오브젝트가 함께 들어오면 캔버스와
    겹쳐 보여 2D 뷰에서 칠할 수 없게 된다."""
    for obj in context.view_layer.objects:
        if obj == canvas:
            continue
        try:
            if obj.local_view_get(space):
                obj.local_view_set(space, False)
        except (AttributeError, RuntimeError):
            continue


def ensure_composite_shading(context) -> None:
    """Solid+Texture 셰이딩은 활성 이미지 한 장만 표시해 레이어 합성이 안 보인다.
    레이어 워크플로에 맞게 Material Preview로 전환한다."""
    screen = getattr(context, 'screen', None)
    if screen is None:
        return
    for area in screen.areas:
        if area.type != 'VIEW_3D':
            continue
        shading = area.spaces.active.shading
        if shading.type == 'SOLID' and shading.color_type == 'TEXTURE':
            shading.type = 'MATERIAL'


def get_canvas_object(scene: bpy.types.Scene) -> bpy.types.Object | None:
    name = scene.get(KEY_CANVAS)
    return bpy.data.objects.get(name) if name else None


def get_source_object(scene: bpy.types.Scene) -> bpy.types.Object | None:
    name = scene.get(KEY_SOURCE)
    return bpy.data.objects.get(name) if name else None


def _find_canvas_area(context, canvas_obj):
    """캔버스 오브젝트가 로컬 뷰로 격리된 3D 뷰 영역을 모든 창에서 찾는다.

    현재 화면(screen)만 뒤지면 워크스페이스 전환·창 분리 상태에서 2D 뷰를
    못 찾아 닫기가 재오픈으로 흘러가므로 반드시 전체 창을 검색한다.
    """
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            space = area.spaces.active
            try:
                if space.local_view and canvas_obj.local_view_get(space):
                    return window, area
            except (AttributeError, RuntimeError):
                continue
    return None, None


def _cleanup_canvas_state(context, canvas):
    """캔버스 오브젝트와 씬 키를 제거하고 원본을 페인트 모드로 복귀시킨다.

    이 정리가 없으면 닫은 뒤에도 패널이 "열림"으로 판정되어 Close 버튼이
    라벨 그대로 남고, 다시 누르면 재오픈되는 악순환에 빠진다.
    """
    scene = context.scene
    src = get_source_object(scene)

    # 캔버스가 활성인 채 제거되지 않도록 원본을 먼저 활성으로 복귀
    if src is not None and src.name in context.view_layer.objects:
        context.view_layer.objects.active = src
        src.select_set(True)
        if context.mode != 'PAINT_TEXTURE':
            try:
                bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
            except RuntimeError:
                pass
    elif context.view_layer.objects.active == canvas:
        # 원본이 없으면 최소한 오브젝트 모드로 되돌려 안전하게 제거
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except RuntimeError:
            pass

    if canvas is not None:
        mesh = canvas.data
        try:
            bpy.data.objects.remove(canvas)
        except RuntimeError:
            pass
        else:
            if mesh is not None and mesh.users == 0:
                bpy.data.meshes.remove(mesh)

    for key in (KEY_CANVAS, KEY_SOURCE):
        try:
            del scene[key]
        except KeyError:
            pass


class PAINTSYSTEM_OT_Toggle2DView(Operator):
    """2D 텍스처 뷰를 열거나 닫는다 (뷰포트 분할 + UV 평면 캔버스)"""
    bl_idname = "paint_system.toggle_2d_view"
    bl_label = "Toggle 2D View"
    # UNDO 필수: 셋업 완료 상태를 undo 스텝으로 남겨야 이후 스트로크를
    # undo해도 "캔버스가 존재하는 상태"로 복원된다 (없으면 셋업 이전으로 튐)
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            context.area is not None and context.area.type == 'VIEW_3D'
            and obj is not None and obj.type == 'MESH'
        )

    def execute(self, context):
        scene = context.scene
        canvas = get_canvas_object(scene)

        # 이미 열려 있으면 닫기 — 영역을 닫기 전에 상태(캔버스·씬 키)를
        # 먼저 정리한다. 2D 뷰 자체의 N패널에서 눌렀을 때 영역이 먼저
        # 사라지면 이후 컨텍스트 조작이 위험하기 때문.
        # 영역을 못 찾는 경우(워크스페이스 전환·수동 로컬 뷰 해제)에도
        # 재오픈으로 흘리지 않고 상태만 정리하고 끝낸다.
        if canvas is not None:
            window, area = _find_canvas_area(context, canvas)
            _cleanup_canvas_state(context, canvas)
            if area is not None:
                try:
                    with context.temp_override(window=window, area=area):
                        bpy.ops.screen.area_close()
                except RuntimeError:
                    # 레이아웃 사정으로 영역을 못 닫아도 상태 정리는 끝났으니
                    # 다음 클릭이 정상적인 "열기"로 동작한다
                    self.report({'WARNING'}, "2D 뷰 영역을 닫지 못했습니다. 영역 경계에서 직접 닫아주세요")
            return {'FINISHED'}

        src = context.active_object

        if src.data.uv_layers.active is None:
            self.report({'ERROR'}, "활성 오브젝트에 UV맵이 없습니다")
            return {'CANCELLED'}

        # 1. 캔버스 오브젝트 생성 (닫을 때 제거되므로 항상 새로 만든다)
        mesh = bpy.data.meshes.new("PS 2D Canvas")
        canvas = bpy.data.objects.new("PS 2D Canvas", mesh)
        context.collection.objects.link(canvas)
        _build_canvas_mesh(src, canvas.data)
        canvas.location = _canvas_location(src, canvas.data)
        canvas.hide_render = True
        canvas.hide_select = False  # 로컬 뷰 진입을 위해 잠시 선택 가능
        scene[KEY_CANVAS] = canvas.name
        scene[KEY_SOURCE] = src.name

        # 2. 캔버스를 텍스처 페인트 모드에 넣어둔다 — 이후 클릭 전환이
        # 오퍼레이터 없이 활성 오브젝트 교체만으로 끝나 undo를 오염시키지 않는다
        prev_mode = src.mode
        if prev_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        context.view_layer.objects.active = canvas
        bpy.ops.object.mode_set(mode='TEXTURE_PAINT')

        # 3. 영역 분할 → 오른쪽 절반을 2D 뷰로 구성
        areas_before = set(a.as_pointer() for a in context.screen.areas)
        with context.temp_override(area=context.area):
            bpy.ops.screen.area_split(direction='VERTICAL', factor=0.5)
        new_area = next(
            (a for a in context.screen.areas if a.as_pointer() not in areas_before),
            None,
        )
        if new_area is None:
            self.report({'ERROR'}, "뷰포트 분할에 실패했습니다")
            return {'CANCELLED'}

        space = new_area.spaces.active
        region = next(r for r in new_area.regions if r.type == 'WINDOW')

        # 4. 캔버스만 선택 → 로컬 뷰로 격리 (캔버스는 텍스처 페인트 모드 유지)
        for o in context.view_layer.objects:
            o.select_set(False)
        canvas.select_set(True)
        with context.temp_override(area=new_area, region=region):
            bpy.ops.view3d.localview(frame_selected=True)
        try:
            in_local = bool(space.local_view) and canvas.local_view_get(space)
        except (AttributeError, RuntimeError):
            in_local = False
        if not in_local:
            # 격리에 실패하면 2D 뷰가 씬 전체를 보여 모델과 겹친다 — 되돌리고 알린다
            _cleanup_canvas_state(context, canvas)
            try:
                with context.temp_override(area=new_area):
                    bpy.ops.screen.area_close()
            except RuntimeError:
                pass
            self.report({'ERROR'}, "2D 뷰 로컬 뷰 격리에 실패했습니다")
            return {'CANCELLED'}
        _isolate_canvas_in_local_view(context, space, canvas)

        # 5. 상단 정사영 고정 + 오버레이 정리
        r3d = space.region_3d
        r3d.view_perspective = 'ORTHO'
        r3d.view_rotation = (1.0, 0.0, 0.0, 0.0)  # 정확히 -Z를 내려다보는 탑뷰
        if hasattr(r3d, 'lock_rotation'):
            r3d.lock_rotation = True
        space.shading.type = 'MATERIAL'
        space.show_gizmo = False
        ov = space.overlay
        ov.show_floor = False
        ov.show_axis_x = False
        ov.show_axis_y = False
        ov.show_axis_z = False
        ov.show_cursor = False
        ov.show_object_origins = False

        # 6. 메인 뷰에서의 오클릭 방지 + 원래 상태 복원
        canvas.hide_select = True
        context.view_layer.objects.active = src
        src.select_set(True)
        bpy.ops.object.mode_set(mode='TEXTURE_PAINT')

        return {'FINISHED'}


class PAINTSYSTEM_OT_Refresh2DCanvas(Operator):
    """원본 UV 변경을 2D 캔버스에 다시 반영한다"""
    bl_idname = "paint_system.refresh_2d_canvas"
    bl_label = "Refresh 2D Canvas"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return get_canvas_object(context.scene) is not None

    def execute(self, context):
        scene = context.scene
        canvas = get_canvas_object(scene)
        src = get_source_object(scene)
        if canvas is None or src is None:
            self.report({'ERROR'}, "2D 캔버스가 없습니다")
            return {'CANCELLED'}
        _build_canvas_mesh(src, canvas.data)
        return {'FINISHED'}


class PAINTSYSTEM_OT_CanvasSwitch(Operator):
    """클릭한 뷰포트(3D/2D)에 맞춰 페인팅 대상을 자동 전환하고 이벤트를 통과시킨다"""
    bl_idname = "paint_system.canvas_switch"
    bl_label = "Canvas Switch"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'PAINT_TEXTURE'

    def invoke(self, context, event):
        scene = context.scene
        canvas = get_canvas_object(scene)
        src = get_source_object(scene)
        space = context.space_data
        if canvas is None or src is None or space is None or space.type != 'VIEW_3D':
            return {'PASS_THROUGH'}

        try:
            in_canvas_view = bool(space.local_view) and canvas.local_view_get(space)
        except (AttributeError, RuntimeError):
            return {'PASS_THROUGH'}

        target = canvas if in_canvas_view else src
        if context.view_layer.objects.active != target:
            # 활성 교체는 순수 대입이라 undo 스텝을 만들지 않는다.
            # 양쪽 오브젝트 모두 셋업 시점에 텍스처 페인트 모드로 유지되므로
            # 원칙적으로 mode_set이 필요 없지만, 어긋난 경우에만 undo 억제 후 복구.
            context.view_layer.objects.active = target
            if context.mode != 'PAINT_TEXTURE':
                prefs = bpy.context.preferences.edit
                prev_undo = prefs.use_global_undo
                prefs.use_global_undo = False
                try:
                    bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
                except RuntimeError:
                    return {'PASS_THROUGH'}
                finally:
                    prefs.use_global_undo = prev_undo
        return {'PASS_THROUGH'}


def _heal_canvas_now():
    """undo가 캔버스 생성 이전으로 넘어간 경우 상태를 정리한다.

    유령이 된 2D 영역(로컬 뷰 + 회전 잠금)을 닫고, 원본 오브젝트를
    텍스처 페인트 모드로 복귀시킨다 — 캔버스 소멸로 모드가 풀리는 문제 방지.
    """
    ctx = bpy.context
    scene = ctx.scene
    if scene is None:
        return None
    name = scene.get(KEY_CANVAS)
    if not name or bpy.data.objects.get(name) is not None:
        return None  # 캔버스 살아 있음 — 할 일 없음

    for window in ctx.window_manager.windows:
        for area in window.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            space = area.spaces.active
            r3d = getattr(space, 'region_3d', None)
            # 우리가 만든 2D 뷰 식별: 로컬 뷰 + 회전 잠금
            if space.local_view and r3d is not None and getattr(r3d, 'lock_rotation', False):
                try:
                    with ctx.temp_override(window=window, area=area):
                        bpy.ops.screen.area_close()
                except RuntimeError:
                    pass
                break

    src = bpy.data.objects.get(scene.get(KEY_SOURCE) or "")
    if src is not None and src.name in ctx.view_layer.objects:
        ctx.view_layer.objects.active = src
        if ctx.mode != 'PAINT_TEXTURE':
            try:
                bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
            except RuntimeError:
                pass
    for key in (KEY_CANVAS, KEY_SOURCE):
        try:
            del scene[key]
        except KeyError:
            pass
    return None


from bpy.app.handlers import persistent


@persistent
def _heal_after_undo(_scene, _depsgraph=None):
    # undo 처리 도중에는 화면 조작이 위험하므로 타이머로 한 박자 미룬다
    ctx_scene = bpy.context.scene
    if ctx_scene is None or not ctx_scene.get(KEY_CANVAS):
        return
    if bpy.data.objects.get(ctx_scene.get(KEY_CANVAS)) is None:
        bpy.app.timers.register(_heal_canvas_now, first_interval=0.05)


classes = collect_classes(sys.modules[__name__])

_register, _unregister = bpy.utils.register_classes_factory(classes)


def register():
    _register()
    if _heal_after_undo not in bpy.app.handlers.undo_post:
        bpy.app.handlers.undo_post.append(_heal_after_undo)


def unregister():
    try:
        bpy.app.handlers.undo_post.remove(_heal_after_undo)
    except ValueError:
        pass
    _unregister()
