# 리팩터링 분석 보고서 (2026-08-08)

성능 최적화 + 확장성 관점 전체 코드베이스 분석. 병렬 에이전트 3개(코어 데이터/그래프, 오퍼레이터, UI/등록)가 조사한 결과를 중복 제거 후 통합.

---

## A. 즉시 수정 대상 (리팩터링 이전의 실버그)

| # | 위치 | 내용 |
|---|------|------|
| A1 | `paintsystem/data.py:1524-1536` | `Layer.get_layer_data`가 `force_refresh` 후 **stale dict에서 재조회해 None 반환** — 재구축 비용만 치르고 결과를 버림. 마지막 줄을 `return layer`로. |
| A2 | `paintsystem/data.py:2107-2290` | `Channel.bake`의 `orig_*` 복원 변수 다수가 조건부 할당. `force_alpha=False`면 `data.py:2207`에서 `UnboundLocalError` → 바깥 except가 삼켜 조용히 실패. 에러 경로(2280-2286)도 미할당 변수 참조 시 채널 설정이 영구 변경된 채 방치. → 프로퍼티 스냅샷/복원 컨텍스트 매니저. |
| A3 | `operators/bake_operators.py:692-931`, `:566-678` | MergeDown/Up·TransferUV·ConvertToImage 4곳 모두 "레이어 끄기→베이크→원복"이 `try/finally` 없음. 베이크 예외 시 레이어 스택 비활성+블렌드 모드 유실. |
| A4 | `lasso_operators.py`, `projection_operators.py` 모달 4종 | `cancel()` 미정의 — 외부 중단(파일 로드 등) 시 `SpaceView3D.draw_handler` 영구 누수. |
| A5 | `operators/common.py:62-76` | `MultiMaterialOperator` 에러 집계가 `bool({'CANCELLED'})==True`라 동작 안 함. 실패가 조용히 묻힘. |
| A6 | `panels/quick_tools_panels.py:74-76` | 존재하지 않는 오퍼레이터 ID `"primitive_plane_add"` → Quick Tools > Mesh 패널 깨짐. |
| A7 | `paintsystem/data.py:1616-1646` | `_material_uid_cache`가 Material 객체를 키로 쓰는데 `load_post`/undo에서 무효화 안 됨 → 죽은 RNA 포인터 참조 위험. 무효화 호출도 linked가 아닌 active 머티리얼 대상. |
| A8 | `brush_painter_core.py:235-245` | 회전 캐시 키가 `id(brush)` — GC 후 id 재사용으로 **엉뚱한 마스크 반환** 가능 + 무제한 증가(최대 2.5만 엔트리). |
| A9 | `panels/common.py:543`, `extras_panels.py:245,346-349` | draw() 중 데이터 쓰기(`asset_generate_preview`, `preview_ensure`, preferences 대입) — 무한 리드로우/크래시 위험. |
| A10 | `handlers.py:122` | `save_handler`가 `id()`로 채널 dedup — RNA 래퍼 주소는 불안정. `as_pointer()` 사용. |
| A11 | `__init__.py:24` vs `blender_manifest.toml:6` | 버전 불일치 (2.1.17 vs 2.2.0). |

## B. 성능 — HIGH

### B1. 레이어 UIList 리드로우 경로 = 최대 병목 (O(N²)×머티리얼)
`panels/layers_panels.py:59-138`의 `draw_item`/`filter_items`가 **행마다** 다음을 호출:
- `is_layer_linked` (`data.py:3025`) — 파일 내 전 레이어 Counter 재생성
- `get_layer_warnings` (`data.py:1439`) — parse_context + flatten + 노드 그래프 전체 `find_node`
- `flattened_layers` (`data.py:2321`) — 재귀 flatten+정렬 재실행
- `get_item_by_id`/`get_item_level_from_id` (`nested_list_manager.py:158,191`) — 선형 스캔
- `filter_items`의 `.index()` 선형 탐색 (`layers_panels.py:131`)

→ **단일 리팩터로 해결**: 패널 draw() 1회당 채널 스냅샷(flatten 결과, `{id:(item,level,index)}` 맵, 링크 Counter, 경고 dict)을 한 번 계산해 행에 전달. 구조 변경 시 무효화되는 리비전 카운터 키 캐시.

### B2. `parse_context()` 리드로우당 45회 실행
`panels/*` 전반 + `poll()` 안(`bake_operators.py:707`, `panels/common.py:197`의 전체 머티리얼 스캔 포함). → 프레임 단위 메모이제이션 + 헬퍼 함수들이 `ps_ctx`를 인자로 받도록 시그니처 변경. `poll`에서는 `node_tree.users <= 1` 조기 반환.

### B3. 프로퍼티 변경마다 노드 그래프 전체 재컴파일
`Layer.update_node_tree`(`data.py:832-940`)가 ~25개 프로퍼티의 update 콜백. `projection_position` 드래그 = 마우스 샘플마다 전체 재빌드+재배치. `enabled` 토글도 동일. → 프로퍼티를 **구조 변경 vs 값만**으로 분류, 값만 그룹(projection_*, normalize_normal, enabled, opacity)은 소켓 `default_value` 직접 기록.

