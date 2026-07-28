# docs/ 인덱스 — 누가 무엇을 읽어야 하는가

> 상태: ✅ 확정 · 🟠 논의중(합의 후 갱신) · 🕓 Phase 2 (구현 금지, 스키마 선반영만)
> 미확정 안건은 `99_open_items.md`에서 추적 — 합의되면 **해당 문서 + 99번을 같이 갱신**하고 버전을 올린다.

## 폴더 구분

- **`docs/` 루트 + `policies/`** — **전원 대상.** 두 사람(+코딩 에이전트)이 모두 따라야 하는 계약·규율. 여기 문서를 바꾸려면 소유권 규칙(08)에 따른 승인 필요.
- **`docs/part_a/`** — **member-A(박진희) 소유.** 탐지·소통·Import의 상세 설계와 파라미터. B는 승인 대상이 아니고 훑어만 봐도 됨 — 단 경계에 걸린 부분(taxonomy 소비, FakeProvider 시나리오 명세)은 해당 문서에 [A+B]로 표시돼 있음.
- **`docs/part_b/`** — **member-B(염준영) 소유.** 진단·출제의 상세 설계와 파라미터(11종 — 아래 절). A는 승인 대상이 아니고 훑어만 봐도 됨 — 단 공용 계약·ERD 편입 등 경계 안건은 `09_integration_proposals.md`가 A 승인 요청을 모아 둔다.

## 전원 필독 (읽는 순서대로)

| 문서 | 내용 | 상태 |
| --- | --- | --- |
| `../README.md` → `../CLAUDE.md` | 프로젝트·에이전트 지침(불변식 8) — 루트에 있다(`../AGENTS.md`는 같은 파일을 가리키는 심링크) | ✅ |
| `01_overview.md` | 제품·AI 역할·용어 사전 | ✅ |
| `02_ownership.md` | 폴더 소유권 — **양자 승인 12파일** | ✅ |
| `03_coding_rules.md` | 코드 규칙(하드코딩·라이브러리 우선·모듈화·PR) — **리뷰 반려 기준** | ✅ |
| `04_api_contract.md` | REST API·데이터 계약 — §2 공통 규약은 B 엔드포인트도 준수 | 🟢 7/15 리뷰 완료 — v1.0 승격 대기 |
| `05_request_json.md` | 백엔드→AI 요청 JSON 9종 | 🟢 04와 동기 |
| `06_erd.md` | AI PG 26테이블 — 공통 실행 메타·에이전트 원장 포함 | ✅ |
| `07_standard_schema.md` | **표준 스키마 정의서** — 템플릿=Import 목적지=스냅숏 3자 일치 (BE-7) | 🟠 백엔드 리뷰 대기 |
| `08_kafka_events.md` | **Kafka 토픽·이벤트 스키마** — 완료 통지·월별 벌크 (Open-2·BE-5) | 🟠 백엔드 공동 확정 대기 |
| `99_open_items.md` | 미확정 안건 추적표 (Open·B·BE 전체) | 진행 중 |

## 공용 정책 (`policies/` — 전원 준수)

| 문서 | 내용 | 상태 |
| --- | --- | --- |
| `taxonomy.md` | 수능 영역·문항 형식 어휘집 — [A+B] 공용 어휘 | ✅ 7/27 B-3 경계 사례 7건까지 확정 |
| `masking_redaction.md` | 마스킹 P1~P8 — LLM 경로 전체에 적용(B의 출제 프롬프트 포함) | ✅ |
| `error_codes.md` | 에러·'정상 상태' 코드 사전 — 전 엔드포인트 공통 | ✅ |
| `quota_metering.md` | 쿼터·미터링 — (7/15) 전부 백엔드 소유로 이관, AI는 LLM 원가 관측만 | ✅ 백엔드 참고용 |
| `langgraph_state.md` | 에이전트 state — 워커 3종 + **§5 결정론 슈퍼바이저 실행 계약** | ✅ 7/27 A+B 확정 |
| `f17_paper_exam.md` | 지면 시험 메모 — 서술형 폐기(7/15)로 OCR 소유만 P2 시점 재론 | 🕓 P2 |

## A 파트 상세 (`part_a/` — B는 참고만)

| 문서 | 내용 | 상태 |
| --- | --- | --- |
| `01_pipeline.md` | A 파이프라인 v2 — 에이전트 2 + 결정론 1 + 보조 4 | ✅ |
| `02_design.md` | 설계 v2.1 — 시퀀스·ERD 경계 | ✅ |
| `03_usecases.md` | 유스케이스 v3 — 시나리오 17종 | ✅ |
| `04_threshold_config.md` | R1~R6 기본값·섀도 모드 | ✅ |
| `05_tone_mapping.md` | 라벨 24조합 + buffer_lexicon 50항 | ✅ |
| `06_refine_policy.md` | 핑퐁 다듬기 정책 | ✅ §5도 확정(집행=백엔드) |
| `07_report_spec.md` | 리포트 사양 — 노출 3층 · 벌크=Kafka 일괄 | 🟠 Open-9 출처만 잔여 |
| `08_evaluation_plan.md` | 평가 계획 — golden/ 구성 (tagging 정답 라벨만 [A+B]) | ✅ |
| `09_detect_spec.md` | 감지 API 명세 — alert_context·lifecycle·display_label·excluded_under_2w (`/detect` 요청/응답 원본) | ✅ AI 확정 — 백엔드 전달본 |

## B 파트 상세 (`part_b/` — A는 참고만)

| 문서 | 내용 | 상태 |
| --- | --- | --- |
| `01_pipeline.md` | B 파이프라인 v1.2 — 결정론 진단 1 + 출제 워크플로 1 + 플랫폼 `llm/` | ✅ |
| `02_design.md` | 설계 v1 — 시퀀스 · B 증분 ERD · 데이터 경계 | ✅ |
| `03_usecases.md` | 유스케이스 v1 — 시나리오 14종 | ✅ |
| `04_curriculum_graph.md` | curriculum_graph 사양 v1 — 그래프 3종 · 약점 판정 결정론 규격 | ✅ |
| `05_problem_generation.md` | 문항 생성 규격 v1 — 요청·지문·문항 계약 · 출처·저작권 | ✅ |
| `06_quality_gates.md` | 3단계 품질 게이트 v1 — RuleValidation · BlindCrossSolve · ReleaseDecision | ✅ |
| `07_refine_policy.md` | 문항 핑퐁 수정 정책 v1 — Step 3 협업형 편집(MVP) | ✅ |
| `08_evaluation_plan.md` | 평가 계획 B 증보 v1 — `golden/problems/` · `golden/diagnosis/` 구성 | ✅ |
| `09_integration_proposals.md` | **B 통합 제안서 v1** — A 변경 요청 · 공용 계약 제안 · OPEN 총괄(§1-9 A 작업 지시) | 🟠 진행 중 — `[제안]`은 오너 승인 전까지 미확정 |
| `10_m2_problem_generation_architecture.md` | M2 문제생성 아키텍처 결정 기록 (B-M2-01~05) | 🟠 후속 결정 수집 중 |
| `11_graphrag_knowledge_layer.md` | GraphRAG 지식 계층 v1 — ContextPack · EvidencePack · GraphContextService | ✅ |

## 저장소 밖 (노션/드라이브)

기획서 ver4 · 기술명세서 v2.3 · 와이어프레임 · 백엔드 DDD 설계 · 팀공유본 HTML — 회의용 문서는 노션에서. 이 docs는 구현에 필요한 것만.
