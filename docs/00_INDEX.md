# docs/ 인덱스 — 누가 무엇을 읽어야 하는가

> 상태: ✅ 확정 · 🟠 논의중(합의 후 갱신) · 🕓 Phase 2 (구현 금지, 스키마 선반영만)
> 미확정 안건은 `99_open_items.md`에서 추적 — 합의되면 **해당 문서 + 99번을 같이 갱신**하고 버전을 올린다.

## 폴더 구분

- **`docs/` 루트 + `policies/`** — **전원 대상.** 두 사람(+코딩 에이전트)이 모두 따라야 하는 계약·규율. 여기 문서를 바꾸려면 소유권 규칙(08)에 따른 승인 필요.
- **`docs/part_a/`** — **member-A(박진희) 소유.** 탐지·소통·Import의 상세 설계와 파라미터. B는 승인 대상이 아니고 훑어만 봐도 됨 — 단 경계에 걸린 부분(taxonomy 소비, FakeProvider 시나리오 명세)은 해당 문서에 [A+B]로 표시돼 있음. B 파트(진단·출제) 상세 설계는 `docs/part_b/`로 추가 예정.

## 전원 필독 (읽는 순서대로)

| 문서 | 내용 | 상태 |
| --- | --- | --- |
| `../README.md` → `../CLAUDE.md` | 프로젝트·에이전트 지침(불변식 8) | ✅ |
| `01_overview.md` | 제품·AI 역할·용어 사전 | ✅ |
| `02_ownership.md` | 폴더 소유권 — **양자 승인 7파일** | ✅ |
| `03_coding_rules.md` | 코드 규칙(하드코딩·라이브러리 우선·모듈화·PR) — **리뷰 반려 기준** | ✅ |
| `04_api_contract.md` | REST API·데이터 계약 v0.1 — §2 공통 규약은 B 엔드포인트도 준수 | 🟠 Open-1~12 |
| `05_request_json.md` | 백엔드→AI 요청 JSON 9종 | 🟠 04와 동기 |
| `06_erd.md` | AI PG 24테이블 (B의 출제 테이블은 증분 추가 예정) | ✅ |
| `99_open_items.md` | 미확정 안건 추적표 (Open·B·BE 전체) | 진행 중 |

## 공용 정책 (`policies/` — 전원 준수)

| 문서 | 내용 | 상태 |
| --- | --- | --- |
| `taxonomy.md` | 수능 영역·문항 형식 어휘집 — **[A+B] 공용 어휘, B-3 합의 자료** | 🟠 Open-11 |
| `masking_redaction.md` | 마스킹 P1~P8 — LLM 경로 전체에 적용(B의 출제 프롬프트 포함) | ✅ |
| `error_codes.md` | 에러·'정상 상태' 코드 사전 — 전 엔드포인트 공통 | ✅ |
| `quota_metering.md` | 쿼터·미터링 — problem(출제) 할당도 여기 | 🟠 BE-4 |
| `langgraph_state.md` | 에이전트 state 스키마 — **B-1 합의 대상 초안** | 🟠 B-1 |
| `f17_paper_exam.md` | 지면 시험 메모 — 조판(B)·OCR 분담은 B-4에서 | 🕓 P2 |

## A 파트 상세 (`part_a/` — B는 참고만)

| 문서 | 내용 | 상태 |
| --- | --- | --- |
| `01_pipeline.md` | A 파이프라인 v2 — 에이전트 2 + 결정론 1 + 보조 4 | ✅ |
| `02_design.md` | 설계 v2.1 — 시퀀스·ERD 경계 | ✅ |
| `03_usecases.md` | 유스케이스 v3 — 시나리오 17종 | ✅ |
| `04_threshold_config.md` | R1~R6 기본값·섀도 모드 | ✅ |
| `05_tone_mapping.md` | 라벨 24조합 + buffer_lexicon 50항 | ✅ |
| `06_refine_policy.md` | 핑퐁 다듬기 정책 | 🟠 §5만 BE-4 |
| `07_report_spec.md` | 리포트 사양 — 노출 3층 | 🟠 Open-9·10 |
| `08_evaluation_plan.md` | 평가 계획 — golden/ 구성 (tagging 정답 라벨만 [A+B]) | ✅ |

## 저장소 밖 (노션/드라이브)

기획서 ver4 · 기술명세서 v2.3 · 와이어프레임 · 백엔드 DDD 설계 · 팀공유본 HTML — 회의용 문서는 노션에서. 이 docs는 구현에 필요한 것만.
