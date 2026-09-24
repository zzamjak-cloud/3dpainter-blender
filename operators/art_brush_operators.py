"""Art Brush Pack 조작 연산자.

프리셋은 활성 이미지 페인트 브러시에 덮어쓴다 — 브러시 데이터블록을 만들지
않으므로 에셋 라이브러리에는 아무것도 등록되지 않는다.
"""

import sys

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator
from bpy.utils import register_classes_factory

from .brushes.art_brushes import (
    CATEGORIES,
    apply_art_brush,
    clear_art_brush,
    get_art_icon,
    get_definition,
    unload_art_previews,
)
from ..utils.registration import collect_classes


def _active_paint_brush(context):
    paint = getattr(context.tool_settings, "image_paint", None)
    return getattr(paint, "brush", None) if paint else None


def _redraw(context):
    for area in context.screen.areas:
        if area.type in {'VIEW_3D', 'IMAGE_EDITOR'}:
            area.tag_redraw()


class PAINTSYSTEM_OT_ApplyArtBrush(Operator):
    bl_idname = "paint_system.apply_art_brush"
    bl_label = "Apply Art Brush"
    bl_options = {'REGISTER', 'UNDO'}

    brush_id: StringProperty(name="Art Brush", options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _active_paint_brush(context) is not None

    @classmethod
    def description(cls, context, properties):
        definition = get_definition(properties.brush_id)
        if definition is None:
            return "선택한 아트 브러시 프리셋을 현재 브러시에 적용"
        return f"{definition[1]} — {definition[2]}"

    def execute(self, context):
        brush = _active_paint_brush(context)
        definition = get_definition(self.brush_id)
        if definition is None:
            self.report({'ERROR'}, f"알 수 없는 아트 브러시: {self.brush_id}")
            return {'CANCELLED'}
        if not apply_art_brush(brush, self.brush_id):
            self.report({'ERROR'}, "활성 브러시가 없습니다")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Art Brush: {definition[1]}")
        _redraw(context)
        return {'FINISHED'}


class PAINTSYSTEM_OT_ClearArtBrush(Operator):
    bl_idname = "paint_system.clear_art_brush"
    bl_label = "Reset Brush Texture"
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "아트 브러시 프리셋을 해제하고 텍스처 없는 기본 브러시로 되돌린다"

    @classmethod
    def poll(cls, context):
        return _active_paint_brush(context) is not None

    def execute(self, context):
        clear_art_brush(_active_paint_brush(context))
        _redraw(context)
        return {'FINISHED'}


# ----------------------------------------------------------------- 픽커 프로퍼티

def draw_art_brush_grid(layout, context) -> None:
    """카테고리별 썸네일 그리드 — 이름 버튼을 누르면 현재 브러시에 즉시 적용."""
    from .brushes.art_brushes import get_active_art_brush
    active = get_active_art_brush(_active_paint_brush(context))
    for category, group in CATEGORIES:
        layout.label(text=category)
        grid = layout.grid_flow(row_major=True, columns=6, even_columns=True, align=True)
        for brush_id, label, *_rest in group:
            cell = grid.column(align=True)
            cell.template_icon(icon_value=get_art_icon(brush_id), scale=3.0)
            # depress(선택 표시)는 쓰지 않는다 — 팝오버가 선택 버튼을 마우스 아래로 끌어와
            # 적용된 브러시에 따라 팝업 위치가 바뀐다. 현재 브러시는 체크 아이콘으로 표시
            op = cell.operator("paint_system.apply_art_brush", text=label,
                               icon='CHECKMARK' if brush_id == active else 'NONE')
            op.brush_id = brush_id
    row = layout.row()
    row.operator("paint_system.clear_art_brush", text="Clear", icon='X')


class PAINTSYSTEM_OT_ArtBrushPicker(Operator):
    """아트 브러시 썸네일 그리드를 연다 (뷰포트 버튼) — 클릭하면 열려 있고, 고르면 닫힌다"""
    bl_idname = "paint_system.art_brush_picker"
    bl_label = "Art Brushes"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return _active_paint_brush(context) is not None

    @staticmethod
    def _open(context):
        context.window_manager.popover(
            lambda popup, ctx: draw_art_brush_grid(popup.layout, ctx), ui_units_x=26)

    def invoke(self, context, event):
        # 기즈모는 PRESS 에 실행된다 — 바로 열면 뒤따르는 RELEASE 가 마우스 아래 버튼을
        # 눌러 버려 '누른 채 끌어서 고르는' 메뉴처럼 동작하므로, 버튼을 뗀 뒤에 연다
        if event.value == 'PRESS' and event.type in {'LEFTMOUSE', 'RIGHTMOUSE'}:
            self._button = event.type
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        self._open(context)
        return {'FINISHED'}

    def modal(self, context, event):
        if event.type == self._button and event.value == 'RELEASE':
            self._open(context)
            return {'FINISHED'}
        if event.type in {'ESC', 'WINDOW_DEACTIVATE'}:
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def execute(self, context):
        return {'CANCELLED'}


# Blender 는 EnumProperty items 콜백이 돌려준 문자열을 참조로만 들고 있어,
# 모듈 전역에 붙잡아 두지 않으면 라벨이 깨진다.
_enum_items_cache = []


def _art_brush_items(self, context):
    global _enum_items_cache
    items = []
    for category, group in CATEGORIES:
        for brush_id, label, description, *_rest in group:
            items.append((brush_id, label, f"{category} — {description}",
                          get_art_icon(brush_id), len(items)))
    _enum_items_cache = items
    return _enum_items_cache


def _art_brush_picked(self, context):
    """썸네일에서 고르는 즉시 활성 브러시에 적용한다."""
    apply_art_brush(_active_paint_brush(context), self.ps_art_brush)
    _redraw(context)


classes = collect_classes(sys.modules[__name__])

_register_classes, _unregister_classes = register_classes_factory(classes)


def register():
    _register_classes()
    bpy.types.WindowManager.ps_art_brush = EnumProperty(
        name="Art Brush",
        description="Art Brush Pack 프리셋 (에셋 라이브러리에 등록되지 않는다)",
        items=_art_brush_items,
        update=_art_brush_picked,
    )


def unregister():
    if hasattr(bpy.types.WindowManager, "ps_art_brush"):
        del bpy.types.WindowManager.ps_art_brush
    _unregister_classes()
    unload_art_previews()
