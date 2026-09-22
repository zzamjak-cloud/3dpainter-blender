#!/usr/bin/env python3
"""Art Brush Pack 텍스처·썸네일 생성기.

번들 PNG 를 절차적으로 만들어 ``operators/brushes/art/`` 에 쓴다.
저장소에는 결과 PNG 만 배포되고(이 스크립트는 빌드에서 제외),
브러시 모양을 손볼 때만 다시 돌리면 된다.

실행: python3 scripts/gen_art_brush_textures.py
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ART_DIR = ROOT / "operators" / "brushes" / "art"
# 모양·결 원본 PNG 는 빌드에서 제외된다 — 배포본은 art_textures.blend 만 쓴다
SRC_DIR = ART_DIR / "src"
THUMB_DIR = ART_DIR / "thumbs"
SIZE = 512
THUMB = 128


# ---------------------------------------------------------------- 노이즈 유틸

def value_noise(h: int, w: int, fy: int, fx: int, rng) -> np.ndarray:
    """주기적(타일링 가능) 값 노이즈. fy/fx 는 세로·가로 셀 수."""
    g = rng.random((fy + 1, fx + 1))
    g[-1, :] = g[0, :]
    g[:, -1] = g[:, 0]
    y = np.linspace(0, fy, h, endpoint=False)
    x = np.linspace(0, fx, w, endpoint=False)
    y0 = y.astype(int)
    x0 = x.astype(int)
    ty = y - y0
    tx = x - x0
    ty = (ty * ty * (3 - 2 * ty))[:, None]
    tx = (tx * tx * (3 - 2 * tx))[None, :]
    g00 = g[np.ix_(y0, x0)]
    g01 = g[np.ix_(y0, x0 + 1)]
    g10 = g[np.ix_(y0 + 1, x0)]
    g11 = g[np.ix_(y0 + 1, x0 + 1)]
    top = g00 * (1 - tx) + g01 * tx
    bot = g10 * (1 - tx) + g11 * tx
    return top * (1 - ty) + bot * ty


def fbm(h: int, w: int, freq: int, octaves: int, rng, gain: float = 0.5) -> np.ndarray:
    total = np.zeros((h, w))
    amp = 1.0
    norm = 0.0
    for i in range(octaves):
        total += amp * value_noise(h, w, freq << i, freq << i, rng)
        norm += amp
        amp *= gain
    return total / norm


def coords(h: int = SIZE, w: int = SIZE):
    """중심 원점, 반지름 1 정규화 좌표 (y, x)."""
    y = (np.arange(h) - (h - 1) / 2) / (h / 2)
    x = (np.arange(w) - (w - 1) / 2) / (w / 2)
    return y[:, None] * np.ones((1, w)), np.ones((h, 1)) * x[None, :]


def smoothstep(edge0: float, edge1: float, v: np.ndarray) -> np.ndarray:
    t = np.clip((v - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def disc(softness: float = 0.12, radius: float = 0.96, ratio: float = 1.0) -> np.ndarray:
    """가장자리가 부드러운 타원 마스크."""
    y, x = coords()
    r = np.sqrt((x * ratio) ** 2 + y ** 2)
    return 1.0 - smoothstep(radius - softness, radius, r)


def normalize(a: np.ndarray) -> np.ndarray:
    lo, hi = float(a.min()), float(a.max())
    if hi - lo < 1e-6:
        return np.zeros_like(a)
    return (a - lo) / (hi - lo)


def save_alpha(name: str, alpha: np.ndarray) -> None:
    """LA PNG 로 저장 — 휘도는 흰색 고정, 모양은 알파 채널.

    브러시 색이 텍스처 밝기에 오염되지 않도록 RGB 를 흰색으로 둔다
    (Blender 의 브러시 마스크 샘플링은 휘도×알파를 쓴다)."""
    a = np.clip(alpha, 0.0, 1.0)
    h, w = a.shape
    buf = np.empty((h, w, 2), dtype=np.uint8)
    buf[..., 0] = 255
    buf[..., 1] = np.round(a * 255).astype(np.uint8)
    SRC_DIR.mkdir(parents=True, exist_ok=True)
    Image.fromarray(buf, mode="LA").save(SRC_DIR / f"{name}.png", optimize=True)


# ---------------------------------------------------------------- 모양 텍스처

def tex_dry_brush(rng):
    """마른 붓 — 가늘고 끊기는 붓결."""
    y, x = coords()
    streak = value_noise(SIZE, SIZE, 110, 5, rng)
    streak = smoothstep(0.42, 0.72, streak)
    gaps = smoothstep(0.30, 0.62, fbm(SIZE, SIZE, 4, 4, rng))
    a = streak * (0.35 + 0.65 * gaps)
    return a * disc(0.30, 0.98, 0.72)


def tex_oil_bristle(rng):
    """유화 붓 — 굵고 촘촘한 붓결."""
    streak = value_noise(SIZE, SIZE, 46, 4, rng)
    streak = smoothstep(0.30, 0.64, streak)
    body = 0.45 + 0.55 * fbm(SIZE, SIZE, 3, 3, rng)
    a = (0.45 + 0.55 * streak) * body
    return a * disc(0.22, 0.97, 0.80)


def tex_rake(rng):
    """레이크 — 넓게 벌어진 빗살."""
    y, _ = coords()
    jitter = (fbm(SIZE, SIZE, 3, 2, rng) - 0.5) * 0.12
    teeth = 0.5 + 0.5 * np.cos((y + jitter) * math.pi * 8.0)
    teeth = smoothstep(0.52, 0.86, teeth)
    wobble = 0.45 + 0.55 * smoothstep(0.25, 0.70, fbm(SIZE, SIZE, 5, 3, rng))
    return teeth * wobble * disc(0.14, 0.98, 0.70)


def tex_impasto(rng):
    """임파스토 — 두껍게 얹힌 물감 덩어리와 이랑."""
    # 이랑을 살짝 휘게 만들어 나이프로 눌러 편 물감처럼 보이게 한다
    y, x = coords()
    warp = (fbm(SIZE, SIZE, 4, 3, rng) - 0.5) * 0.5
    ridge = 0.5 + 0.5 * np.cos((y + warp) * math.pi * 9.0)
    ridge = 0.50 + 0.50 * smoothstep(0.20, 0.85, ridge)
    chunk = 0.65 + 0.35 * smoothstep(0.30, 0.75, fbm(SIZE, SIZE, 8, 3, rng))
    edge = fbm(SIZE, SIZE, 5, 3, rng)
    r = np.sqrt((x * 0.92) ** 2 + y ** 2) + (edge - 0.5) * 0.26
    body = 1.0 - smoothstep(0.78, 0.98, r)
    return np.clip(body * ridge * chunk * 1.25, 0.0, 1.0)


def tex_gouache(rng):
    """과슈 — 가장자리만 살짝 거친 매트한 면."""
    edge = fbm(SIZE, SIZE, 6, 3, rng)
    y, x = coords()
    r = np.sqrt(x ** 2 + y ** 2) + (edge - 0.5) * 0.12
    body = 1.0 - smoothstep(0.74, 0.96, r)
    return body * (0.86 + 0.14 * fbm(SIZE, SIZE, 4, 3, rng))


def tex_watercolor(rng):
    """수채 — 번진 얼룩과 가장자리 물자국."""
    warp = fbm(SIZE, SIZE, 3, 4, rng)
    y, x = coords()
    r = np.sqrt(x ** 2 + y ** 2) + (warp - 0.5) * 0.45
    body = 1.0 - smoothstep(0.55, 0.95, r)
    mottle = 0.55 + 0.45 * fbm(SIZE, SIZE, 6, 4, rng)
    # 물이 마르며 가장자리에 색이 몰리는 자국
    rim = smoothstep(0.62, 0.88, r) * (1.0 - smoothstep(0.88, 0.97, r))
    return np.clip(body * mottle * 0.80 + rim * 0.75, 0.0, 1.0)


def tex_charcoal(rng):
    """목탄 — 거친 입자와 뭉개진 가장자리."""
    grain = fbm(SIZE, SIZE, 26, 4, rng)
    grain = smoothstep(0.34, 0.80, grain)
    warp = fbm(SIZE, SIZE, 3, 3, rng)
    y, x = coords()
    r = np.sqrt((x * 0.95) ** 2 + y ** 2) + (warp - 0.5) * 0.30
    body = 1.0 - smoothstep(0.62, 0.98, r)
    return body * (0.25 + 0.75 * grain)


def tex_crayon(rng):
    """크레용 — 종이 요철에 왁스가 얹힌 오돌토돌한 입자."""
    # 방향성 있는 결보다 덩어리진 입자가 크레용·파스텔 느낌에 가깝다
    clump = smoothstep(0.34, 0.66, fbm(SIZE, SIZE, 12, 3, rng))
    grit = smoothstep(0.30, 0.75, fbm(SIZE, SIZE, 34, 3, rng))
    a = np.clip(0.25 + 0.55 * clump + 0.50 * grit * clump, 0.0, 1.0)
    return a * disc(0.26, 0.96, 0.92)


def tex_pencil(rng):
    """연필 — 종이 결에 걸린 흑연 입자."""
    grain = fbm(SIZE, SIZE, 40, 3, rng)
    grain = smoothstep(0.38, 0.82, grain)
    return (0.35 + 0.65 * grain) * disc(0.34, 0.92, 1.0)


def tex_ink(rng):
    """잉크 — 가장자리만 미세하게 흔들리는 단단한 면."""
    edge = fbm(SIZE, SIZE, 9, 3, rng)
    y, x = coords()
    r = np.sqrt((x * 1.05) ** 2 + y ** 2) + (edge - 0.5) * 0.07
    return 1.0 - smoothstep(0.86, 0.96, r)


def tex_marker(rng):
    """치즐 마커 — 모서리가 둥근 납작한 사각 팁."""
    y, x = coords()
    q = np.maximum(np.abs(x) / 0.98, np.abs(y) / 0.42)
    corner = np.sqrt(np.clip(np.abs(x) / 0.98 - 0.72, 0, None) ** 2
                     + np.clip(np.abs(y) / 0.42 - 0.72, 0, None) ** 2)
    shape = 1.0 - smoothstep(0.92, 1.0, np.maximum(q, 0.72 + corner))
    return shape * (0.90 + 0.10 * fbm(SIZE, SIZE, 12, 2, rng))


def tex_spatter(rng):
    """스패터 — 크기가 제각각인 물감 방울."""
    a = np.zeros((SIZE, SIZE))
    y, x = coords()
    for _ in range(90):
        cy, cx = rng.uniform(-0.82, 0.82, 2)
        rad = rng.uniform(0.02, 0.13) ** 1.4 * 3.2
        d = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
        a = np.maximum(a, 1.0 - smoothstep(rad * 0.55, rad, d))
    return a * disc(0.20, 1.0, 1.0)


def tex_sponge(rng):
    """스펀지 — 구멍이 숭숭 뚫린 기공 조직."""
    cells = fbm(SIZE, SIZE, 9, 3, rng)
    holes = smoothstep(0.42, 0.56, cells)
    fine = smoothstep(0.40, 0.70, fbm(SIZE, SIZE, 24, 2, rng))
    return holes * (0.45 + 0.55 * fine) * disc(0.18, 0.98, 1.0)


def tex_cloud(rng):
    """구름 — 뭉게뭉게 부드러운 덩어리 (배경·수풀 스탬프)."""
    n = fbm(SIZE, SIZE, 4, 5, rng)
    y, x = coords()
    r = np.sqrt(x ** 2 + y ** 2)
    body = 1.0 - smoothstep(0.35, 1.0, r)
    return np.clip(smoothstep(0.34, 0.78, n) * body * 1.3, 0.0, 1.0)


def tex_grunge(rng):
    """그런지 — 갈라지고 벗겨진 표면."""
    n = fbm(SIZE, SIZE, 6, 5, rng)
    crack = 1.0 - smoothstep(0.015, 0.09, np.abs(n - 0.5))
    surface = 0.35 + 0.65 * smoothstep(0.28, 0.78, fbm(SIZE, SIZE, 16, 4, rng))
    return np.clip(surface - crack * 0.9, 0.0, 1.0) * disc(0.22, 0.99, 1.0)


def tex_canvas_grain(rng):
    """캔버스 결 — 타일링되는 직조 패턴 (TILED 매핑용)."""
    t = np.linspace(0, 2 * math.pi * 26, SIZE, endpoint=False)
    weave_x = (0.5 + 0.5 * np.sin(t))[None, :] * np.ones((SIZE, 1))
    weave_y = (0.5 + 0.5 * np.sin(t))[:, None] * np.ones((1, SIZE))
    weave = np.maximum(weave_x, weave_y)
    noise = fbm(SIZE, SIZE, 16, 3, rng)
    return np.clip(0.55 * weave + 0.45 * noise, 0.0, 1.0)


def tex_paper_grain(rng):
    """종이 결 — 물감 입자용 미세 노이즈 (TEX 슬롯 공용)."""
    fine = fbm(SIZE, SIZE, 30, 4, rng)
    broad = fbm(SIZE, SIZE, 6, 3, rng)
    return np.clip(0.35 + 0.65 * normalize(fine * 0.7 + broad * 0.3), 0.0, 1.0)


SHAPES = {
    "dry_brush": tex_dry_brush,
    "oil_bristle": tex_oil_bristle,
    "rake": tex_rake,
    "impasto": tex_impasto,
    "gouache": tex_gouache,
    "watercolor": tex_watercolor,
    "charcoal": tex_charcoal,
    "crayon": tex_crayon,
    "pencil": tex_pencil,
    "ink": tex_ink,
    "marker": tex_marker,
    "spatter": tex_spatter,
    "sponge": tex_sponge,
    "cloud": tex_cloud,
    "grunge": tex_grunge,
    "canvas_grain": tex_canvas_grain,
    "paper_grain": tex_paper_grain,
}


# ------------------------------------------------------------------- 썸네일

def round_tip(n: int, hardness: float) -> np.ndarray:
    """텍스처 없는 브러시용 원형 팁 (hardness 0=에어브러시, 1=하드)."""
    y = (np.arange(n) - (n - 1) / 2) / (n / 2)
    r = np.sqrt(y[:, None] ** 2 + y[None, :] ** 2)
    inner = max(0.0, min(hardness, 0.98))
    return 1.0 - smoothstep(inner, 1.0, r)


def stamp_stroke(tip: np.ndarray, spacing: float, strength: float,
                 random_angle: bool, rng, size: int = THUMB * 2) -> np.ndarray:
    """S 자 획을 따라 팁을 찍어 스트로크 미리보기를 만든다.

    실제 브러시처럼 스탬프를 겹치되 누적이 아니라 최대값으로 합성한다
    (한 획 안에서 알파가 금세 1.0 으로 차 텍스처가 사라지는 것을 막는다)."""
    canvas = np.zeros((size, size))
    tip_img = Image.fromarray(
        np.round(np.clip(tip, 0, 1) * 255).astype(np.uint8), mode="L")

    # 획 경로를 촘촘히 뽑아 호 길이 기준으로 스탬프 간격을 맞춘다
    ts = np.linspace(0.0, 1.0, 600)
    px = 0.13 + 0.74 * ts
    py = 0.68 - 0.36 * ts - 0.12 * np.sin(ts * math.pi * 1.6)
    seg = np.hypot(np.diff(px), np.diff(py))
    arc = np.concatenate([[0.0], np.cumsum(seg)])

    base = size * 0.30
    step = max(1.0 / size, spacing * base / size)
    marks = np.arange(0.0, arc[-1], step)
    for m in marks:
        i = int(np.searchsorted(arc, m))
        i = min(i, len(ts) - 1)
        t = ts[i]
        press = math.sin(math.pi * min(1.0, 0.08 + t * 0.95)) ** 0.5
        d = max(4, int(base * (0.42 + 0.58 * press)))
        s_img = tip_img.resize((d, d), Image.BILINEAR)
        if random_angle:
            s_img = s_img.rotate(rng.uniform(0, 360), resample=Image.BILINEAR)
        arr = np.asarray(s_img, dtype=np.float32) / 255.0
        arr = arr * strength * (0.6 + 0.4 * press)
        x0 = int(px[i] * size) - d // 2
        y0 = int(py[i] * size) - d // 2
        xs, xe = max(0, x0), min(size, x0 + d)
        ys, ye = max(0, y0), min(size, y0 + d)
        if xs >= xe or ys >= ye:
            continue
        sub = arr[ys - y0:ye - y0, xs - x0:xe - x0]
        np.maximum(canvas[ys:ye, xs:xe], sub, out=canvas[ys:ye, xs:xe])
    return np.clip(canvas, 0.0, 1.0)


def save_thumb(name: str, alpha: np.ndarray) -> None:
    """흰 획 + 알파 — 밝은/어두운 UI 테마 어디서든 읽힌다."""
    a = np.clip(alpha, 0.0, 1.0)
    h, w = a.shape
    buf = np.empty((h, w, 4), dtype=np.uint8)
    buf[..., :3] = 235
    buf[..., 3] = np.round(a * 255).astype(np.uint8)
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    img = Image.fromarray(buf, mode="RGBA")
    if img.width != THUMB:
        img = img.resize((THUMB, THUMB), Image.LANCZOS)
    img.save(THUMB_DIR / f"{name}.png", optimize=True)


# 썸네일용 (브러시 id, 모양 텍스처 또는 hardness, 간격, 강도, 랜덤 회전)
THUMBS = (
    ("hard_round", None, 0.92, 0.04, 1.00, False),
    ("soft_round", None, 0.10, 0.04, 0.90, False),
    ("airbrush", None, 0.00, 0.04, 0.45, False),
    ("hard_round_opacity", None, 0.80, 0.04, 0.70, False),
    ("pencil", "pencil", None, 0.07, 0.75, True),
    ("ink_pen", "ink", None, 0.05, 1.00, False),
    ("marker", "marker", None, 0.07, 0.85, False),
    ("crayon", "crayon", None, 0.09, 0.90, True),
    ("chalk", "crayon", None, 0.14, 0.70, True),
    ("dry_brush", "dry_brush", None, 0.07, 0.95, False),
    ("oil_bristle", "oil_bristle", None, 0.07, 1.00, False),
    ("impasto", "impasto", None, 0.10, 1.00, False),
    ("gouache", "gouache", None, 0.08, 0.95, False),
    ("watercolor", "watercolor", None, 0.16, 0.55, True),
    ("charcoal", "charcoal", None, 0.11, 0.85, True),
    ("canvas_grain", None, 0.50, 0.05, 0.95, False),
    ("grunge", "grunge", None, 0.16, 0.85, True),
    ("spatter", "spatter", None, 0.55, 1.00, True),
    ("sponge", "sponge", None, 0.35, 0.85, True),
    ("cloud", "cloud", None, 0.18, 0.90, True),
    ("rake", "rake", None, 0.07, 0.95, False),
)


def main() -> None:
    shapes = {}
    for i, (name, fn) in enumerate(SHAPES.items()):
        rng = np.random.default_rng(1000 + i)
        a = np.clip(fn(rng), 0.0, 1.0)
        shapes[name] = a
        save_alpha(name, a)
        print(f"  texture  {name}.png")

    for i, (bid, shape, hardness, spacing, strength, rot) in enumerate(THUMBS):
        rng = np.random.default_rng(2000 + i)
        if shape is None:
            tip = round_tip(192, hardness)
        else:
            tip = shapes[shape]
        stroke = stamp_stroke(tip, spacing, strength, rot, rng)
        if bid == "canvas_grain":
            # TILED 매핑이라 획 모양이 아니라 화면에 깔린 결이 획을 깎아낸다
            n = stroke.shape[0]
            grain = np.asarray(Image.fromarray(
                np.round(shapes["canvas_grain"] * 255).astype(np.uint8), mode="L"
            ).resize((n, n), Image.BILINEAR), dtype=np.float32) / 255.0
            stroke = stroke * (0.20 + 0.80 * grain)
        save_thumb(bid, stroke)
        print(f"  thumb    {bid}.png")


if __name__ == "__main__":
    main()
