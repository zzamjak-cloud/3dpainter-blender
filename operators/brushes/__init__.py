from __future__ import annotations

from pathlib import Path
from typing import Optional

import bpy

BRUSH_PREFIX = "PS_"

# 사용자가 수동 임포트한 브러시 표시 키 (자동 정리 예외 처리용)
MANUAL_IMPORT_KEY = "ps_manual_import"

def _resolve_library_path(filename: str = "brushes.blend") -> Path:
    """
    Resolve the absolute path to the given library filename.

    `brushes.blend` resides with this file.
    """
    folder_root = Path(__file__).resolve().parent
    return folder_root / filename


def get_brushes_from_library():
    """브러시 라이브러리를 현재 파일로 임포트한다.

    사용자가 패널의 "Import Paint System Brushes"를 눌렀을 때만 호출한다.
    자동(파일 로드/애드온 활성화) 호출은 하지 않는다 — 에셋 라이브러리에
    PS_ 브러시가 계속 되살아나는 문제 때문.
    """
    # Load the library file
    filepath = _resolve_library_path()
    if not filepath.exists():
        raise FileNotFoundError(f"Library file not found: {filepath}")

    # 3) Inspect the library for the node tree, then append it
    library_path_str = str(filepath)
    with bpy.data.libraries.load(library_path_str) as (lib_file, current_file):
        lib_brushes = lib_file.brushes
        current_brushes = current_file.brushes
        for brush in lib_brushes:
            if brush.startswith(BRUSH_PREFIX) and brush not in bpy.data.brushes:
                current_brushes.append(brush)

    # 예전 버전이 만들던 아트 브러시 잔재 정리
    _remove_legacy_art_brushes()

    # For blender 4.3
    # 사용자가 직접 임포트한 브러시임을 표시해 두어, 자동 정리 대상에서 제외한다.
    for brush in bpy.data.brushes:
        if brush.name.startswith(BRUSH_PREFIX):
            brush[MANUAL_IMPORT_KEY] = True
            if bpy.app.version >= (4, 3, 0):
                brush.asset_mark()


def _remove_legacy_art_brushes():
    """예전 버전이 자동 생성하던 아트 브러시·텍스처를 정리한다.
    (기존 사용자 파일에 남아 있는 데이터블록까지 제거)"""
    for brush in list(bpy.data.brushes):
        if "ps_art_brush_version" in brush.keys():
            try:
                bpy.data.brushes.remove(brush)
            except Exception:
                pass
    for tex in list(bpy.data.textures):
        if tex.name.startswith(".PS_tex_") and tex.users == 0:
            try:
                bpy.data.textures.remove(tex)
            except Exception:
                pass


def _remove_auto_imported_brushes():
    """예전 버전이 파일을 열 때마다 자동 임포트·에셋 등록하던 PS_ 브러시를 정리한다.

    사용자가 패널에서 직접 임포트한 브러시(``MANUAL_IMPORT_KEY`` 표시)는 남긴다.
    """
    for brush in list(bpy.data.brushes):
        if not brush.name.startswith(BRUSH_PREFIX):
            continue
        if brush.get(MANUAL_IMPORT_KEY):
            continue
        try:
            if getattr(brush, "asset_data", None) is not None:
                brush.asset_clear()
        except Exception:
            pass
        try:
            bpy.data.brushes.remove(brush)
        except Exception:
            pass


# ---- 자동 준비: 파일을 열 때마다 잔재 브러시를 정리하고 전경색을 브러시 간 공유 ----

from bpy.app.handlers import persistent  # noqa: E402


def enable_unified_color(scene) -> bool:
    """텍스처 페인트 통합 색상 활성화 (5.x: image_paint 산하 / 4.x: tool_settings 직속)"""
    ts = scene.tool_settings
    for holder in (getattr(ts, "image_paint", None), ts):
        ups = getattr(holder, "unified_paint_settings", None) if holder else None
        if ups is not None:
            ups.use_unified_color = True
            return True
    return False


def ensure_default_white_color(scene) -> None:
    """전경색이 초기값(검정)이면 흰색으로 교정.
    사용자가 고른 색은 건드리지 않도록 순수 검정(0,0,0)일 때만 바꾼다."""
    try:
        ts = scene.tool_settings
        for holder in (getattr(ts, "image_paint", None), ts):
            ups = getattr(holder, "unified_paint_settings", None) if holder else None
            if ups is not None and tuple(ups.color) == (0.0, 0.0, 0.0):
                ups.color = (1.0, 1.0, 1.0)
        brush = getattr(getattr(ts, "image_paint", None), "brush", None)
        if brush is not None and tuple(brush.color) == (0.0, 0.0, 0.0):
            brush.color = (1.0, 1.0, 1.0)
    except Exception:
        pass


