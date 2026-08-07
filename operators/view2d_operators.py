# SPDX-License-Identifier: GPL-3.0-or-later
# 3DPainter 포크 추가 기능: Flat UV Mesh 기반 2D 텍스처 뷰
#
# 원리: UV 좌표를 XY 평면 지오메트리로 펼친 캔버스 오브젝트를 만들고
# 원본과 같은 머티리얼·같은 이름의 UV맵을 공유시킨다. 로컬 뷰로 격리한
# 상단 정사영 뷰포트가 곧 2D 텍스처 뷰가 되며, 어느 뷰에서 칠해도
# 같은 레이어 이미지에 기록되므로 3D/2D가 실시간 상호 반영된다.

import bpy
from bpy.types import Operator

# 캔버스 오브젝트를 원점에서 밀어내 메인 뷰에서 모델과 겹치지 않게 한다
CANVAS_OFFSET_X = 2.0

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


def get_canvas_object(scene: bpy.types.Scene) -> bpy.types.Object | None:
    name = scene.get(KEY_CANVAS)
    return bpy.data.objects.get(name) if name else None


def get_source_object(scene: bpy.types.Scene) -> bpy.types.Object | None:
    name = scene.get(KEY_SOURCE)
    return bpy.data.objects.get(name) if name else None


def _find_canvas_area(context, canvas_obj):
    """캔버스 오브젝트가 로컬 뷰로 격리된 3D 뷰 영역을 찾는다."""
    for area in context.screen.areas:
        if area.type != 'VIEW_3D':
            continue
        space = area.spaces.active
        try:
            if space.local_view and canvas_obj.local_view_get(space):
                return area, space
        except (AttributeError, RuntimeError):
            continue
    return None, None


class PAINTSYSTEM_OT_Toggle2DView(Operator):
    """2D 텍스처 뷰를 열거나 닫는다 (뷰포트 분할 + UV 평면 캔버스)"""
    bl_idname = "paint_system.toggle_2d_view"
    bl_label = "Toggle 2D View"
    bl_options = {'REGISTER'}

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

        # 이미 열려 있으면 닫기
        if canvas is not None:
            area, _space = _find_canvas_area(context, canvas)
            if area is not None:
                with context.temp_override(area=area):
                    bpy.ops.screen.area_close()
                return {'FINISHED'}

        src = context.active_object
        if canvas is not None and src == canvas:
            src = get_source_object(scene) or src

        if src.data.uv_layers.active is None:
            self.report({'ERROR'}, "활성 오브젝트에 UV맵이 없습니다")
            return {'CANCELLED'}

        # 1. 캔버스 오브젝트 생성/재사용
        if canvas is None:
            mesh = bpy.data.meshes.new("PS 2D Canvas")
            canvas = bpy.data.objects.new("PS 2D Canvas", mesh)
            context.collection.objects.link(canvas)
        _build_canvas_mesh(src, canvas.data)
        canvas.location = (CANVAS_OFFSET_X, 0.0, 0.0)
        canvas.hide_render = True
        canvas.hide_select = False  # 로컬 뷰 진입을 위해 잠시 선택 가능
        scene[KEY_CANVAS] = canvas.name
        scene[KEY_SOURCE] = src.name

        # 2. 캔버스를 텍스처 페인트 모드로 준비 (이후 클릭 전환이 매끄럽도록)
        prev_mode = src.mode
        if prev_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        context.view_layer.objects.active = canvas
        bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
        bpy.ops.object.mode_set(mode='OBJECT')

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

        # 4. 캔버스만 선택 → 로컬 뷰로 격리
        for o in context.view_layer.objects:
            o.select_set(False)
        canvas.select_set(True)
        context.view_layer.objects.active = canvas
        with context.temp_override(area=new_area, region=region):
            bpy.ops.view3d.localview(frame_selected=True)

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
            context.view_layer.objects.active = target
            if context.mode != 'PAINT_TEXTURE':
                try:
                    bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
                except RuntimeError:
                    return {'PASS_THROUGH'}
        return {'PASS_THROUGH'}


classes = (
    PAINTSYSTEM_OT_Toggle2DView,
    PAINTSYSTEM_OT_Refresh2DCanvas,
    PAINTSYSTEM_OT_CanvasSwitch,
)

register, unregister = bpy.utils.register_classes_factory(classes)
