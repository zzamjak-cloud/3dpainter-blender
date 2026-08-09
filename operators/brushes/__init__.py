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

    # 번들 텍스처 기반 아트 브러시 (blend 파일 버전에 의존하지 않음)
    create_art_brushes()

    # For blender 4.3
    if bpy.app.version >= (4, 3, 0):
        for brush in bpy.data.brushes:
            if brush.name.startswith(BRUSH_PREFIX):
                brush.asset_mark()


# Gouache 브러시와 동일한 2중 구성: TEX 슬롯 = 물감 결(입자), MASK 슬롯 = 획 모양
_TWO_PI = 6.283185307
_GRAIN_SLOT = {"map_mode": "VIEW_PLANE", "use_rake": True,
               "use_random": True, "random_angle": _TWO_PI}
_SHAPE_RAKE = {"map_mode": "VIEW_PLANE", "use_rake": True}
_SHAPE_RANDOM = {"map_mode": "VIEW_PLANE", "use_random": True,
                 "random_angle": _TWO_PI}

# (이름, 결 텍스처, 모양 마스크, 브러시 설정, 결 슬롯 설정, 마스크 슬롯 설정)
_ART_BRUSHES = (
    ("PS_Dry_Bristle", "grain.png", "dry_bristle.png",
     {"strength": 0.85, "spacing": 4, "use_pressure_strength": True},
     _GRAIN_SLOT, _SHAPE_RAKE),
    ("PS_Rake", "grain.png", "rake.png",
     {"strength": 0.9, "spacing": 5, "use_pressure_strength": True},
     _GRAIN_SLOT, _SHAPE_RAKE),
    ("PS_Oil_Impasto", "grain.png", "oil_impasto.png",
     {"strength": 1.0, "spacing": 8, "use_pressure_strength": True},
     _GRAIN_SLOT, _SHAPE_RAKE),
    ("PS_Charcoal", "grain.png", "charcoal.png",
     {"strength": 0.7, "spacing": 8, "use_accumulate": True,
      "use_pressure_strength": True},
     _GRAIN_SLOT, _SHAPE_RANDOM),
    ("PS_Watercolor", "grain.png", "watercolor.png",
     {"strength": 0.45, "spacing": 12, "use_accumulate": True,
      "use_pressure_strength": True},
     _GRAIN_SLOT, _SHAPE_RANDOM),
    ("PS_Sponge", "grain.png", "sponge.png",
     {"strength": 0.8, "spacing": 25},
     _GRAIN_SLOT, _SHAPE_RANDOM),
    ("PS_Spatter", "grain.png", "spatter.png",
     {"strength": 1.0, "spacing": 65, "jitter": 0.25},
     _GRAIN_SLOT, _SHAPE_RANDOM),
    ("PS_Canvas_Grain", None, "canvas_grain.png",
     {"strength": 0.75, "spacing": 8, "use_pressure_strength": True},
     None, {"map_mode": "TILED"}),
)


def _apply_props(target, props):
    # 버전별 API 차이(속성 부재/이름 변경)는 조용히 건너뛴다
    for key, value in props.items():
        try:
            setattr(target, key, value)
        except Exception:
            pass


# 브러시 정의가 바뀌면 올린다 — 낡은 버전은 재임포트 시 자동 교체된다
_ART_BRUSH_VERSION = 3


def _ensure_image_texture(textures_dir, filename):
    """번들 PNG → (텍스처, 경로). 텍스처는 파일 단위로 공유·재사용된다."""
    tex_path = textures_dir / filename
    if not tex_path.exists():
        return None, None
    # RGBA(흰색 RGB + 알파 모양) — 기존 Gouache 브러시와 동일하게 sRGB 유지
    img = bpy.data.images.load(str(tex_path), check_existing=True)
    try:
        img.reload()  # 같은 경로의 낡은 데이터블록이 재사용될 수 있어 픽셀 갱신
    except RuntimeError:
        pass
    tex_name = f".PS_tex_{filename.rsplit('.', 1)[0]}"
    tex = bpy.data.textures.get(tex_name)
    if tex is None:
        tex = bpy.data.textures.new(tex_name, 'IMAGE')
    tex.image = img
    return tex, tex_path


def create_art_brushes():
    """번들 PNG 텍스처로 회화 브러시를 코드로 생성한다 (최신 버전이면 건너뜀)"""
    textures_dir = _resolve_library_path("textures")
    for name, grain_file, mask_file, brush_props, grain_slot, mask_slot in _ART_BRUSHES:
        existing = bpy.data.brushes.get(name)
        if existing is not None:
            if existing.get("ps_art_brush_version", 0) >= _ART_BRUSH_VERSION:
                continue
            bpy.data.brushes.remove(existing)
        brush = bpy.data.brushes.new(name, mode='TEXTURE_PAINT')
        icon_source = None
        if grain_file:
            tex, _tex_path = _ensure_image_texture(textures_dir, grain_file)
            if tex is not None:
                brush.texture = tex
                _apply_props(brush.texture_slot, grain_slot)
        if mask_file:
            tex, tex_path = _ensure_image_texture(textures_dir, mask_file)
            if tex is not None:
                brush.mask_texture = tex
                _apply_props(brush.mask_texture_slot, mask_slot)
                icon_source = (tex_path, tex.image)  # 아이콘은 획 모양으로
        _apply_props(brush, brush_props)
        brush["ps_art_brush_version"] = _ART_BRUSH_VERSION
        if icon_source is not None:
            _set_brush_icon(brush, *icon_source)


def _set_brush_icon(brush, tex_path, img):
    # 4.x: 커스텀 아이콘 경로 지정 / 5.x(속성 제거됨): ID 프리뷰에 픽셀 주입
    if hasattr(brush, "use_custom_icon"):
        brush.use_custom_icon = True
        brush.icon_filepath = str(tex_path)
        return
    try:
        import numpy as np
        w, h = img.size
        step = max(1, w // 128, h // 128)
        px = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(px)
        px = px.reshape(h, w, 4)[::step, ::step]
        # 흰색 RGB + 알파 모양 이미지라 그대로 넣으면 흰 사각형이 된다
        # → 알파를 밝기로 바꿔 검정 바탕 위 흰 획으로 표시
        alpha = px[..., 3:4]
        out = np.concatenate([np.repeat(alpha, 3, axis=2), np.ones_like(alpha)], axis=2)
        preview = brush.preview_ensure()
        preview.image_size = (out.shape[1], out.shape[0])
        preview.image_pixels_float.foreach_set(np.ascontiguousarray(out.ravel()))
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
    "create_art_brushes",
    "enable_unified_color",
    "register_brush_auto_setup",
    "unregister_brush_auto_setup",
]


