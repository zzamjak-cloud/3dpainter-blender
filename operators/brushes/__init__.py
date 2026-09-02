from __future__ import annotations

from pathlib import Path
from typing import Optional

import bpy

BRUSH_PREFIX = "PS_"

def _resolve_library_path(filename: str = "brushes.blend") -> Path:
    """
    Resolve the absolute path to the given library filename.

    `brushes.blend` resides with this file.
    """
    folder_root = Path(__file__).resolve().parent
    return folder_root / filename


def get_brushes_from_library():
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
    if bpy.app.version >= (4, 3, 0):
        for brush in bpy.data.brushes:
            if brush.name.startswith(BRUSH_PREFIX):
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


# ---- 자동 준비: 파일을 열 때마다 브러시를 갖추고 전경색을 브러시 간 공유 ----

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


@persistent
def _load_post_ensure_brushes(_filepath=None):
    try:
        get_brushes_from_library()
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


def _startup_ensure_brushes():
    _load_post_ensure_brushes()
    return None


def register_brush_auto_setup():
    if _load_post_ensure_brushes not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post_ensure_brushes)
    # 애드온 활성화 직후 현재 세션에도 즉시 반영 (등록 컨텍스트 제한 회피)
    bpy.app.timers.register(_startup_ensure_brushes, first_interval=0.2)


def unregister_brush_auto_setup():
    if _load_post_ensure_brushes in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post_ensure_brushes)


__all__ = [
    "get_brushes_from_library",
    "enable_unified_color",
    "register_brush_auto_setup",
    "unregister_brush_auto_setup",
]


