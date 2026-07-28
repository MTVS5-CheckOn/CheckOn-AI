# [체크온] `ai/` 폴더 소유권 분장 v4 — R&R v1 + 에이전트·HTTP·DB 계층 반영

> **v1 → v2 변경:** ① 에이전트 2종(counsel_pack·mapping_probe)과 보조 4종(ⓐⓑⓒⓓ)의 파일 소유 명시 ② 데이터 파일 신설분(`tone_map.yaml` · `redaction_patterns.yaml`) 추가 ③ **수능 태그 enum을 공용 어휘로 승격** — `contracts/taxonomy.py` 신설, 양자 승인 대상 6곳 → **7곳** ④ `evaluation/golden/` 하위를 평가 계획서 v0 구성으로 세분 ⑤ F17(Phase 2) 분담 예고 각주.
>
> **v2 → v3 변경(7/22, B 확인 완료):** `api/` HTTP 노출 계층 신설 승인 — `app.py`·`envelope.py`는 공통 계약, capability 라우터는 해당 오너 소유. 양자 승인 대상 7곳 → **9곳**.
>
> **v3 → v4 변경:** `db/models.py`·`db/base.py`를 공통 계약으로 편입하고, B 소유 문제생성 워커가 합류한 슈퍼바이저 경계를 확정했다. 슈퍼바이저 구현은 A 단독 소유, 공통 Job 상태·operation·결정론 라우팅 계약(`contracts/agents.py`)은 양자 승인, 각 워커 그래프는 해당 capability 오너가 소유한다. 양자 승인 대상은 **12곳**이다.
>
> **원칙(불변)** — 폴더 구조는 AI 아키텍처 지시서 그대로 유지한다(사람 기준 재편 금지). 모든 폴더·파일에 단독 오너를 지정한다. "공동 소유"는 소유가 아니므로, 공동 영역은 최소화하고 변경 절차(양자 승인)로만 남긴다.
>
> **오너의 의미** — 그 코드의 설계 결정권 + PR 최종 승인 책임. 상대방도 코드는 자유롭게 읽고 PR을 보낼 수 있다 — 머지 승인만 오너가 한다.
>
> **참조** — 체크온_AI파트_RnR_v1.md · AI 아키텍처 지시서(폴더 구조 원본) · 파이프라인 v2(에이전트·보조 설계) · 수능태그 어휘집 v0(taxonomy 내용)

---

## 1. Capability 소유 — 겹침 없음

| 패키지 | 오너 | 근거 |
| --- | --- | --- |
| `detection/` | 박진희 | R&R: 탐지 담당. 규칙 R1~R6·베이스라인·랭킹·캘리브레이션. **보조 ⓐ(브리핑 문장화)는 소비 측 기능으로 composition에 두되 오너 동일** |
| `composition/` | **박진희** | R&R: 소통 초안 담당. 라벨→톤 매핑·게이트 구현·완충 사전 + **(v2) 핑퐁 다듬기(refine)·리포트 chart_analysis·에이전트 ①(counsel_pack)·보조 ⓐⓑⓓ** |
| `diagnosis/` | 염준영 | R&R: 약점 진단 체계는 B 소유. `curriculum_graph.yaml` 포함 — **(v2) 그래프의 영역 어휘는 `contracts/taxonomy.py`를 따른다** |
| `problem_generation/` | 염준영 | R&R: 출제 파이프라인 전체. **(v2) F17(Phase 2) 시험지 PDF 조판도 여기 — 문항 메타(area·type·item_format) 보존 책임** |
| `import_mapping/` | **박진희** | 결정론 변환 + 데이터 품질 게이트 재사용 — A의 데이터 파이프라인 축. **(v2) 에이전트 ②(mapping_probe)·보조 ⓒ(태깅 제안) 포함.** 착수 시기는 파일럿 안정화 이후(단, 태깅 제안ⓒ는 F5 채점 UX에 걸려 있어 선행 가능) |

## 2. 플랫폼 모듈 소유 — 최다 이해관계자 원칙

