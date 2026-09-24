# SPDX-License-Identifier: GPL-3.0-or-later
# 3DPainter 포크 추가 기능: 파츠(폴리곤 그룹) Isolate
#
# 머리·팔·몸통처럼 면을 파츠로 등록해 두고 하나만 보이게(Isolate) 해서 칠한다.
# 구현은 면 숨김(polygon.hide) + 블렌더 면 선택 마스킹(use_paint_mask)만 쓴다 —
# 셰이더·노드 그래프를 건드리지 않으므로 페인팅 중 뷰포트 비용이 늘지 않는다.
# 숨긴 면은 표시·칠·가림에서 모두 빠져 팔 안쪽처럼 가려진 곳도 칠할 수 있다.
# 로컬 뷰는 2D 캔버스(canvas_switch)가 쓰고 있어 사용하지 않는다.

import sys
from contextlib import contextmanager

import numpy as np

import bpy
from bpy.props import CollectionProperty, IntProperty, PointerProperty, StringProperty, BoolProperty
from bpy.types import Operator, PropertyGroup

from ..utils.registration import collect_classes

MAX_HOTKEY_PARTS = 9  # 1~9 단축키 대상
ATTR_PREFIX = "ps_part_"


class PS_Part(PropertyGroup):
    name: StringProperty(name="Name", default="Part")
    # 면 BOOLEAN 속성 이름 — 한 면이 여러 파츠에 속할 수 있게 파츠마다 따로 둔다
    attr: StringProperty()


class PS_MeshParts(PropertyGroup):
    parts: CollectionProperty(type=PS_Part)
    active_index: IntProperty(default=0)
    # Isolate 중인 파츠 인덱스 (-1 = 전체 표시)
    isolated: IntProperty(default=-1)
    # Isolate 전의 면 선택 마스킹 상태 — 전체 표시 때 되돌린다
    prev_paint_mask: BoolProperty(default=False)
    uid_counter: IntProperty(default=0)


def get_mesh(context):
    obj = context.view_layer.objects.active if context.view_layer else None
    if obj is None or obj.type != 'MESH':
        return None
    return obj.data


def get_parts(context):
    mesh = get_mesh(context)
    return getattr(mesh, "ps_parts", None) if mesh else None


def _face_mask(mesh, part) -> np.ndarray | None:
    attr = mesh.attributes.get(part.attr)
    if attr is None or attr.domain != 'FACE' or attr.data_type != 'BOOLEAN':
        return None
    mask = np.zeros(len(mesh.polygons), dtype=bool)
    attr.data.foreach_get("value", mask)
    return mask


def _write_face_mask(mesh, attr_name: str, mask: np.ndarray) -> None:
    attr = mesh.attributes.get(attr_name)
    if attr is None:
        attr = mesh.attributes.new(attr_name, 'BOOLEAN', 'FACE')
    attr.data.foreach_set("value", mask)


@contextmanager
def _object_mode_data(obj):
    """메쉬 속성을 쓰는 동안만 오브젝트 모드로 둔다.

    편집 모드에서는 면 데이터가 BMesh 에 있어 mesh.attributes 의 길이가 0 이고,
    써도 편집 모드를 나갈 때 덮어써진다. 모드 전환으로 편집 내용을 먼저 반영한다.
    """
    was_edit = obj.mode == 'EDIT'
    if was_edit:
        bpy.ops.object.mode_set(mode='OBJECT')
    try:
        yield obj.data
    finally:
        if was_edit:
            bpy.ops.object.mode_set(mode='EDIT')


def _selected_faces(context, obj) -> np.ndarray:
    """현재 면 선택 (편집 모드 선택은 _object_mode_data 안에서 이미 반영된 상태)."""
    mesh = obj.data
    sel = np.zeros(len(mesh.polygons), dtype=bool)
    mesh.polygons.foreach_get("select", sel)
    return sel


def _set_paint_mask(mesh, enabled: bool) -> None:
    if mesh.use_paint_mask != enabled:
        mesh.use_paint_mask = enabled


def _apply_visibility(mesh, visible: np.ndarray | None) -> None:
    """visible 면만 보이고 칠해지게 한다. None 이면 전체 표시."""
    count = len(mesh.polygons)
    if visible is None:
        mesh.polygons.foreach_set("hide", np.zeros(count, dtype=bool))
    else:
        mesh.polygons.foreach_set("hide", ~visible)
        # 면 선택 마스킹은 '선택된' 면만 칠하므로 보이는 면을 선택 상태로 둔다
        mesh.polygons.foreach_set("select", visible)
    mesh.update()


