# SPDX-License-Identifier: GPL-3.0-or-later
# 3DPainter 포크 추가 기능: 파츠 Isolate UI (사이드바 관리 패널) + 뷰포트 우측 버튼
# (아트 브러시 썸네일 버튼, 파츠 번호 버튼)

import sys

import blf
import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader
from bpy.types import GizmoGroup, Panel, UIList
from mathutils import Matrix

from ..operators.parts_operators import MAX_HOTKEY_PARTS, get_parts
from ..utils.registration import collect_classes

BUTTON_RADIUS = 11.0   # px (UI 배율 전)
ART_RADIUS = 18.0      # 아트 브러시 썸네일 버튼 반지름
BUTTON_GAP = 28.0
SIDE_MARGIN = 22.0     # 오른쪽 사이드바 경계 ~ 버튼 중심 (내비게이션 버튼 열과 맞춤)
NAV_STACK_FAC = 2.4    # 축 기즈모 + 미니 버튼 4개(줌·팬·카메라·그리드) 높이 ≈ 기즈모 크기 × 2.4
NAV_GAP = 44.0         # 그리드 버튼 아래 여백
TOP_MARGIN = 36.0      # 내비게이션 기즈모가 없을 때 위쪽 여백


def _parts_visible(context) -> bool:
    if context.mode != 'PAINT_TEXTURE':
        return False
    data = get_parts(context)
    return data is not None and len(data.parts) > 0


def _ui_overlap_width(area) -> float:
    """오른쪽 사이드바(N 패널)가 뷰포트 위에 겹쳐 그려지는 폭."""
    # 리전 겹침이 꺼져 있으면 사이드바가 뷰포트 밖에 있으므로 비킬 필요가 없다
    if not bpy.context.preferences.system.use_region_overlap:
        return 0.0
    for region in area.regions:
        if region.type == 'UI' and region.width > 1:
            return float(region.width)
    return 0.0


def _top_overlap_height(area, region) -> float:
    """뷰포트 위에 겹쳐 그려지는 상단 헤더·툴 헤더 높이 (겹치지 않으면 0)."""
    top = region.y + region.height
    return float(sum(
        r.height for r in area.regions
        if r.type in {'HEADER', 'TOOL_HEADER'} and r.alignment == 'TOP'
        and r.y >= region.y and r.y + r.height <= top
    ))


def _first_button_top(context) -> float:
    """첫 버튼 중심의 위쪽 거리(px). 뷰포트 우상단 내비게이션 기즈모 아래에 둔다."""
    scale = context.preferences.system.ui_scale
    top = _top_overlap_height(context.area, context.region)
    space = context.space_data
    if space is not None and space.show_gizmo and space.show_gizmo_navigate:
        nav = context.preferences.view.gizmo_size_navigate_v3d
        return top + (nav * NAV_STACK_FAC + NAV_GAP) * scale
    return top + TOP_MARGIN * scale


def _art_center(context) -> tuple[float, float]:
    """아트 브러시 버튼 중심 — 버튼 열 맨 위."""
    scale = context.preferences.system.ui_scale
    region = context.region
    x = region.width - _ui_overlap_width(context.area) - SIDE_MARGIN * scale
    y = region.height - _first_button_top(context) - (ART_RADIUS - BUTTON_RADIUS) * scale
    return x, y


def _button_center(context, index: int) -> tuple[float, float]:
    """index 번째 파츠 버튼 중심 — 아트 브러시 버튼 아래부터 쌓는다."""
    scale = context.preferences.system.ui_scale
    ax, ay = _art_center(context)
    y = ay - (ART_RADIUS + BUTTON_RADIUS + 16.0) * scale - index * BUTTON_GAP * scale
    return ax, y


def _active_art_brush(context) -> str:
    from ..operators.brushes.art_brushes import get_active_art_brush
    paint = getattr(context.tool_settings, "image_paint", None)
    return get_active_art_brush(getattr(paint, "brush", None) if paint else None)


# 아트 브러시 썸네일 GPU 텍스처 캐시 (brush_id → GPUTexture) — 한 번만 만든다
_thumb_textures: dict = {}


def _thumb_texture(brush_id: str):
    if brush_id in _thumb_textures:
        return _thumb_textures[brush_id]
    from ..operators.brushes import art_brushes
    if not art_brushes.get_art_icon(brush_id):
        return None
    entry = art_brushes._previews.get(brush_id)
    w, h = entry.image_size
    if w == 0 or h == 0:
        return None  # 미리보기 로딩 전 — 다음 리드로우에서 다시 시도
    pixels = np.empty(w * h * 4, dtype=np.float32)
    entry.image_pixels_float.foreach_get(pixels)
    buf = gpu.types.Buffer('FLOAT', w * h * 4, pixels)
    tex = gpu.types.GPUTexture((w, h), format='RGBA16F', data=buf)
    _thumb_textures[brush_id] = tex
    return tex