| 패키지 | 오너 | 근거 |
| --- | --- | --- |
| `evidence/` | **박진희** | 근거 체계의 원천이 감지(경보 evidence). 경계 규약 ②의 관리자 |
| `gates/` (chain 실행기) | **박진희** | 최다 사용자가 composition 게이트 체인. **(v2) refine의 매 턴 재통과·사전 정적 검사도 이 실행기 위에서** |
| `llm/` (gateway·structured·providers) | 염준영 | 구조화 출력·교차 풀이의 최대 이해관계자가 출제. FakeProvider 포함. **(v2) usage_daily 미터링 훅은 gateway에 — 스키마는 A와 양자(§4)** |
| `llm/prompts/templates/composition/` | **박진희** | 템플릿은 사용하는 capability 오너를 따라감 — **(v2) refine·chart_analysis·brief·classify·label_suggest 템플릿 포함** |
| `llm/prompts/templates/problem_generation/` | 염준영 | 〃 |
| `llm/prompts/templates/import_mapping/` | **박진희** | 〃 — **(v2) probe(도구 선택)·tag_suggest 템플릿 포함** |
| `llm/prompts/loader.py`·`registry.yaml` | 염준영 | llm/ 소유에 귀속. 단 registry.yaml에 상대 프롬프트 등록 행 변경 시 해당 오너 리뷰 |
| `registry/` | **박진희** | 감지 엔진(규칙→GRU) 교체 지점의 메타. A의 Phase 3 준비물 |
| `runtime/` (metrics·redaction·errors) | 박진희 | redaction은 A의 개인정보 방어선. **(v2) `redaction_patterns.yaml`(마스킹 정의서 P1~P8)·`errors.py`(에러·상태 코드 사전 §4 구현) 포함.** 비용 지표 스키마 변경은 B 승인(§4) |
| `agents/` (슈퍼바이저·스케줄러·공통 유틸) | **박진희** | 슈퍼바이저 구현(`supervisor.py` 스케줄링 facade·`job_store.py`)과 체크포인터·기록기는 A 단독 소유. 워커 그래프는 capability 오너(A: counsel_pack·mapping_probe, B: problem_generation)가 소유하며, 공통 Job 상태·operation·라우팅 계약은 `contracts/agents.py`에서 양자 승인 |
| **(신설) `.github/workflows/`** (CI) | **박진희** | 전 코드 ruff·mypy·pytest(fake) 게이트를 A가 신설·주도. `ai/` 폴더 밖이라 소유 명시가 없어 여기서 닫는다(단독 오너·공동 최소화 원칙). **B capability 전용 잡 추가 시 B 리뷰.** 실 PG 통합 잡은 signal-brief 머지 후(99 ⑫) |

## 3. `contracts/` — 파일 단위 소유

| 파일 | 오너 | 비고 |
| --- | --- | --- |
| `detection.py` · `composition.py` | **박진희** | composition.py에 **(v2) RefineRequest·DraftRevision·ChartAnalysisBlock 타입 추가분 포함** |
| `diagnosis.py` · `problem_generation.py` | 염준영 | |
| `import_mapping.py` | **박진희** | (v2) ProbeStep·도구 시그니처 3종 포함 |
| `agents.py` | **공통 계약** | `WorkerKind·OperationKind·JobPhase·PriorityClass·WorkerJob`과 결정론 라우팅 테이블 — 변경 시 A·B 양자 승인 |
| **(v2 신설) `taxonomy.py`** | **공통 계약** | **수능 6영역 area enum + subject_track + type_tag + item_format** — 감지 R6·약점 지도·태깅ⓒ·출제가 전부 이 어휘를 씀(어휘집 v0이 사양 원본). Open-11 합의로 확정 |
| `execution.py` · `llm.py` · `gates.py` · `evaluation.py` | **공통 계약** | 단독 오너 없음 — 변경 시 A·B 양자 승인 필수. 신규 필드 추가도 예외 없음 |

## 4. 공동 영역의 변경 절차 (겹침을 규칙으로 관리)

1. **양자 승인 대상 — 12곳:** `contracts/execution.py·llm.py·gates.py·evaluation.py·taxonomy.py·agents.py`, `evidence/models.py`(EvidenceRef 스키마), `runtime/metrics.py`의 이벤트 스키마, `api/app.py·api/envelope.py`, `db/models.py·db/base.py`(ERD 스키마 — B의 문제·진단·워커 실행도 포함하므로 공용). 이 12곳만 두 명 승인, 나머지는 전부 단독 오너.
   - **마이그레이션 파일은 양자 목록에 넣지 않는다** — `db/models.py`의 기계적 산출물이므로, 모델 diff가 포함된 PR에서 함께 리뷰되면 충분하다. 모델 무변경 마이그레이션(인덱스 조정 등)은 해당 테이블 오너 단독. 저장소(`db/repositories/`) 구현은 capability별 오너(detection 적재 = A).
