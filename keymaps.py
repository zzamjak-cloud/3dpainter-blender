# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

addon_keymaps = []

# Toggleable shortcuts
ENABLE_SHIFT_RMB_IN_TEXPAINT = True
# 3DPainter 포크: 포토샵식 단축키
ENABLE_ALT_CLICK_EYEDROPPER = True
ENABLE_SHIFT_CLICK_LINE = True


def _add_keymap_entry(
    kc: bpy.types.KeyConfig,
    name: str,
    space_type: str,
    idname: str,
    key: str,
    value: str = 'PRESS',
    shift: bool = False,
    ctrl: bool = False,
    alt: bool = False,
    oskey: bool = False,
    repeat: bool = False,
    properties: dict | None = None,
):
    km = kc.keymaps.new(name=name, space_type=space_type)
    kmi = km.keymap_items.new(
        idname, type=key, value=value,
        shift=shift, ctrl=ctrl, alt=alt, oskey=oskey)
    if repeat:
        kmi.repeat = repeat
    if properties:
        for prop, prop_value in properties.items():
            try:
                setattr(kmi.properties, prop, prop_value)
            except Exception:
                pass
    addon_keymaps.append((km, kmi))


def register() -> None:
    try:
        kc = getattr(getattr(bpy.context, 'window_manager', None), 'keyconfigs', None)
        kc = getattr(kc, 'addon', None)
        if not kc:
            return

        km_name = 'Image Paint'
        space = 'EMPTY'
        # Plain RMB override in Texture Paint tool context (preferred)
        if ENABLE_SHIFT_RMB_IN_TEXPAINT:
            # Tool-specific keymap names vary slightly across versions; add to a couple of common ones
            _add_keymap_entry(
                kc,
                name=km_name,
                space_type=space,
                idname='wm.call_panel',
                key='RIGHTMOUSE',
                value='PRESS',
                properties={'name': 'MAT_PT_TexPaintRMBMenu'},
                shift=True,
            )

        # 3DPainter 포크: Alt를 누르는 동안 스포이드 커서 유지
        # (ALT 키 PRESS 이벤트 자체가 alt 플래그를 가질 수 있어 양쪽 등록)
        for _alt_key in ('LEFT_ALT', 'RIGHT_ALT'):
            for _alt_flag in (False, True):
                _add_keymap_entry(
                    kc,
                    name=km_name,
                    space_type=space,
                    idname='paint_system.eyedropper_cursor',
                    key=_alt_key,
                    alt=_alt_flag,
                )

        # 3DPainter 포크: Alt+클릭 스포이드 (포토샵식)
        # 네이티브 paint.sample_color 는 merged=False 일 때 활성 레이어 이미지
        # 한 장만 보므로, 그 레이어가 비어 있으면 투명 픽셀의 RGB 인 검은색이
        # 집힌다. paint_system.color_sample 은 레이어 스택 전체를 합성해
        # 음영 없는 색을 구하고, 아무것도 없을 때만 화면 픽셀로 폴백한다.
        # (네이티브는 EXEC 로만 호출해 모달 중첩으로 커서가 갇히는 문제를 피한다)
        if ENABLE_ALT_CLICK_EYEDROPPER:
            _add_keymap_entry(
                kc,
                name=km_name,
                space_type=space,
                idname='paint_system.color_sample',
                key='LEFTMOUSE',
                alt=True,
            )

        # 3DPainter 포크: 2D/3D 뷰 클릭 시 페인팅 대상 자동 전환 (이벤트 통과)
        _add_keymap_entry(
            kc,
            name=km_name,
            space_type=space,
            idname='paint_system.canvas_switch',
            key='LEFTMOUSE',
        )

        # 3DPainter 포크: Shift+클릭 직선 + 앵커 기록(일반 클릭 통과)
        if ENABLE_SHIFT_CLICK_LINE:
            _add_keymap_entry(
                kc,
                name=km_name,
                space_type=space,
                idname='paint_system.line_stroke',
                key='LEFTMOUSE',
                shift=True,
            )
            _add_keymap_entry(
                kc,
                name=km_name,
                space_type=space,
                idname='paint_system.record_stroke_anchor',
                key='LEFTMOUSE',
            )

        # 3DPainter 포크: 라쏘 선택 (2D 뷰 전용) — Ctrl/Cmd+Shift+드래그,
        # +Alt는 선택 제외. 모디파이어 정확 일치라 Alt 변형도 별도 등록.
        for _mods in (
            dict(ctrl=True, shift=True),
            dict(ctrl=True, shift=True, alt=True),
            dict(oskey=True, shift=True),          # macOS Cmd+Shift
            dict(oskey=True, shift=True, alt=True),
        ):
            _add_keymap_entry(
                kc,
                name=km_name,
                space_type=space,
                idname='paint_system.lasso_select',
                key='LEFTMOUSE',
                **_mods,
            )

        # 3DPainter 포크: 캔버스 밖 빈 공간 클릭 = 선택 해제 (포토샵과 동일)
        _add_keymap_entry(
            kc,
            name=km_name,
            space_type=space,
            idname='paint_system.deselect_on_empty_click',
            key='LEFTMOUSE',
        )

        # 3DPainter 포크: Alt+Delete/Backspace = 선택 영역을 브러시 색으로 채우기
        # (맥 delete 키는 BACK_SPACE 코드로 들어온다)
        for _key in ('BACK_SPACE', 'DEL'):
            _add_keymap_entry(
                kc,
                name=km_name,
                space_type=space,
                idname='paint_system.fill_selection',
                key=_key,
                alt=True,
            )

        # 3DPainter 포크: Ctrl/Cmd+D = 선택 해제 (포토샵과 동일)
        # Image Paint 키맵 + 3D View 키맵 양쪽에 등록해 모든 뷰에서 동작 보장
        for _km_name, _km_space in ((km_name, space), ('3D View', 'VIEW_3D')):
            for _mods in (dict(ctrl=True), dict(oskey=True)):
                _add_keymap_entry(
                    kc,
                    name=_km_name,
                    space_type=_km_space,
                    idname='paint_system.clear_selection',
                    key='D',
                    **_mods,
                )

        # 3DPainter 포크: 숫자키 1~9 = 파츠 Isolate (같은 번호 다시 = 전체), 0 = 전체 표시
        # 넘패드는 블렌더 뷰 전환 키로 남겨 둔다
        _digit_keys = ('ONE', 'TWO', 'THREE', 'FOUR', 'FIVE',
                       'SIX', 'SEVEN', 'EIGHT', 'NINE')
        for _i, _key in enumerate(_digit_keys):
            _add_keymap_entry(
                kc, name=km_name, space_type=space,
                idname='paint_system.isolate_part', key=_key,
                properties={'index': _i})
        _add_keymap_entry(
            kc, name=km_name, space_type=space,
            idname='paint_system.show_all_parts', key='ZERO')

        # 3DPainter 포크: 레이어 단축키 — Ctrl/Cmd+E 병합, Ctrl/Cmd+Alt+Shift+N 새 레이어
        for _mods in (dict(ctrl=True), dict(oskey=True)):
            _add_keymap_entry(
                kc,
                name=km_name,
                space_type=space,
                idname='paint_system.quick_merge_down',
                key='E',
                **_mods,
            )
        for _mods in (dict(ctrl=True, alt=True, shift=True),
                      dict(oskey=True, alt=True, shift=True)):
            _add_keymap_entry(
                kc,
                name=km_name,
                space_type=space,
                idname='paint_system.new_image_layer',
                key='N',
                properties={'image_add_type': 'NEW', 'skip_dialog': True},
                **_mods,
            )

        # 3DPainter 포크: 도구 전환 — M(사각/원 토글), L(라쏘/다각형 토글), B(브러시)
        _add_keymap_entry(
            kc, name=km_name, space_type=space,
            idname='paint_system.cycle_shape_tool', key='M')
        _add_keymap_entry(
            kc, name=km_name, space_type=space,
            idname='paint_system.cycle_lasso_tool', key='L')
        _add_keymap_entry(
            kc, name=km_name, space_type=space,
            idname='paint_system.set_brush_tool', key='B')

        # 3DPainter 포크: P = 필압 → 불투명도, Shift+P = 필압 → 크기 토글
        # (P/Shift+P 모두 블렌더 기본 Image Paint 키맵에서 비어 있다. 참고로
        #  Shift+S는 블렌더 기본으로 use_smooth_stroke 토글에 잡혀 있다)
        _add_keymap_entry(
            kc, name=km_name, space_type=space,
            idname='paint_system.toggle_pressure_strength', key='P')
        _add_keymap_entry(
            kc, name=km_name, space_type=space,
            idname='paint_system.toggle_pressure_size', key='P', shift=True)

        # 3DPainter 포크: Q = 팔레트 피커 팝업 토글 (포토샵 Swatches 패널 대용)
        # 블렌더 기본 Q는 Screen 키맵의 Quick Favorites 이지만, 애드온 Image
        # Paint 키맵이 우선하므로 텍스처 페인트 중에는 팔레트가 먼저 뜬다.
        _add_keymap_entry(
            kc, name=km_name, space_type=space,
            idname='paint_system.toggle_palette_popup', key='Q')
        # 팝오버에 넘기는 전용 키맵 — 열린 팝오버에서 Q 를 다시 누르면 닫힌다
        from .operators.painting_ux_operators import PALETTE_POPOVER_KEYMAP
        _add_keymap_entry(
            kc, name=PALETTE_POPOVER_KEYMAP, space_type='EMPTY',
            idname='paint_system.toggle_palette_popup', key='Q')

        # 3DPainter 포크: F 누른 채 상하 드래그 = 브러시 크기, 떼면 확정
        # (블렌더 기본 F 반경 조절을 대체 — Shift+F 강도·Ctrl+F 회전은 그대로)
        _add_keymap_entry(
            kc, name=km_name, space_type=space,
            idname='paint_system.drag_brush_size', key='F')

        # Color Sampler ('I') and Toggle Erase Alpha ('E')
        _add_keymap_entry(
            kc,
            name=km_name,
            space_type=space,
            idname='paint_system.color_sample',
            key='I',
        )
        _add_keymap_entry(
            kc,
            name=km_name,
            space_type=space,
            idname='paint_system.toggle_brush_erase_alpha',
            key='E',
        )
    except Exception:
        # Keymap setup is best-effort; failures shouldn't block add-on load
        pass


def unregister() -> None:
    for km, kmi in addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass

    addon_keymaps.clear()
