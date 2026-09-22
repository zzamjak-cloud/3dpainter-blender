"""Art Brush Pack — 널리 쓰이는 회화·드로잉 브러시 프리셋.

에셋 라이브러리를 건드리지 않는 것이 이 모듈의 핵심 제약이다.

Blender 4.3+ 부터 브러시는 에셋이고, `Paint.brush` 는 읽기 전용이라
브러시 데이터블록을 새로 만들어 고르게 하려면 `asset_mark()` 로 에셋 등록을
해야 한다. 그러면 애셋 브라우저·브러시 셸프에 3DPainter 브러시가 쌓이므로,
여기서는 **브러시를 만들지 않고 현재 활성 브러시에 프리셋을 덮어쓴다.**
생성되는 데이터블록은 이름이 `.` 로 시작하는 이미지·텍스처뿐이라
UI 목록에도, 에셋 라이브러리에도 나타나지 않는다.
"""

from __future__ import annotations

import math
from pathlib import Path

import bpy

# 아트 브러시가 손댄 브러시임을 표시 (패널에서 활성 프리셋 강조용)
ART_BRUSH_KEY = "ps_art_brush"

_TWO_PI = math.pi * 2.0

# 데이터블록 이름 접두사 — `.` 로 시작하면 Blender UI 목록에서 숨겨진다
_DATA_PREFIX = ".PS_art_"


def _art_dir() -> Path:
    return Path(__file__).resolve().parent / "art"


# ---------------------------------------------------------------- 슬롯 프리셋

# 획 진행 방향을 따라 도장을 회전 — 붓결이 획을 따라 눕는다
_RAKE = {"mask_map_mode": 'VIEW_PLANE', "use_rake": True, "use_random": False}
# 도장마다 무작위 회전 — 반복 패턴이 눈에 띄지 않는다
_RANDOM = {"mask_map_mode": 'VIEW_PLANE', "use_rake": False,
           "use_random": True, "random_angle": _TWO_PI}
# 회전 없이 고정 — 치즐 마커처럼 팁 각도가 의미 있는 경우
_FIXED = {"mask_map_mode": 'VIEW_PLANE', "use_rake": False, "use_random": False}
# 화면에 깔린 결을 획이 통과 — 캔버스·종이 질감
_TILED = {"mask_map_mode": 'TILED', "use_rake": False, "use_random": False}

# 물감 입자(TEX 슬롯)는 화면에 고정된 결로 깔아 둔다
_GRAIN = {"map_mode": 'TILED'}

# 프리셋을 갈아탈 때 이전 프리셋의 잔재가 남지 않도록 먼저 깔아 두는 기준값
_BASELINE = {
    "strength": 1.0,
    "spacing": 8,
    "jitter": 0.0,
    "hardness": 0.5,
    "flow": 1.0,
    "density": 1.0,
    "wet_mix": 0.0,
    "rate": 0.1,
    "tip_roundness": 1.0,
    "tip_scale_x": 1.0,
    "blend": 'MIX',
    "stroke_method": 'SPACE',
    "use_accumulate": False,
    "use_pressure_size": True,
    "use_pressure_strength": True,
    "use_pressure_jitter": False,
    "use_smooth_stroke": False,
}


# ------------------------------------------------------------------ 브러시 정의

# (id, 표시 이름, 설명, 모양 마스크 PNG, 마스크 슬롯, 입자 PNG, 브러시 속성)
BASIC = (
    ("hard_round", "Hard Round", "경계가 또렷한 기본 원형 브러시 — 선화·러프의 출발점",
     None, None, None,
     {"hardness": 0.98, "spacing": 4, "use_pressure_strength": False}),
    ("soft_round", "Soft Round", "가장자리가 부드러운 원형 — 그라데이션과 블렌딩",
     None, None, None,
     {"hardness": 0.0, "spacing": 4, "strength": 0.85}),
    ("airbrush", "Airbrush", "누르고 있으면 계속 쌓이는 에어브러시 — 부드러운 음영",
     None, None, None,
     {"hardness": 0.0, "spacing": 4, "strength": 0.25,
      "stroke_method": 'AIRBRUSH', "rate": 0.06, "use_accumulate": True,
      "use_pressure_size": False}),
    ("hard_round_opacity", "Hard Round Opacity",
     "크기는 고정, 필압이 불투명도만 바꾸는 포토샵식 기본 브러시",
     None, None, None,
     {"hardness": 0.92, "spacing": 4, "use_pressure_size": False}),
)