2. **경계를 넘는 입력:** B가 감지 산출을 더 원하면 A의 `contracts/detection.py`에 PR → A 승인. 반대 방향도 동일. **상대 capability 내부 파일 직접 수정은 금지**(지시서 2.2).
3. **골든셋·평가:** `evaluation/detection_eval.py`·`draft_eval.py`·`import_eval.py` = A, `problem_eval.py` = B. golden/ 하위는 §5 트리의 코퍼스별 소유 — **(v2) `golden/tagging/`은 정답 라벨 확정이 [A+B]**(어휘집 §2 판정 기준 합의 후 각자 라벨링, 불일치가 경계 사례집 증보분). 엔진·프롬프트 버전업 시 골든셋 diff는 상호 리뷰(오너 아닌 쪽이 리뷰어).
4. **tests/ai/:** 프로덕션 대칭 — 소유도 대응 파일을 따름. `tests/ai/fakes/`(FakeProvider 시나리오)는 llm/ 소유자인 B — **(v2) 단 refine 게이트 공격·에이전트 장애 시나리오는 A가 시나리오 명세를 제공**(B는 Fake 구현만).
5. **(v2 신설) 데이터 파일 규칙:** `tone_map.yaml`·`buffer_lexicon.yaml`·`redaction_patterns.yaml`은 코드와 동일하게 PR 리뷰 대상(오너 단독) — 단 **golden 코퍼스 통과가 머지 조건**(사전 갱신도 테스트를 거친다).

## 5. 소유권 주석 트리 (v4 — 복붙용)

