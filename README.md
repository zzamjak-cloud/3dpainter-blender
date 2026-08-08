# 3DPainter

**포토샵처럼 쓰는 블렌더 손맵(핸드페인팅) 텍스처 페인팅 애드온.**
[Paint System](https://github.com/natapol2547/paintsystem) (Tawan Sunflower, GPL-3.0-or-later)의 포크로, 레이어 기반 Diffuse 드로잉 워크플로에 필요한 기능을 추가·확장합니다. 라이선스는 원본과 동일하게 GPL-3.0-or-later를 유지합니다.

## 설치

1. [Releases](https://github.com/zzamjak-cloud/3dpainter-blender/releases)에서 플랫폼에 맞는 zip 다운로드 (`windows_x64` / `macos_arm64`, 압축 풀지 않음)
2. 블렌더(**5.2 LTS 권장**) → Edit → Preferences → Add-ons → 우측 상단 **▼ → Install from Disk** → zip 선택
3. **원본 Paint System 애드온은 비활성화** — 같은 내부 구조를 등록하므로 동시 활성화 시 충돌
4. (Windows + 펜 타블렛) Preferences → Input → **Tablet API = Wintab** 으로 지정하면 Windows Ink를 꺼도 필압이 동작
5. (macOS) 입력 소스가 한글이면 블렌더 단축키가 막히는 OS 이슈가 있음 — 블렌더 사용 시 영문(ABC) 입력 소스 권장 (Input Source Pro 등으로 앱별 자동 전환 가능)

PSD 연동용 psd-tools 휠이 동봉되어 있어 별도 파이썬 패키지 설치는 필요 없습니다.

## 포크 추가 기능

### 드로잉
| 기능 | 사용법 |
|---|---|
| 스포이드 | **Alt+클릭** — 합성 결과에서 색 추출, Alt 누르는 동안 스포이드 커서 유지 |
| 직선 | 클릭(앵커) 후 **Shift+클릭** — 연속 Shift+클릭으로 꺾은선 |
| 브러시 강도 | **숫자키 1~9** = 10~90%, **0** = 100% (숫자패드 지원) |
| 브러시 툴 복귀 | **B** |

### 레이어
| 기능 | 사용법 |
|---|---|
| 원클릭 새 레이어 | Add Image → New Image Layer (다이얼로그 없음, 커스텀은 별도 항목) 또는 **Ctrl/Cmd+Alt+Shift+N** |
| Quick Merge Down | **Ctrl/Cmd+E** 또는 패널 버튼 — 이미지 레이어끼리 베이크·다이얼로그 없이 즉시 병합 (블렌드 모드·불투명도 반영, 복잡한 레이어는 자동으로 베이크 병합 폴백) |

### 2D 텍스처 뷰
N패널 → Paint System → **2D View → Open 2D View**: 뷰포트가 분할되고 UV를 평면으로 펼친 캔버스가 열립니다. 3D/2D 어느 쪽을 클릭하든 페인팅 대상이 자동 전환되며 같은 레이어에 실시간 반영됩니다. UV를 수정했다면 **Refresh Canvas**.

### 선택 도구 (2D·3D 뷰 모두 지원)
| 기능 | 사용법 |
|---|---|
| 라쏘 / 다각형 라쏘 | 툴바 툴 선택 후 드래그/클릭, 또는 **Ctrl/Cmd+Shift+드래그**(임시). **L** 반복으로 자유곡선↔다각형 토글 |
| 사각 / 원 선택 | 툴바 툴, **M** 반복으로 사각↔원 토글, 드래그 중 **Shift** = 정비율 |
| 선택에서 제외 | 도구 사용 시 **Alt** |
| 채우기 | **Alt+Delete(Backspace)** — 선택 영역(없으면 레이어 전체)을 브러시 색으로 |
| 해제 | **Ctrl/Cmd+D**, 빈 공간 클릭, 패널 Clear |
| 반전 | 패널 Invert |

선택하면 안쪽만 칠해지고(스텐실 마스킹), 2D 뷰에는 점선(marching ants), 3D 뷰에는 표면 위 도트로 경계가 표시됩니다. 다각형 라쏘: 클릭으로 꼭짓점 → 시작점 클릭/Enter 닫기 → Backspace 점 취소.

### Photoshop (PSD) 왕복
2D View 패널의 **Photoshop (PSD)** 섹션:
- **Export / Import** — 레이어 스택 ↔ PSD (이름·순서·블렌드 모드·불투명도·표시 상태 보존, 픽셀 레이어만)
- **Open PS** — 연동된 PSD를 포토샵에서 즉시 열기
- **Live Sync** — 포토샵에서 저장하면 2초 내 자동 반영 (이름이 같은 레이어의 픽셀 갱신)

주의: 조정 레이어·텍스트·스마트 오브젝트·레이어 스타일은 왕복 시 보존되지 않습니다. 포토샵 전용 기능은 포토샵에서만 쓰고, 블렌더에서는 픽셀 레이어만 수정하는 운용을 권장합니다.

### Projection Tex (뷰 투사)
N패널 → **Projection Tex**: 2D 이미지(JPG/PNG/PSD)를 현재 뷰 화면 그대로 모델에 투사합니다 (병 위의 라벨 등).
1. **Import**로 이미지 등록 (다중 선택 가능, 썸네일 그리드 표시, 원본 파일 수정 시 자동 리로드)
2. **Place & Apply** — 뷰포트에 오버레이 표시: 드래그 이동, 휠 크기 조절 (뷰 고정)
3. **Enter** — 신규 레이어를 만들며 현재 카메라 각도 그대로 투사 (가려진 면 자동 제외, 투명 영역 보존). ESC 취소

투사는 화면 기준이므로 적용 전에 원하는 투사 방향으로 뷰를 맞춰두세요.

### 기타
- `.paint3d` 임포터 (웹앱 3DPainter 자산 이전용) — F3 검색 "Import .paint3d"
- 새 레이어 생성 시 뷰포트가 Solid+Texture 셰이딩이면 Material Preview로 자동 전환 (레이어 합성이 보이도록)

## 알려진 제약

- 레이어 합성은 **Material Preview** 셰이딩에서 보입니다. Solid+Texture 모드는 활성 이미지 한 장만 표시합니다.
- 직선/채우기 등 픽셀 직접 조작의 undo 통합이 불완전할 수 있습니다.
- 초기 스코프는 단일 오브젝트·단일 머티리얼 워크플로 기준입니다.
- 뷰포트 셰이딩·2D 뷰 레이아웃은 UI 데이터라, 파일을 열 때 **Load UI**가 켜져 있어야 복원됩니다 (Preferences → Save & Load).

## 개발

```bash
git clone https://github.com/zzamjak-cloud/3dpainter-blender
# 블렌더 extensions 디렉토리에 심볼릭 링크 (macOS 예시)
ln -sfn "$(pwd)/3dpainter-blender" ~/Library/Application\ Support/Blender/5.2/extensions/user_default/painter3d
# 배포 빌드 (플랫폼별 zip)
blender --command extension build --split-platforms --output-dir dist
```

포크 방침: 신규 기능은 별도 모듈(`operators/line_operators.py`, `lasso_operators.py`, `view2d_operators.py`, `psd_operators.py`, `projection_operators.py`, `merge_operators.py`, `panels/view2d_panels.py`, `projection_panels.py`)로 격리하고 원본 파일 수정은 최소 diff를 유지합니다. 업스트림 릴리스는 `upstream` 리모트에서 주기적으로 머지합니다.

---

# Paint System Addon (원본 README)
**Paint System** is a layer based add-on that resembles your typical drawing process. The Idea is that it simplifies the user’s experience by consolidating the main settings and tools into one place.

We are focused on replicating the UI and workflow you see in Photoshop, Clip Studio Paint, and Procreate allowing artists to have better experience transitioning from 2D painting software to Blender.

<img height="600" alt="image" src="https://github.com/user-attachments/assets/5824418f-5119-4d21-8ec3-29c2e171c2f9" />

## Documentation
[Paint System Documentation](https://app.notion.com/p/Paint-System-Documentation-2b02d71289d680f68ff9ef712d33716f)

### Have questions about the original addon? You can email the author anytime!
tawan.sunflower.nc@gmail.com