### B4. NodeTreeBuilder 내부 O(N²) 4곳
- `nodetree_builder.py:508`, `:1065` — dict를 두고 선형 스캔 (`self.nodes.get(identifier)`로 대체)
- `:1130-1136` — BFS가 노드마다 전체 엣지 스캔 (역인접 dict 사전 생성)
- `:1144` — 레벨 루프 안 서브그래프 선형 탐색
- 추가: `basic_layers.py:104-137` — `create_mixing_graph`가 멱등하지 않아 모디파이어마다 엣지 세트 중복 누적(모디파이어 2개 = 4벌)

### B5. 브러시 페인터(image_filters) Python 루프
- `brush_painter_core.py:1335-1361` — 스탬프 메인 루프 순수 Python + 스탬프마다 `wm.progress_update` UI 왕복 → 팬시 인덱싱 사전 추출 + 마스크 필터 + 콜백 스로틀
- `:411-486` — 시프트 0이어도 매 스탬프 RGB↔HSV 왕복 → 조기 반환
- `:682, 692` — 스탬프마다 `np.tile` 전체 할당 → 브로드캐스트
- `:878-913` — UV 심 탐색이 스탬프마다 전체 선형 스캔 → 그리드 버킷 인덱스

### B6. 라쏘/투사
- `lasso_operators.py:274-299` — 폴리곤 래스터화 행 단위 Python 루프 → 브로드캐스팅/accumulate
- `:183-254` — 커밋마다 BVH 재구축 + `_mesh_uv_batch`와 메시 추출 12줄 중복 → `(obj, 정점수, uv층)` 키 캐시
- `:302-311, 408-412` — 팽창 48 전체 패스 → 분리형 팽창
- `projection_operators.py:392-494` — 적용마다 임시 이미지 3개+카메라 생성/파괴+투사 2회, `layer_img` 예외 누수 → 재사용

### B7. 핸들러
- `handlers.py:35-64` — `frame_change_pre`가 프레임마다 파일 전체 레이어 순회 → 액션 보유 레이어 캐시
- `:145-185` — 페인트 스트로크마다 팔레트 재구축 → 스트로크 종료 시점 스로틀
- `data.py:2569-2584` — `Group.update_node_tree`가 이름 하나 만들려고 전 머티리얼 스캔(`self.id_data`로 대체)
- `data.py:297-303, 1356-1360` — 블렌드 모드 변경마다 파일 전체 스캔 후 채널 재컴파일 → `{uid:[layers]}` 인덱스

## C. 성능 — MEDIUM

- `data.py:448-499` `ensure_sockets` — 전체 재스캔 1회당 변경 1개 적용 (diff 배치 적용으로)
- `panels/common.py:388-406` — draw마다 프리뷰 픽셀 numpy 스캔 (`is_image_painted`) → 이미지별 캐시
- `bake_operators.py:73-79` — 다이얼로그 draw마다 O(재질×오브젝트) 스캔 → invoke에서 1회
- `psd_operators.py:306-326` — PSD 라이브 동기화가 메인 스레드 전체 재디코드 → 변경 레이어만+워커 스레드
- `data.py:2053` + `:517` — 레이어 생성당 `get_udim_tiles` 2회
- `extras_panels.py:79-82` — draw마다 `bpy.data.brushes` 전체 스캔
- `main_panels.py:151` — draw마다 레거시 파서 실행 → load_post에서 1회 검사
- `lasso_operators.py:639, 904, 915` — `modal()` 안 `import math` (마우스 이벤트마다)
- `image_operators.py:195, 225, 383` — 필터마다 이미지 데이터블록 고아 생성
- `quick_edit.py:184, 204` — `save_image(force_save=True)` 중복 디스크 쓰기

## D. 확장성 — HIGH

### D1. 레이어 타입 레지스트리 (최대 투자 효과)
새 레이어 타입 추가에 서로 연결 안 된 **8곳** 수정 필요: `LAYER_TYPE_ENUM`(`data.py:99`), 버전 상수+match 2곳(`basic_layers.py:10,358,632`), `source_node` match(`data.py:969`), `update_node_tree` match(`data.py:887-914`), `uses_coord_type`(`data.py:1436`), UI 분기(`layers_panels.py:200-380`), 아이콘 match(`panels/common.py:531`).
→ `LayerTypeSpec` 데이터클래스(id, label, icon, version, build_graph, uses_coord, draw_properties) + 데코레이터 등록 dict. enum·그래프 빌더·패널 디스패치를 전부 여기서 파생. 채널 타입(`ChannelTypeSpec`)도 동일 패턴 (`data.py:1872, 1966, 2592` 문자열 분기 제거).

### D2. 클래스 등록 자동화
패널 8개+오퍼레이터 17개 파일이 각자 수동 `classes = (...)` 튜플 + 서브모듈 문자열 리스트 4곳(`__init__.py:39`, `panels/__init__.py:4`, `operators/__init__.py:4`, `paintsystem/__init__.py:5`). 실제 등록 누락 사례 존재(`channels_panels.py:258`, `extras_panels.py:502`). → `collect_classes(module)` 자동 수집 + `pkgutil.iter_modules`.

