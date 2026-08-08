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

        # 3DPainter 포크: Alt+클릭 스포이드 (포토샵식 — 합성 결과에서 색 추출)
        # 래퍼 오퍼레이터 안에서 모달을 중첩 호출하면 릴리스 이벤트를 놓쳐
        # 모달이 갇힐 수 있으므로 네이티브 오퍼레이터에 직접 바인딩한다
        if ENABLE_ALT_CLICK_EYEDROPPER:
            _add_keymap_entry(
                kc,
                name=km_name,
                space_type=space,
                idname='paint.sample_color',
                key='LEFTMOUSE',
                alt=True,
                properties={'merged': True, 'palette': False},
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