```mathematica
ai/
│
│ ═══════════════ 플랫폼 (공통 규율) ═══════════════
│
├── contracts/                          # 파일 단위 소유 — 폴더 오너 없음
│   ├── execution.py                    [박진희+염준영]  ExecutionContext · RunMetadata
│   ├── detection.py                    [박진희]
│   ├── diagnosis.py                    [염준영]
│   ├── composition.py                  [박진희]         ← v2: Refine·Revision·ChartAnalysis 타입
│   ├── problem_generation.py           [염준영]
│   ├── agents.py                       [박진희+염준영]  ★슈퍼바이저 공통 Job·operation·라우팅 계약
│   ├── import_mapping.py               [박진희]         ← v2: Probe 도구 시그니처
│   ├── taxonomy.py                     [박진희+염준영]  ★v2 신설 — 수능 area·type·item_format enum (Open-11)
│   ├── llm.py                          [박진희+염준영]  LLMProvider · ModelRole(+classifier) · 공통 예외
│   ├── gates.py                        [박진희+염준영]  Gate · GateResult
│   └── evaluation.py                   [박진희+염준영]  Evaluator · 지표 타입
│
├── evidence/                           [박진희]    근거 추적 규율
│   ├── models.py                       [박진희+염준영]  ← EvidenceRef 스키마만 양자
│   ├── collector.py · validator.py · resolver.py   [박진희]
│
├── gates/                              [박진희]    게이트 체인 실행기 (+refine 정적 검사)
│   └── chain.py                        [박진희]
│
├── agents/                             [박진희]    결정론 슈퍼바이저·영속 스케줄러·공통 유틸
│   ├── supervisor.py                   [박진희]    operation 라우팅 · lease · terminal 수렴
│   ├── job_store.py                     [박진희]    우선순위·aging · 영속 Job 저장 경계
│   ├── checkpointer.py                 [박진희]    PostgresSaver 연결 · 내부 스키마 초기화
│   └── recorder.py                     [박진희]    AGENT_RUN · AGENT_STEP 기록
│
├── llm/                                [염준영]    provider 독립성의 경계
│   ├── gateway.py                      [염준영]    라우팅 · retry · 비용 집계 (+usage_daily 훅)
│   ├── structured.py                   [염준영]
│   ├── providers/                      [염준영]    어댑터 · FakeProvider
│   └── prompts/
│       ├── loader.py · registry.yaml   [염준영]    ← A 프롬프트 등록 행 변경 시 A 리뷰
│       └── templates/
│           ├── composition/            [박진희]    reply · report(+chart_analysis) · counsel_pack · refine · brief(ⓐ) · classify(ⓑ) · label_suggest(ⓓ)
│           ├── problem_generation/     [염준영]    passage · items · cross_solve
│           └── import_mapping/         [박진희]    infer_mapping · probe(②) · tag_suggest(ⓒ)
│
├── registry/                           [박진희]    엔진 레지스트리 (규칙→GRU 교체 지점)
│
├── runtime/                            [박진희]    관측성 · 마스킹 · 오류 분류
│   ├── metrics.py                      [박진희+염준영]  ← 이벤트 스키마만 양자
│   ├── redaction.py                    [박진희]
│   ├── redaction_patterns.yaml         [박진희]    ★v2 — 마스킹 정의서 P1~P8 (골든 코퍼스 통과가 머지 조건)
│   └── errors.py                       [박진희]    에러·상태 코드 사전 §4 구현
│
├── api/                                ★v3 — HTTP 노출 계층(B 확인 완료)
│   ├── app.py                          [공통 계약]  FastAPI 앱 팩토리 · 예외 핸들러 — 여러 라우터 공용이라 양자
│   ├── envelope.py                     [공통 계약]  공통 응답 조립(data·error·meta envelope) — 〃
│   └── routers/                                    ← 라우터는 해당 capability 오너를 따름
│       └── detect.py                   [박진희]    감지 라우터 (POST /v1/detect)
│
├── db/                                 ★v4 — 저장 계층(D-②, A+B 공통 계약)
│   ├── base.py                         [공통 계약]  DeclarativeBase · naming convention — 양자
│   ├── models.py                       [공통 계약]  ERD 26테이블 ORM(B 문제·진단 포함) — 양자
│   ├── settings.py                     [박진희]    DATABASE_URL (pydantic-settings)
│   ├── session.py                      [박진희]    async 엔진·세션 팩토리
│   ├── store_factory.py                [박진희]    저장소 backend 조립
│   ├── migrations/                                 ← 모델 종속 산출물(양자 아님)
│   │   ├── env.py · script.py.mako     [박진희]    alembic 환경
│   │   └── versions/                   [테이블 오너]  마이그레이션(모델 diff PR에서 함께 리뷰)
│   └── repositories/                               ← 적재 구현은 capability 오너
│       ├── idempotency.py              [박진희]    멱등 영속 저장소(공용 유틸성 — A 소유)
│       ├── detection_store.py          [박진희]    감지 적재(AI_RUN·SIGNAL·FEATURE_WEEK)
│       └── agent_job.py                [박진희]    AGENT_RUN 영속 Job·lease·fencing
│
│ ═══════════════ Capability (기능) ═══════════════
│
├── detection/                          [박진희]    위험신호 감지 — 결정론 · LLM 금지
│   ├── features.py · baseline.py · rules.py · thresholds.py · ranking.py
│
├── diagnosis/                          [염준영]    약점 진단 — 그래프 탐색 · LLM 금지
│   ├── skill_graph.py · diagnoser.py
│   └── data/curriculum_graph.yaml      [염준영]    ← 영역 어휘는 contracts/taxonomy.py 준수
│
├── composition/                        [박진희]    라벨 기반 초안 + 핑퐁 + 리포트 + 에이전트①
│   ├── context_builder.py              [박진희]    ← audience 필터(teacher_only 구조적 제외) 여기
│   ├── tone_mapping.py                 [박진희]
│   ├── tone_map.yaml                   [박진희]    ★v2 — 24조합 매핑표 (매핑표 v0이 사양 원본)
│   ├── buffer_lexicon.yaml             [박진희]
│   ├── composer.py · gates_impl.py · pipeline.py   [박진희]
│   ├── refine.py                       [박진희]    ★v2 — 핑퐁(정적 검사·리비전·롤백)
│   ├── report_blocks.py                [박진희]    ★v2 — chart_analysis · numbers_used 대조
│   ├── briefing.py                     [박진희]    ★v2 — 보조 ⓐ 문장화 + 왜곡 게이트 (detection 무변경)
│   ├── classify.py                     [박진희]    ★v2 — 보조 ⓑ 문의 분류
│   ├── label_suggest.py                [박진희]    ★v2 — 보조 ⓓ 라벨 제안 + 인용 실존 게이트
│   └── workflow_counsel_pack.py        [박진희]    ★v2 — 에이전트 ① LangGraph (B-1 합의 후 착수)
│
├── problem_generation/                 [염준영]    문항 생성 — LangGraph
│   ├── passage.py · generator.py · verification.py
│   ├── cross_solver.py · workflow.py
│   └── (P2 예약) print_layout.py       [염준영]    F17 시험지 조판 — 문항 메타 보존
│
├── import_mapping/                     [박진희]    엑셀 Import + 에이전트② + 보조ⓒ
│   ├── profiler.py · masking.py · inferencer.py
│   ├── transformer.py · gates_impl.py · spec_store.py
│   ├── probe_agent.py                  [박진희]    ★v2 — 에이전트 ② ReAct (B-1 합의 후 착수)
│   ├── probe_tools.py                  [박진희]    ★v2 — get_unique_values 등 3종 (마스킹 내장)
│   └── tag_suggest.py                  [박진희]    ★v2 — 보조 ⓒ (enum은 taxonomy 준수 · 캐시)
│
│ ═══════════════ 평가 (프로덕션 격리) ═══════════════
│
└── evaluation/
    ├── fake_snapshot.py                [박진희]    감지 평가 픽스처 — 프로덕션 capability import 금지
    ├── detection_eval.py · draft_eval.py · import_eval.py   [박진희]
    ├── problem_eval.py                 [염준영]
    └── golden/                         # 평가 계획서 v0 §1 구성
        ├── detection/  (G1~G12)        [박진희]
        ├── tone/       (24조합 스냅숏)  [박진희]
        ├── tagging/                    [박진희+염준영]  ★정답 라벨 확정만 양자 (어휘집 §2 기준)
        ├── refine_attack/ (A1~A8)      [박진희]    ★v2
        ├── redaction/  (코퍼스 30건)    [박진희]
        ├── import_corpus/ (양식 10종)   [박진희]
        ├── classify/                   [박진희]
        ├── problems/   (문항 검증)      [염준영]
        └── diagnosis/  (진단 회귀)      [염준영]

tests/ai/                               # 대응 프로덕션 파일의 오너를 그대로 따름
├── unit/ · contract/ · integration/ · gates/ · golden/ · failure/
└── fakes/                              [염준영]    FakeProvider (refine·에이전트 장애 시나리오 명세는 A 제공)
```

