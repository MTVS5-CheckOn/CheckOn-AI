# CLAUDE.md — 체크온 AI 서비스 · 코딩 에이전트 지침

> 이 파일은 Claude Code / Codex 등 코딩 에이전트가 이 저장소에서 작업할 때의 규칙이다. `AGENTS.md`는 동일 내용이다. 사람 리뷰어도 같은 기준으로 PR을 본다.

## 0. 이 저장소가 하는 일 (3줄)

**수능 대비 고등 국어 학원 강사**용 SaaS의 AI 서비스(중등·내신은 고도화 — v1 코드에 중등 분기 만들지 말 것). 백엔드(Java/MySQL — 도메인 원본)가 alias 처리된 학습 스냅숏을 REST로 보내면, 위험신호(결정론)·상담 초안(LLM+게이트)·엑셀 매핑·태깅·문제 생성/검증 결과를 돌려준다. 승인·발송·학생 노출은 전부 백엔드(HITL) — 이 저장소에 발송 코드는 없다.

## 1. 절대 불변식 — 위반하는 코드는 작성하지 마라

1. **LLM은 수치·판정을 확정하지 않는다.** 신호 발화, 게이트 통과, 라벨 확정, 채점 확정은 전부 결정론 코드. LLM 산출물은 항상 게이트를 거쳐야 저장된다.
2. **evidence 없는 산출물 금지.** Signal·Draft 블록은 evidence(MySQL record_id 논리 참조)가 비면 생성자에서 실패해야 한다. "그럴듯한 문장으로 메우기" 금지 — 실패는 섹션 비움 + 사유로 정직하게.
3. **실명·연락처는 경계를 넘지 않는다.** 입력은 alias만. LLM 전송 직전 redaction 필수(fail-closed — 불확실하면 전송 중단). 에이전트 도구 반환값도 마스킹 통과분만. 패턴: `docs/policies/masking_redaction.md`.
4. **게이트 거부는 에러가 아니다.** `rejected_insufficient`·`template_only`는 200 + status. `GateRejected`를 5xx로 올리면 리뷰 반려. 코드 사전: `docs/policies/error_codes.md`.
5. **강사 지시가 게이트를 이기지 못한다.** refine(핑퐁)의 매 턴도 게이트 전체 재통과. `docs/part_a/06_refine_policy.md`.
6. **모든 루프에 상한.** 블록 재생성 ≤3, 조사 루프 ≤5, 학생 큐 유한. 무한 루프 가능 구조는 반려.
7. **teacher_only 데이터(반 평균·석차)는 학부모向 프롬프트에 조립 자체가 안 된다** — 컨텍스트 조립기의 audience 필터가 1차 방어(`docs/part_a/07_report_spec.md` §4). 프롬프트 지시로 막는 방식 금지.
8. **재현성.** 모든 실행은 AI_RUN(버전 세트 + snapshot_hash) 기록. 동일 입력 + 동일 버전 = 동일 출력(결정론 경로는 바이트 동일).

## 2. 아키텍처 경계 — 어디를 수정해도 되는가

- 폴더 구조는 지시서 고정 — **구조 재편 금지.** 소유권: `docs/02_ownership.md`.
- capability 간 참조는 `contracts/`의 타입으로만. 상대 capability 내부 직접 import 금지.
- **양자 승인 13파일**(`contracts/execution.py·llm.py·gates.py·evaluation.py·taxonomy.py·agents.py·graphrag.py`, `evidence/models.py`의 EvidenceRef, `runtime/metrics.py` 이벤트 스키마, `api/app.py·api/envelope.py`, `db/models.py·db/base.py`)은 에이전트가 임의 수정하지 말 것 — 변경이 필요하면 PR 설명에 사유를 쓰고 사람 승인을 기다린다.
- 데이터 파일(`tone_map.yaml`·`buffer_lexicon.yaml`·`redaction_patterns.yaml`)은 대응 골든셋 통과가 머지 조건. 규칙을 프롬프트에 하드코딩하지 말고 데이터 파일로.

## 3. 지금 하지 말 것 (미확정 — `docs/99_open_items.md` 추적)

| 항목 | 이유 | 대신 |
| --- | --- | --- |
| Kafka terminal outbox·실연동 | 슈퍼바이저 실행 계약과 영속 Job은 확정·구현됐으나 토픽 운영값·백엔드 `result_ref` 조회 방식은 공동 확정 전 | `docs/08_kafka_events.md` 인터페이스와 `(job_id, terminal phase)` 멱등 규약까지만 준수 |
| `item_format`의 short·essay 분기 코드 | 7/15 확정: **v1은 mcq만 사용** | enum엔 예약값만 두고 처리 로직 만들지 말 것 (area 6영역은 확정 — TODO 제거 가능) |
| ~~LLM 벤더 SDK 설치·직접 호출~~ **해제(7/23 B-5 확정)** | 팀 로컬 OpenAI 호환 서버(Gemma 계열)로 확정 | `openai` SDK는 **`llm/providers/` 안에서만** import 허용 — capability·contracts에서 직접 import 금지(벤더 독립 유지). provider 어댑터는 A 초안 + B 승인(llm/ 소유). 접속정보(URL·키·모델)는 env 주입 |
| 백엔드 실연동 | 계약 리뷰는 완료(7/15) — **v1.0 승격 커밋 + Kafka 토픽 스키마 확정 전** | FakeSnapshot 픽스처 (`docs/05_request_json.md` 형태) + Kafka는 뼈대만 |
| F17(지면 시험 OCR) 구현 | Phase 2 | 스키마 선반영만(`docs/policies/f17_paper_exam.md` §4) |