### D3. 데이터 모델 ↔ 그래프 빌더 경계 분리
- `data.py` 3341줄 god-file: enum/색변환/소켓diff/UDIM/UV/모델/베이크/레거시 8관심사 혼재 → `enums.py`, `layer.py`, `channel.py`, `bake.py`, `legacy.py` 분리
- `Channel.update_node_tree`(`data.py:1822-2004`) — 180줄 합성 알고리즘이 PropertyGroup 메서드 안 → `graph/channel_graph.py`의 `build_channel_graph(channel)`로 이동 (기존 `create_layer_graph` 경계 모방)
- `create_coord_graph` 2벌 존재 — `graph/common.py:146`은 낡은 버전(PARALLAX·종횡비 보정 없음)인데 여전히 export됨 → 삭제

### D4. 노드 식별자를 `node.label`에 저장
`nodetree_builder.py:520, 910-925` — 사용자가 라벨 편집하면 노드 고아화→삭제. `node.get("identifier")` 읽는 경로(`:680, 826, 835`)는 아무도 기록하지 않아 죽어 있고, `:826`의 `None.startswith`는 서브그래프 레이어 타입 추가 시 즉시 크래시. → `node["ps_id"]` 커스텀 프로퍼티로 이전.

### D5. 오퍼레이터 중복 제거
- 레이어 생성 8종+마스크 4종이 타입 문자열만 다름(`layers_operators.py:157-393, 1134-1185`) → `layer_type` 클래스 속성 베이스+팩토리 (~450줄→80줄)
- MergeDown/Up(`bake_operators.py:692-931`), MoveUp/Down(`layers_operators.py:671-830`) 복붙 쌍 → `direction` 속성 베이스
- 모달 4종 draw 핸들러 생명주기 복붙 → `ModalDrawMixin` (+A4의 `cancel()` 포함)

### D6. 이미지↔numpy 변환 단일화
읽기 5곳/쓰기 7곳이 4개 모듈에 중복, `update_tag()` 적용 제각각(과거 버그 재발 경로). `merge_operators.py:15`는 타 모듈 private 함수 임포트. 바이리니어 리사이즈 3중, 가우시안 블러 글자 단위 복제(`brush_painter_core.py:101` ≡ `basic_filters.py:6`). → `paintsystem/image.py`에 `read_rgba`/`write_rgba`, `utils/imaging.py`에 공용 필터.

## E. 확장성 — MEDIUM/LOW

- 문자열 리터럴 산재: 레이어/좌표/블렌드 타입, 노드 소켓 이름(`'Color Alpha'` 등 KeyError 경로) → 명명 상수 모듈
- UI의 노드 내부 직접 접근(Law of Demeter): `layers_panels.py:242-261, 350-352, 406`, `channels_panels.py:176` → `Layer`에 명명 소켓 프로퍼티 추가(기존 `mix_node` 패턴 확장)
- 레이어 타입→아이콘 매핑 UI 하드코딩 이중 관리 + 이미 드리프트(`common.py:555` 'SHADER' 죽은 분기) → enum에 아이콘 컬럼
- `preferences.py:8` vs `panels/preferences_panels.py:9` 동명 클래스 충돌, `get_preferences`의 광범위 except
- 폐기 코드: `GlobalLayer` 150줄, 레거시 파서 250줄, 예제 클래스 170줄(`nodetree_builder.py:1197`, 실제 UI 노출됨), `IMAGE_FILTERS_AVAILABLE` 상수, `bake_channels` 죽은 프로퍼티, `timing_decorator`(NameError 잠재)
- `keymaps.py:238` 전체 감싼 `except: pass` — 부분 실패 시 단축키 조용히 누락
- Scene 프로퍼티 등록 위치 분산(`projection_operators.py:535` vs `data.py:3325`)
- `nested_list_manager.py` 전반 선형 스캔 → 지연 `{id:index}` 맵
- `apply_properties`(`data.py:1574`) 재시도 루프 → 명시적 복사 순서

---

## 권장 착수 순서

1. **A그룹 실버그** (특히 A1~A5: 한 줄~수십 줄 수정, 데이터 손상 경로 차단)
2. **B1+B2** — 리드로우 캐싱 단일 리팩터 (체감 성능 최대)
3. **B3** — 값 프로퍼티의 소켓 직접 기록 (드래그 반응성)
4. **B4** — 빌더 선형 스캔 dict화 (기계적, 무위험)
5. **D2** — 클래스 등록 자동화 (확장성 투자 대비 효과 최대, 등록 누락 버그도 해소)
6. **D1** — 레이어 타입 레지스트리 (이후 모든 기능 추가 비용 절감)
7. **D5+D6** — 오퍼레이터/이미지IO 중복 제거
8. **B5~B7, D3, D4** — 규모 큰 항목, 개별 계획 후 진행