> **[PART_B 크로스체킹 요청 · 미확정 — 평가 격리]** FakeSnapshot 위치와 소유권은 승인했지만 현재 프로덕션 capability의 `ai.evaluation` 역방향 import 금지는 문서 규칙으로만 보장된다. **제안 해결안:** import 경계 회귀 테스트에 `detection/`·`composition/`·`diagnosis/`·`problem_generation/`·`import_mapping/`이 `ai.evaluation`을 import하지 않는 조건을 추가한다. A가 기존 격리 테스트 범위에 포함할지 확인해 달라.
>
> ✅ **A 판정(7/22):** 수용 — 문서 규칙을 AST 회귀 테스트로 승격했다(`tests/ai/contract/test_evaluation_isolation.py`). 5개 프로덕션 capability가 `ai.evaluation`을 import하면 CI가 실패한다. 아직 파일이 없는 패키지(composition·import_mapping)는 파일 생성 시 자동으로 검사 대상에 편입된다.

## 6. 부하 요약 (v4)

| | **박진희** | 염준영 |
| --- | --- | --- |
| capability | detection · composition(+핑퐁·리포트·ⓐⓑⓓ·에이전트①) · import_mapping(+에이전트②·ⓒ) | diagnosis · problem_generation(+P2 조판) |
| 플랫폼 | evidence · gates · agents · registry · runtime | llm 전체(prompts 코어·FakeProvider·미터링 훅) |
| 성격 | 폭이 넓음(결정론+생성+에이전트 2종) — 각 항목은 상대적으로 가벼우나 **v2에서 항목 수 증가** — 착수 순서가 중요(체크리스트 v2 §5) | 깊음(최고 난도 체인) — 한 체인에 집중. F17 조판은 P2라 당장 부담 없음 |
| 시기 | Phase 0~1 초반 크리티컬(감지→초안→핑퐁). 에이전트 2종은 B-1 합의 후 | 파일럿 중반부터 크리티컬(검증 게이트). 초기엔 diagnosis 그래프·taxonomy 합의부터 |

> **한 줄 요지** — 폴더는 capability 기준 그대로, 소유는 주석 트리로. 슈퍼바이저 구현은 A, 각 워커 그래프는 capability 오너, 공통 Job·라우팅 계약은 양자 승인이다. 겹치는 곳은 공동 소유가 아니라 **양자 승인이 필요한 12개 파일**로 좁혀 관리한다.
