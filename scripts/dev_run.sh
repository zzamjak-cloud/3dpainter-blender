#!/usr/bin/env bash
#
# 개발용 실행 (macOS): 저장소 소스를 설치된 3DPainter 확장 폴더에 동기화하고 Blender 를 띄운다.
#
# 사용법:
#   ./scripts/dev_run.sh              # 동기화 후 Blender 실행 (이미 실행 중이면 동기화만)
#   ./scripts/dev_run.sh --no-launch  # 동기화만
#   BLENDER=/path/to/Blender ./scripts/dev_run.sh
#
# 전제: 확장이 zip 으로 한 번은 설치돼 있어야 한다 (휠 설치·활성화는 Blender 가 담당).

set -euo pipefail

EXT_ID="painter3d"
BLENDER="${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"
LAUNCH=1
[[ "${1:-}" == "--no-launch" ]] && LAUNCH=0

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -x "$BLENDER" ]]; then
  echo "error: Blender 실행 파일을 찾을 수 없습니다: $BLENDER" >&2
  echo "       BLENDER 환경 변수로 경로를 지정하세요." >&2
  exit 1
fi

# "Blender 5.2.0 LTS" → 5.2
VERSION="$("$BLENDER" --version 2>/dev/null | awk '/^Blender /{split($2, v, "."); print v[1] "." v[2]; exit}')"
EXT_ROOT="$HOME/Library/Application Support/Blender/$VERSION/extensions"

# 어느 저장소(repo)에 설치돼 있든 찾아서 그 자리에 덮어쓴다 (.local 은 휠 전용이라 제외)
TARGET=""
for d in "$EXT_ROOT"/*/"$EXT_ID"; do
  [[ -f "$d/blender_manifest.toml" ]] && { TARGET="$d"; break; }
done
if [[ -z "$TARGET" ]]; then
  echo "error: Blender $VERSION 에 $EXT_ID 확장이 설치돼 있지 않습니다." >&2
  echo "       먼저 ./scripts/build.sh 로 만든 zip 을 Install from Disk 로 한 번 설치하세요." >&2
  exit 1
fi

# 제외 목록은 blender_manifest.toml 의 [build] paths_exclude_pattern 과 맞춘다
rsync -a --delete \
  --exclude '.git/' --exclude '.github/' --exclude '.gitignore' --exclude '.prettierrc' \
  --exclude '.serena/' --exclude '.ruff_cache/' --exclude '.omc/' --exclude '.venv/' --exclude '.python-version' \
  --exclude 'pyproject.toml' --exclude 'uv.lock' --exclude 'run_tests.py' \
  --exclude 'operators/brushes/art/src/' \
  --exclude 'donation_cache.json' --exclude 'version_cache.json' \
  --exclude '__pycache__/' --exclude '*.py[cod]' --exclude '.DS_Store' --exclude '*.blend1' \
  --exclude '/scripts/' --exclude '/dist/' --exclude '*.zip' \
  "$REPO_ROOT/" "$TARGET/"

echo "동기화 완료 → $TARGET"

if pgrep -x Blender >/dev/null 2>&1; then
  echo "Blender 실행 중 — F3 > Reload Scripts 또는 재시작해야 반영됩니다."
elif [[ $LAUNCH -eq 1 ]]; then
  "$BLENDER" >/dev/null 2>&1 &
  echo "Blender $VERSION 실행"
fi
