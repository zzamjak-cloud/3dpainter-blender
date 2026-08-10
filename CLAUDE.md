# CLAUDE.md

이 저장소에서 작업할 때의 프로젝트 규칙.

## 릴리즈 절차 (강제)

"릴리즈 해줘"라는 요청은 **아래 전 과정을 한 번에** 의미한다. GitHub Release 생성까지 완료해야 릴리즈다 — 태그만 푸시하면 GitHub 최신 릴리즈에 반영되지 않는다.

1. **커밋**: 미커밋 변경을 논리 단위로 커밋 (한국어 메시지, `feat:`/`fix:`/`refactor:`/`chore:` 프리픽스)
2. **버전 범프**: `blender_manifest.toml`의 `version` 갱신 — 사용자가 버전을 지정하지 않으면 patch 증가. `chore: v{버전} — {요약}` 커밋
3. **태그·푸시**: `git tag v{버전}` 후 현재 브랜치와 태그를 함께 푸시
4. **빌드**:
   ```bash
   BLENDER=/Applications/Blender.app/Contents/MacOS/Blender ./scripts/build.sh --split-platforms
   ```
   → `dist/painter3d-{버전}-macos_arm64.zip`, `dist/painter3d-{버전}-windows_x64.zip`
5. **GitHub Release 생성** (필수):
   ```bash
   gh release create v{버전} --repo zzamjak-cloud/3dpainter-blender \
     --title "3DPainter v{버전}" --notes "{한국어 변경 요약 + 설치 안내}" \
     dist/painter3d-{버전}-macos_arm64.zip dist/painter3d-{버전}-windows_x64.zip
   ```
   - 노트 형식은 v2.2.0/v2.3.0 릴리즈 참고: 변경 요약(추가 기능/수정) + 설치 안내(Blender 5.2 LTS 권장, Install from Disk, 원본 Paint System 애드온 비활성화)
6. 빌드 zip에 신규 리소스(텍스처 등)가 포함됐는지 `unzip -l`로 확인

주의: `main` 머지 후 릴리즈가 기본. 빌드 중 `Gouache ... not available. Keeping packed image` 경고는 무해함.