def isolate(context, index: int) -> str | None:
    """index 파츠를 Isolate 한다. 같은 파츠면 전체 표시로 되돌린다. 오류 메시지 반환."""
    mesh = get_mesh(context)
    data = mesh.ps_parts
    if index < 0 or index >= len(data.parts):
        return "등록된 파츠가 없습니다"
    if data.isolated == index:
        show_all(context)
        return None
    mask = _face_mask(mesh, data.parts[index])
    if mask is None or not mask.any():
        return f"'{data.parts[index].name}' 파츠에 면이 없습니다"
    if data.isolated < 0:
        data.prev_paint_mask = mesh.use_paint_mask
    _apply_visibility(mesh, mask)
    _set_paint_mask(mesh, True)
    data.isolated = index
    data.active_index = index
    return None


def show_all(context) -> None:
    mesh = get_mesh(context)
    data = mesh.ps_parts
    if data.isolated < 0:
        return
    _apply_visibility(mesh, None)
    _set_paint_mask(mesh, data.prev_paint_mask)
    data.isolated = -1


def _redraw(context) -> None:
    for area in context.screen.areas if context.screen else ():
        if area.type in {'VIEW_3D', 'PROPERTIES'}:
            area.tag_redraw()


class _PartsPoll:
    @classmethod
    def poll(cls, context):
        return get_parts(context) is not None


def _not_edit_mode(cls, context) -> bool:
    obj = context.view_layer.objects.active
    if obj is not None and obj.mode == 'EDIT':
        cls.poll_message_set("편집 모드에서는 Isolate 할 수 없습니다 — 텍스처 페인트 모드에서 사용하세요")
        return False
    return get_parts(context) is not None


class PAINTSYSTEM_OT_IsolatePart(_PartsPoll, Operator):
    """파츠 하나만 보이게 하고 그 면에만 칠한다 (같은 파츠를 다시 고르면 전체 표시)"""
    bl_idname = "paint_system.isolate_part"
    bl_label = "Isolate Part"
    # 자주 토글하는 표시 상태라 undo 스텝을 만들지 않는다 (페인팅 undo 보존)
    bl_options = {'REGISTER'}

    index: IntProperty(name="Index", default=0, min=0)

    @classmethod
    def poll(cls, context):
        return _not_edit_mode(cls, context)

    def execute(self, context):
        data = get_parts(context)
        if self.index >= len(data.parts):
            # 빈 번호키는 조용히 무시 — 다른 단축키 동작을 방해하지 않게 통과시킨다
            return {'PASS_THROUGH'}
        err = isolate(context, self.index)
        if err:
            self.report({'WARNING'}, err)
            return {'CANCELLED'}
        _redraw(context)
        return {'FINISHED'}


class PAINTSYSTEM_OT_ShowAllParts(_PartsPoll, Operator):
    """모든 파츠를 다시 보이게 한다"""
    bl_idname = "paint_system.show_all_parts"
    bl_label = "Show All Parts"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return _not_edit_mode(cls, context)

    def execute(self, context):
        show_all(context)
        _redraw(context)
        return {'FINISHED'}


class PAINTSYSTEM_OT_AddPartFromSelection(_PartsPoll, Operator):
    """선택한 면으로 파츠를 만든다 (편집 모드 선택 또는 면 선택 마스킹 선택)"""
    bl_idname = "paint_system.add_part_from_selection"
    bl_label = "Add Part from Selection"
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty(name="Name", default="")

    def execute(self, context):
        obj = context.view_layer.objects.active
        if obj.data.ps_parts.isolated >= 0:
            self.report({'WARNING'}, "전체 표시 상태에서 등록하세요")
            return {'CANCELLED'}
        with _object_mode_data(obj) as mesh:
            sel = _selected_faces(context, obj)
            if not sel.any():
                self.report({'WARNING'}, "선택된 면이 없습니다")
                return {'CANCELLED'}
            _add_part(mesh, self.name or f"Part {len(mesh.ps_parts.parts) + 1}", sel)
        _redraw(context)
        self.report({'INFO'}, f"파츠 추가: 면 {int(sel.sum())}개")
        return {'FINISHED'}