DRAWING = (
    ("pencil", "Pencil", "종이 결에 흑연이 걸리는 연필 — 러프 스케치",
     "pencil.png", _RANDOM, "paper_grain.png",
     {"strength": 0.55, "spacing": 6, "hardness": 0.6,
      "use_accumulate": True}),
    ("ink_pen", "Ink Pen", "번짐 없는 단단한 잉크 펜 — 필압으로 굵기 조절",
     "ink.png", _FIXED, None,
     {"strength": 1.0, "spacing": 3, "hardness": 0.95,
      "use_pressure_strength": False}),
    ("marker", "Chisel Marker", "납작한 치즐 팁 마커 — 각진 면과 넓은 획",
     "marker.png", _FIXED, None,
     {"strength": 0.85, "spacing": 4, "use_accumulate": True,
      "use_pressure_size": False, "use_pressure_strength": False}),
    ("crayon", "Crayon", "왁스가 오돌토돌 얹히는 크레용",
     "crayon.png", _RANDOM, "paper_grain.png",
     {"strength": 0.8, "spacing": 8, "use_accumulate": True}),
    ("chalk", "Chalk / Pastel", "가루가 흩날리는 분필·파스텔 — 성기게 얹힌다",
     "crayon.png", _RANDOM, "canvas_grain.png",
     {"strength": 0.6, "spacing": 14, "jitter": 0.08,
      "use_accumulate": True}),
)

PAINTERLY = (
    ("dry_brush", "Dry Brush", "물감이 마른 붓 — 가늘게 끊기는 붓결",
     "dry_brush.png", _RAKE, "paper_grain.png",
     {"strength": 0.9, "spacing": 5}),
    ("oil_bristle", "Oil Bristle", "털이 촘촘한 유화 붓 — 결이 살아 있는 두툼한 획",
     "oil_bristle.png", _RAKE, "paper_grain.png",
     {"strength": 1.0, "spacing": 5}),
    ("impasto", "Impasto", "나이프로 눌러 편 두꺼운 물감 — 이랑이 남는다",
     "impasto.png", _RAKE, None,
     {"strength": 1.0, "spacing": 8, "use_pressure_strength": False}),
    ("gouache", "Gouache", "매트하게 덮이는 과슈 — 불투명 채색의 기본",
     "gouache.png", _RANDOM, "paper_grain.png",
     {"strength": 0.95, "spacing": 6}),
    ("watercolor", "Watercolor", "겹칠수록 진해지는 수채 — 가장자리에 물자국",
     "watercolor.png", _RANDOM, "paper_grain.png",
     {"strength": 0.3, "spacing": 16, "use_accumulate": True}),
    ("rake", "Rake", "빗살처럼 갈라지는 팬 브러시 — 결·머리카락·풀",
     "rake.png", _RAKE, None,
     {"strength": 0.9, "spacing": 4}),
)

TEXTURED = (
    ("charcoal", "Charcoal", "거칠게 부서지는 목탄 — 어두운 덩어리 잡기",
     "charcoal.png", _RANDOM, "paper_grain.png",
     {"strength": 0.7, "spacing": 9, "use_accumulate": True}),
    ("canvas_grain", "Canvas Grain", "캔버스 직조가 비쳐 나오는 획 — 회화 마감용",
     "canvas_grain.png", _TILED, None,
     {"strength": 0.8, "spacing": 5, "hardness": 0.5}),
    ("grunge", "Grunge", "갈라지고 벗겨진 표면 — 낡음·오염 디테일",
     "grunge.png", _RANDOM, None,
     {"strength": 0.85, "spacing": 12, "use_accumulate": True}),
    ("spatter", "Spatter", "튀긴 물감 방울 — 흩뿌리는 디테일",
     "spatter.png", _RANDOM, None,
     {"strength": 1.0, "spacing": 55, "jitter": 0.3,
      "use_pressure_strength": False}),
    ("sponge", "Sponge", "기공이 숭숭한 스펀지 — 바위·이끼 질감",
     "sponge.png", _RANDOM, None,
     {"strength": 0.8, "spacing": 28, "jitter": 0.12}),
    ("cloud", "Cloud", "뭉게뭉게 번지는 덩어리 — 구름·수풀 실루엣",
     "cloud.png", _RANDOM, None,
     {"strength": 0.5, "spacing": 18, "use_accumulate": True}),
)

# (카테고리 표시 이름, 항목들)
CATEGORIES = (
    ("Basic", BASIC),
    ("Drawing", DRAWING),
    ("Painterly", PAINTERLY),
    ("Textured", TEXTURED),
)

ART_BRUSHES = {
    item[0]: item for _label, group in CATEGORIES for item in group
}


def get_definition(brush_id: str):
    return ART_BRUSHES.get(brush_id)


# ------------------------------------------------------------------ 미리보기

_previews = None