# ---- 포토샵식 브러시 정밀도 ----

# 브러시 데이터블록에 남기는 "정밀도 기본값을 이미 적용했다" 표시.
# 값을 올리면 기존 파일의 브러시에도 새 기본값이 한 번 더 적용된다.
BRUSH_PRECISION_KEY = "ps_precision_version"
BRUSH_PRECISION_VERSION = 1


def _precision_prefs():
    from ...preferences import get_preferences
    return get_preferences(bpy.context)


def apply_brush_precision(brush, prefs=None) -> bool:
    """브러시 하나에 포토샵식 필압·정밀도 기본값을 적용한다.

    블렌더 기본 브러시(Paint Hard)는 ``use_pressure_size`` 와
    ``use_pressure_strength`` 가 **둘 다 꺼져 있어** 태블릿 필압이 아예 반영되지
    않는다. 포토샵 기본 브러시는 크기·불투명도 모두 필압을 따르므로 그에 맞추고,
    입력 샘플과 스탬프 간격도 획이 각지지 않는 값으로 낮춘다.
    """
    if brush is None or not getattr(brush, "use_paint_image", False):
        return False
    if prefs is None:
        prefs = _precision_prefs()
    values = (
        ("use_pressure_size", True),
        ("use_pressure_strength", True),
        ("input_samples", int(prefs.brush_input_samples)),
        ("spacing", int(prefs.brush_spacing)),
    )
    changed = False
    for attr, value in values:
        try:
            if getattr(brush, attr) != value:
                setattr(brush, attr, value)
                changed = True
        except (AttributeError, TypeError):
            pass
    brush[BRUSH_PRECISION_KEY] = BRUSH_PRECISION_VERSION
    return changed


def ensure_brush_precision(*_args) -> None:
    """활성 이미지 페인트 브러시에 정밀도 기본값을 **브러시당 1회** 적용한다.

    데이터블록에 표시를 남기므로, 이후 사용자가 필압을 끄거나 간격을 바꿔도
    다시 덮어쓰지 않는다. 5.x 의 브러시는 필요할 때 에셋에서 새로 로드되므로
    파일 단위가 아니라 브러시 단위로 걸어야 한다.
    """
    try:
        prefs = _precision_prefs()
        if not prefs.use_brush_precision:
            return
        paint = getattr(bpy.context.tool_settings, "image_paint", None)
        brush = getattr(paint, "brush", None) if paint else None
        if brush is None or brush.get(BRUSH_PRECISION_KEY) == BRUSH_PRECISION_VERSION:
            return
        apply_brush_precision(brush, prefs)
    except Exception:
        pass


@persistent
def _load_post_ensure_brushes(_filepath=None):
    # 브러시 자동 임포트는 하지 않는다. 예전 버전이 남긴 잔재만 정리한다.
    try:
        _remove_legacy_art_brushes()
        _remove_auto_imported_brushes()
    except Exception:
        pass
    # 브러시마다 색이 따로 놀지 않도록 통합 색상 활성화
    # (파일당 1회만 강제 — 이후 사용자가 끄면 존중)
    for scene in bpy.data.scenes:
        try:
            if not scene.get("ps_unified_color_init"):
                if enable_unified_color(scene):
                    scene["ps_unified_color_init"] = True
            # 전경색 최초 기본값이 검정으로 보이는 문제 → 파일당 1회 흰색으로 교정
            if not scene.get("ps_default_white_init"):
                ensure_default_white_color(scene)
                scene["ps_default_white_init"] = True
        except Exception:
            pass
    ensure_brush_precision()


def _startup_ensure_brushes():
    _load_post_ensure_brushes()
    return None


_precision_owner = object()


def register_brush_auto_setup():
    if _load_post_ensure_brushes not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post_ensure_brushes)
    # 브러시를 바꾸면 새 데이터블록이 로드될 수 있으므로 전환 시점마다 확인한다
    bpy.msgbus.subscribe_rna(
        key=(bpy.types.Paint, "brush"),
        owner=_precision_owner,
        args=(),
        notify=ensure_brush_precision,
    )
    # 애드온 활성화 직후 현재 세션에도 즉시 반영 (등록 컨텍스트 제한 회피)
    bpy.app.timers.register(_startup_ensure_brushes, first_interval=0.2)


def unregister_brush_auto_setup():
    bpy.msgbus.clear_by_owner(_precision_owner)
    if _load_post_ensure_brushes in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post_ensure_brushes)


__all__ = [
    "get_brushes_from_library",
    "enable_unified_color",
    "apply_brush_precision",
    "ensure_brush_precision",
    "register_brush_auto_setup",
    "unregister_brush_auto_setup",
]