class PAINTSYSTEM_OT_AssignPartSelection(_PartsPoll, Operator):
    """활성 파츠의 면을 현재 선택으로 바꾼다"""
    bl_idname = "paint_system.assign_part_selection"
    bl_label = "Assign Selection"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        data = get_parts(context)
        return data is not None and 0 <= data.active_index < len(data.parts) and data.isolated < 0

    def execute(self, context):
        obj = context.view_layer.objects.active
        with _object_mode_data(obj) as mesh:
            data = mesh.ps_parts
            sel = _selected_faces(context, obj)
            if not sel.any():
                self.report({'WARNING'}, "선택된 면이 없습니다")
                return {'CANCELLED'}
            _write_face_mask(mesh, data.parts[data.active_index].attr, sel)
        return {'FINISHED'}


class PAINTSYSTEM_OT_RemovePart(_PartsPoll, Operator):
    """활성 파츠를 지운다 (메쉬 면은 그대로)"""
    bl_idname = "paint_system.remove_part"
    bl_label = "Remove Part"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        data = get_parts(context)
        return data is not None and 0 <= data.active_index < len(data.parts)

    def execute(self, context):
        obj = context.view_layer.objects.active
        if obj.data.ps_parts.isolated >= 0 and obj.mode != 'EDIT':
            show_all(context)
        with _object_mode_data(obj) as mesh:
            data = mesh.ps_parts
            index = data.active_index
            attr = mesh.attributes.get(data.parts[index].attr)
            if attr is not None:
                mesh.attributes.remove(attr)
            data.parts.remove(index)
            data.active_index = min(index, len(data.parts) - 1)
        _redraw(context)
        return {'FINISHED'}


class PAINTSYSTEM_OT_PartsFromLooseParts(_PartsPoll, Operator):
    """연결되지 않은 조각(머리·팔 등이 분리된 메쉬)마다 파츠를 자동으로 만든다"""
    bl_idname = "paint_system.parts_from_loose"
    bl_label = "Parts from Loose Parts"
    bl_options = {'REGISTER', 'UNDO'}

    min_faces: IntProperty(name="Min Faces", default=16, min=1,
                           description="이보다 면이 적은 조각은 건너뛴다")

    def execute(self, context):
        obj = context.view_layer.objects.active
        if obj.data.ps_parts.isolated >= 0 and obj.mode != 'EDIT':
            show_all(context)
        with _object_mode_data(obj) as mesh:
            labels = _loose_part_labels(mesh)
            ids, counts = np.unique(labels, return_counts=True)
            order = np.argsort(-counts)  # 큰 조각부터 1, 2, 3…
            added = 0
            for i in order:
                if counts[i] < self.min_faces:
                    continue
                _add_part(mesh, f"Part {len(mesh.ps_parts.parts) + 1}", labels == ids[i])
                added += 1
        _redraw(context)
        self.report({'INFO'}, f"파츠 {added}개 생성")
        return {'FINISHED'} if added else {'CANCELLED'}


def _add_part(mesh, name: str, mask: np.ndarray) -> None:
    data = mesh.ps_parts
    data.uid_counter += 1
    attr_name = f"{ATTR_PREFIX}{data.uid_counter}"
    _write_face_mask(mesh, attr_name, mask)
    part = data.parts.add()
    part.name = name
    part.attr = attr_name
    data.active_index = len(data.parts) - 1


def _loose_part_labels(mesh) -> np.ndarray:
    """면 단위 연결 요소 라벨 (정점 공유 기준, union-find)."""
    n_verts = len(mesh.vertices)
    parent = np.arange(n_verts)

    def find(x):
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    edges = np.zeros(len(mesh.edges) * 2, dtype=np.int64)
    mesh.edges.foreach_get("vertices", edges)
    for a, b in edges.reshape(-1, 2):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    roots = np.array([find(v) for v in range(n_verts)])
    loop_start = np.zeros(len(mesh.polygons), dtype=np.int64)
    mesh.polygons.foreach_get("loop_start", loop_start)
    loop_verts = np.zeros(len(mesh.loops), dtype=np.int64)
    mesh.loops.foreach_get("vertex_index", loop_verts)
    return roots[loop_verts[loop_start]]


classes = collect_classes(sys.modules[__name__])


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Mesh.ps_parts = PointerProperty(type=PS_MeshParts)


def unregister():
    del bpy.types.Mesh.ps_parts
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