def _load_previews():
    global _previews
    import bpy.utils.previews
    _previews = bpy.utils.previews.new()
    thumbs = _art_dir() / "thumbs"
    for brush_id in ART_BRUSHES:
        path = thumbs / f"{brush_id}.png"
        if path.exists():
            _previews.load(brush_id, str(path), 'IMAGE')


def get_art_icon(brush_id: str) -> int:
    """썸네일 icon_id. 미리보기 컬렉션은 처음 그릴 때 만든다."""
    global _previews
    if _previews is None:
        try:
            _load_previews()
        except Exception:
            return 0
    entry = _previews.get(brush_id)
    return entry.icon_id if entry else 0


def unload_art_previews():
    global _previews
    if _previews is not None:
        try:
            bpy.utils.previews.remove(_previews)
        except Exception:
            pass
        _previews = None


# ------------------------------------------------------------------ 적용 로직

def _ensure_texture(filename: str):
    """번들 PNG → 텍스처 데이터블록. 파일 단위로 재사용한다.

    이미지는 팩해 두어 애드온이 업데이트되어 경로가 바뀌어도
    사용자 파일에서 그대로 열린다."""
    path = _art_dir() / filename
    if not path.exists():
        return None
    stem = filename.rsplit(".", 1)[0]
    tex_name = f"{_DATA_PREFIX}{stem}"
    tex = bpy.data.textures.get(tex_name)
    if tex is not None and tex.image is not None:
        return tex

    img_name = f"{_DATA_PREFIX}{stem}"
    img = bpy.data.images.get(img_name)
    if img is None:
        # check_existing 으로 사용자가 띄워 둔 동일 경로 이미지를 집어다
        # 이름을 바꿔 버리지 않도록 항상 새로 읽는다
        img = bpy.data.images.load(str(path), check_existing=False)
        img.name = img_name
    try:
        if not img.packed_file:
            img.pack()
    except RuntimeError:
        pass

    if tex is None:
        tex = bpy.data.textures.new(tex_name, 'IMAGE')
    tex.image = img
    return tex


def _apply_props(target, props) -> None:
    # 버전별 속성 부재·이름 변경은 조용히 건너뛴다
    for key, value in props.items():
        try:
            setattr(target, key, value)
        except (AttributeError, TypeError):
            pass


def _apply_slot(slot, props) -> None:
    if not props:
        return
    _apply_props(slot, props)
    # mask_map_mode 가 없는 버전을 위한 대체 경로
    mode = props.get("mask_map_mode")
    if mode is not None and getattr(slot, "mask_map_mode", None) != mode:
        try:
            slot.map_mode = mode
        except (AttributeError, TypeError):
            pass


def clear_art_brush(brush) -> None:
    """활성 브러시를 텍스처 없는 기본 상태로 되돌린다."""
    if brush is None:
        return
    brush.texture = None
    brush.mask_texture = None
    _apply_props(brush, _BASELINE)
    brush.pop(ART_BRUSH_KEY, None)


def apply_art_brush(brush, brush_id: str) -> bool:
    """활성 브러시에 아트 브러시 프리셋을 덮어쓴다.

    브러시 데이터블록을 새로 만들지 않으므로 에셋 라이브러리에는
    아무것도 추가되지 않는다."""
    definition = ART_BRUSHES.get(brush_id)
    if brush is None or definition is None:
        return False
    _bid, _label, _desc, mask_file, mask_slot, grain_file, props = definition

    # 이전 프리셋 잔재 제거 후 기준값 → 프리셋 순으로 덮어쓴다
    brush.texture = None
    brush.mask_texture = None
    _apply_props(brush, _BASELINE)

    if mask_file:
        tex = _ensure_texture(mask_file)
        if tex is not None:
            brush.mask_texture = tex
            _apply_slot(brush.mask_texture_slot, mask_slot)
    if grain_file:
        tex = _ensure_texture(grain_file)
        if tex is not None:
            brush.texture = tex
            _apply_props(brush.texture_slot, _GRAIN)

    _apply_props(brush, props)
    brush[ART_BRUSH_KEY] = brush_id

    # 정밀도 기본값이 나중에 spacing·필압을 덮어쓰지 않도록 적용 완료로 표시
    try:
        from . import BRUSH_PRECISION_KEY, BRUSH_PRECISION_VERSION
        brush[BRUSH_PRECISION_KEY] = BRUSH_PRECISION_VERSION
    except ImportError:
        pass
    return True


def get_active_art_brush(brush) -> str:
    return brush.get(ART_BRUSH_KEY, "") if brush is not None else ""


__all__ = [
    "ART_BRUSH_KEY",
    "CATEGORIES",
    "ART_BRUSHES",
    "apply_art_brush",
    "clear_art_brush",
    "get_active_art_brush",
    "get_art_icon",
    "get_definition",
    "unload_art_previews",
]
