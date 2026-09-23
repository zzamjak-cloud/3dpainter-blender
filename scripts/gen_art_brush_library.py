"""Art Brush Pack 텍스처 라이브러리(.blend) 생성기.

`operators/brushes/art/src/*.png` 를 이미지 팩된 ImageTexture 로 담아
`operators/brushes/art/art_textures.blend` 를 만든다.

왜 PNG 를 직접 읽지 않고 .blend 로 링크하는가:
Blender 4.3+ 의 활성 브러시는 보통 Essentials 에서 **링크된** 데이터블록이다.
링크된 ID 는 로컬 ID 를 가리킬 수 없어 `brush.mask_texture = <로컬 텍스처>`
대입이 예외 없이 무시된다. 반면 **링크된** 텍스처는 정상적으로 붙는다.

실행:
    BLENDER_USER_EXTENSIONS="$(mktemp -d)" \
    blender --background --factory-startup --python scripts/gen_art_brush_library.py

BLENDER_USER_EXTENSIONS 격리 필수: 확장이 비활성인 --factory-startup 이 사용자
공용 휠(.local/site-packages)을 정리해 psd-tools 등이 사라진다.
"""

import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "operators" / "brushes" / "art" / "src"
OUT_BLEND = ROOT / "operators" / "brushes" / "art" / "art_textures.blend"

# art_brushes.py 와 맞춰야 하는 접두사
PREFIX = ".PS_art_"


def main() -> None:
    if not SRC_DIR.exists():
        sys.exit(f"source textures not found: {SRC_DIR}")

    # 링크 대상만 남도록 기본 씬의 데이터는 건드리지 않고 텍스처만 추가한다
    count = 0
    for path in sorted(SRC_DIR.glob("*.png")):
        name = PREFIX + path.stem
        img = bpy.data.images.load(str(path))
        img.name = name
        img.pack()          # 라이브러리 하나로 자족하도록 픽셀을 포함
        img.use_fake_user = True
        tex = bpy.data.textures.new(name, 'IMAGE')
        tex.image = img
        tex.use_fake_user = True   # 사용자가 없어도 저장되게
        count += 1
        print(f"  packed {name}")

    OUT_BLEND.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT_BLEND), compress=True)
    print(f"wrote {OUT_BLEND} ({count} textures)")


if __name__ == "__main__":
    main()