## 4. 스택·명령어

- Python 3.12 + uv. `uv sync` → `uv run pytest` / `uv run ruff check .` / `uv run mypy .`
- 패키지 루트 = `src/ai/` (src 레이아웃). 서버: `uv run uvicorn ai.api.app:app --reload`
- FastAPI(async) · SQLAlchemy 2.x + asyncpg · Alembic · LangGraph(+postgres checkpointer) · pandas/numpy/openpyxl · **Kafka(7/15 확정 — 비동기 완료 통지·월별 리포트 벌크. 토픽 확정 전엔 consumer/producer 뼈대만, aiokafka)**
- AI PG는 산출물·메타·캐시만(애플리케이션 소유 26테이블 — `docs/06_erd.md`; LangGraph PostgresSaver 내부 테이블은 별도 관리). 도메인 원본 테이블을 만들지 마라.
- 전 테이블 `tenant_id` 필수. 테넌트 격리 없는 쿼리는 반려.

## 5. 테스트 규칙

- 구조는 프로덕션 대칭(`tests/ai/unit·contract·integration·gates·golden·failure·fakes`).
- LLM 낀 기능은 정답 비교가 아니라 **불변식 검증**(게이트·구조·근거 실존). FakeProvider 시나리오로 결정론화.
- 골든셋(`evaluation/golden/`)의 기대값 임의 수정 금지 — threshold 시트·어휘집 버전과 연동(`docs/part_a/08_evaluation_plan.md`).
- redaction 코퍼스 **미탐 0건 = CI 게이트.** refine 공격 케이스 A1~A7 차단 미탐 0건.
- 새 기능 PR에는 대응 failure 케이스(장애·소진·차단) 테스트 포함.

## 6. 코딩 컨벤션 — 상세는 `docs/03_coding_rules.md` (리뷰 반려 기준)

핵심만: **하드코딩 금지**(임계값=DB·규칙=yaml·프롬프트=템플릿·설정=Settings — "값이 바뀌면 코드 diff가 생기면 위치가 틀린 것") · **바퀴 재발명 금지** — 직접 짜기 전에 표준 라이브러리→기존 의존성→검증된 라이브러리 순으로 찾는다(재시도=tenacity, 시간대=zoneinfo 등 금지 목록은 03번 §1b — 단 개인정보 경로엔 외부 전송 라이브러리 금지) · capability 간 직접 import 금지(contracts 경유) · 계산과 I/O 분리(순수 함수) · 외부 의존은 인터페이스+주입(clock 포함) · 경계 데이터는 전부 Pydantic · `except: pass` 금지 · 테스트 없는 로직 PR 금지 · TODO에는 안건 번호(`# TODO(Open-11):`) · ruff+mypy 통과 · 주석·커밋 한국어 OK, 식별자는 영어.

## 7. 문서 맵 — 구현 전 반드시 해당 문서를 읽어라

폴더 구분: `docs/` 루트+`policies/` = 전원 공용 규율 · `docs/part_a/` = member-A 상세 설계(B 파트는 `part_b/` 추가 예정).

| 작업 | 먼저 읽을 것 |
| --- | --- |
| 감지 규칙·베이스라인 | `docs/part_a/04_threshold_config.md` + `docs/part_a/03_usecases.md` D1~D3 |
| 상담 초안·톤 | `docs/part_a/05_tone_mapping.md` + 유스케이스 C1~C3 |
| refine(핑퐁) | `docs/part_a/06_refine_policy.md` + 유스케이스 C8 |
| 리포트 | `docs/part_a/07_report_spec.md` + 유스케이스 C9 |
| 태깅·수능 enum | `docs/policies/taxonomy.md` (B-3 경계 7건 확정) + 유스케이스 I4 |
| Import·에이전트② | `docs/part_a/01_pipeline.md` + 유스케이스 I1~I3 |
| 슈퍼바이저·워커 Job | `docs/policies/langgraph_state.md` §3·§5 + `docs/02_ownership.md` |
| 문제 생성·검증 | `docs/part_b/01_pipeline.md` + `05_problem_generation.md` + `06_quality_gates.md` |
| API 형태 | `docs/04_api_contract.md` + `docs/05_request_json.md` |
| DB | `docs/06_erd.md` |
| 표준 스키마(템플릿·Import 목적지) | `docs/07_standard_schema.md` |
| Kafka 이벤트 | `docs/08_kafka_events.md` (확정 전 — 인터페이스만) |
| 쿼터 | **AI는 쿼터 무관(7/15 확정)** — 차단·카운트·표시 전부 백엔드. AI에 남는 건 LLM 원가 기록뿐(quota_metering.md 상단 참조) |
| 에러·상태 | `docs/policies/error_codes.md` |
| 골든셋·평가 | `docs/part_a/08_evaluation_plan.md` |