def _draw_disc(cx: float, cy: float, radius: float, color) -> None:
    import math
    segments = 48
    pts = [(cx, cy)] + [
        (cx + radius * math.cos(t), cy + radius * math.sin(t))
        for t in (i * math.tau / segments for i in range(segments + 1))
    ]
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'TRI_FAN', {"pos": pts})
    gpu.state.blend_set('ALPHA')
    shader.uniform_float("color", color)
    batch.draw(shader)
    gpu.state.blend_set('NONE')


def _draw_thumb(tex, cx: float, cy: float, half: float) -> None:
    shader = gpu.shader.from_builtin('IMAGE')
    quad = ((cx - half, cy - half), (cx + half, cy - half),
            (cx + half, cy + half), (cx - half, cy + half))
    batch = batch_for_shader(shader, 'TRI_FAN', {
        "pos": quad, "texCoord": ((0, 0), (1, 0), (1, 1), (0, 1))})
    gpu.state.blend_set('ALPHA')
    shader.bind()
    shader.uniform_sampler("image", tex)
    batch.draw(shader)
    gpu.state.blend_set('NONE')


class PAINTSYSTEM_UL_Parts(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        key = str(index + 1) if index < MAX_HOTKEY_PARTS else "·"
        row.label(text=key)
        row.prop(item, "name", text="", emboss=False)
        isolated = data.isolated == index
        op = row.operator("paint_system.isolate_part", text="",
                          icon='HIDE_OFF' if isolated else 'HIDE_ON',
                          depress=isolated, emboss=isolated)
        op.index = index


class MAT_PT_Parts(Panel):
    """면 그룹(파츠)을 등록해 하나씩 Isolate 하며 칠한다"""
    bl_idname = "MAT_PT_Parts"
    bl_label = "Parts (Isolate)"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Paint System'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.mode in {'PAINT_TEXTURE', 'EDIT_MESH'} and get_parts(context) is not None

    def draw(self, context):
        layout = self.layout
        data = get_parts(context)
        row = layout.row()
        row.template_list("PAINTSYSTEM_UL_Parts", "", data, "parts", data, "active_index", rows=4)
        col = row.column(align=True)
        col.operator("paint_system.add_part_from_selection", text="", icon='ADD')
        col.operator("paint_system.remove_part", text="", icon='REMOVE')
        col = layout.column(align=True)
        row = col.row(align=True)
        row.operator("paint_system.assign_part_selection", text="Assign Selection")
        row.operator("paint_system.parts_from_loose", text="From Loose Parts")
        col.operator("paint_system.show_all_parts", text="Show All (0)", icon='HIDE_OFF')
        layout.label(text="1~9: Isolate / 같은 번호 다시: 전체", icon='INFO')


class PAINTSYSTEM_GGT_PartButtons(GizmoGroup):
    """뷰포트 우측 내비게이션 버튼 아래의 파츠 버튼 (클릭 = Isolate 토글)"""
    bl_idname = "PAINTSYSTEM_GGT_part_buttons"
    bl_label = "3DPainter Part Buttons"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_options = {'PERSISTENT', 'SCALE'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'PAINT_TEXTURE'

    def setup(self, context):
        # 아트 브러시 버튼은 둘 — 적용 전: 기본 브러시 아이콘 버튼 / 적용 후: 투명한 클릭 영역
        # (기즈모가 라벨 핸들러보다 나중에 그려져 썸네일을 가리고, 아이콘은 실행 중 교체가
        # 반영되지 않으므로 썸네일 쪽 배경·그림은 _draw_part_labels 가 직접 그린다)
        icon_btn = self.gizmos.new("GIZMO_GT_button_2d")
        icon_btn.icon = 'BRUSH_DATA'
        icon_btn.draw_options = {'BACKDROP', 'OUTLINE'}
        thumb_btn = self.gizmos.new("GIZMO_GT_button_2d")
        thumb_btn.draw_options = set()
        for gz in (icon_btn, thumb_btn):
            gz.scale_basis = ART_RADIUS
            gz.alpha = 0.55
            gz.color = (0.15, 0.15, 0.15)
            gz.color_highlight = (0.9, 0.9, 0.9)
            gz.alpha_highlight = 0.8
            gz.target_set_operator("paint_system.art_brush_picker")
        self.art_buttons = (icon_btn, thumb_btn)
        # 개수가 바뀔 때마다 기즈모를 만들고 지우지 않도록 1~9 + 전체 버튼을 미리 만든다
        self.buttons = []
        for i in range(MAX_HOTKEY_PARTS + 1):
            gz = self.gizmos.new("GIZMO_GT_button_2d")
            gz.draw_options = {'BACKDROP', 'OUTLINE'}
            gz.scale_basis = BUTTON_RADIUS
            gz.alpha = 0.55
            gz.color_highlight = (0.9, 0.9, 0.9)
            gz.alpha_highlight = 0.8
            if i < MAX_HOTKEY_PARTS:
                gz.target_set_operator("paint_system.isolate_part").index = i
            else:
                gz.target_set_operator("paint_system.show_all_parts")
            self.buttons.append(gz)

    def draw_prepare(self, context):
        x, y = _art_center(context)
        has_art = bool(_active_art_brush(context))
        icon_btn, thumb_btn = self.art_buttons
        icon_btn.hide = has_art
        thumb_btn.hide = not has_art
        for gz in self.art_buttons:
            gz.matrix_basis = Matrix.Translation((x, y, 0.0))
        data = get_parts(context)
        count = min(len(data.parts), MAX_HOTKEY_PARTS) if data else 0
        for i, gz in enumerate(self.buttons):
            if i < MAX_HOTKEY_PARTS:
                gz.hide = i >= count
                slot = i
            else:
                gz.hide = count == 0
                slot = count  # 전체 표시 버튼은 목록 맨 아래
            if gz.hide:
                continue
            x, y = _button_center(context, slot)
            gz.matrix_basis = Matrix.Translation((x, y, 0.0))
            active = (i < MAX_HOTKEY_PARTS and data.isolated == i) or (
                i == MAX_HOTKEY_PARTS and data.isolated < 0)
            gz.color = (0.28, 0.5, 0.9) if active else (0.15, 0.15, 0.15)


def _draw_part_labels():
    """아트 브러시 썸네일, 파츠 버튼 번호·이름. 뷰포트 리드로우 때만 (캐시된 텍스처 + 텍스트 몇 줄)."""
    context = bpy.context
    if context.mode != 'PAINT_TEXTURE' or context.region is None:
        return
    scale = context.preferences.system.ui_scale
    brush_id = _active_art_brush(context)
    if brush_id:
        tex = _thumb_texture(brush_id)
        if tex is not None:
            ax, ay = _art_center(context)
            _draw_disc(ax, ay, ART_RADIUS * scale, (0.15, 0.15, 0.15, 0.55))
            _draw_thumb(tex, ax, ay, ART_RADIUS * 0.72 * scale)
    if not _parts_visible(context):
        return
    data = get_parts(context)
    font = 0
    blf.size(font, 11 * scale)
    count = min(len(data.parts), MAX_HOTKEY_PARTS)
    labels = [(str(i + 1), data.parts[i].name, data.isolated == i) for i in range(count)]
    labels.append(("0", "All", data.isolated < 0))
    for slot, (key, name, active) in enumerate(labels):
        x, y = _button_center(context, slot)
        kw, kh = blf.dimensions(font, key)
        blf.color(font, 1.0, 1.0, 1.0, 1.0)
        blf.position(font, x - kw / 2, y - kh / 2, 0)
        blf.draw(font, key)
        blf.enable(font, blf.SHADOW)
        blf.shadow(font, 3, 0.0, 0.0, 0.0, 0.8)
        alpha = 1.0 if active else 0.75
        blf.color(font, 1.0, 1.0, 1.0, alpha)
        # 버튼이 오른쪽 가장자리에 있으므로 이름은 왼쪽에 오른쪽 정렬로 쓴다
        nw, _nh = blf.dimensions(font, name)
        blf.position(font, x - (BUTTON_RADIUS + 6) * scale - nw, y - kh / 2, 0)
        blf.draw(font, name)
        blf.disable(font, blf.SHADOW)


classes = collect_classes(sys.modules[__name__])
_label_handle = None


def register():
    global _label_handle
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.utils.register_class(PAINTSYSTEM_GGT_PartButtons)
    _label_handle = bpy.types.SpaceView3D.draw_handler_add(
        _draw_part_labels, (), 'WINDOW', 'POST_PIXEL')


def unregister():
    global _label_handle
    if _label_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_label_handle, 'WINDOW')
        _label_handle = None
    bpy.utils.unregister_class(PAINTSYSTEM_GGT_PartButtons)
    _thumb_textures.clear()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
