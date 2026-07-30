# [체크온] B 통합 제안서 v2 — A 변경 요청 · 공용 문서/계약 변경 제안 · OPEN 총괄

> **지위:** member-B(염준영)의 공식 통합 제안과 승인 이력. `[제안]` 항목은 오너 승인 전까지 확정되지 않으며, `✅ A+B 승인 완료`로 표시된 항목은 승인된 결정 기록이다. A 소유 문서·공용 계약·정책·ERD 변경은 `docs/02_ownership.md` 절차를 따른다. A가 이미 요청한 리뷰 반영은 직접 갱신하고, 독립 크로스체크에서 새로 발견한 A·백엔드 안건은 기존 정본 값을 바꾸지 않은 채 원본 조항에 `[PART_B 크로스체킹 요청 · 미확정]`으로 남긴다.
>
> **변경 이력**
> - v1.10 (2026-07-29): §2-13 `type_tag` 어휘 확장 제안 신설, §3에 수능형 포맷 총괄 W8·`type_tag` 표현력 W9·롤백 재검증 W10 등록, §2-8에 `12_suneung_format_alignment.md` INDEX 링크 제안을 추가했다.
> - v2.0 (2026-07-28): **A 공식 회신 전수 반영** — §2-4의 B 테이블 수·ERD parity 기준을 8종·34개로 통일하고 `ITEM_CANDIDATE` 유니크 제약을 원자 PR 조건에 추가. A-10 gateway 공용 규칙 승격과 데모 rank를 완료 처리하고, A-4 조건(meta.versions 10→13키 동시 개정·GraphRAG 버전 축 독립성)을 §2-12-①에 반영. `ReviewReason`·`DifficultyBand`의 `error_codes.md` §2.6 편입을 제안하고, A-6 판정에 따라 RLS 문서 불일치를 해소·실도입을 BE-11로 이관. B-8은 ⓐ validator와 ⓑ GraphRAG 3필드를 한 안건으로 병합했다.
> - v1.9 (2026-07-27): **§1-10 「A 게이트웨이 협의 3건 — B 회신」 신설** — ① role별 전송 재시도 `0..1` 파라미터를 **B가 게이트웨이에 신설**(생성자 주입·기본 1로 문제생성 무변경·A의 "어댑터 직결" 차선책은 `01` §5 위반이라 수용 불가) ② recorder가 `ExecutionContext`를 함께 받는 ②안 동의 + `LlmCallRecord`가 양자가 아닌 **B 단독 소유**임을 정정 ③ 마스킹 훅을 **전송 redaction(fail-closed·no-op 불가)과 트레이스 마스킹(no-op 허용) 2개로 분리**.
> - v1.8 (2026-07-27): B-M2-01 확정 반영 — `PROBLEM_ITEM.difficulty_fit` 제안의 확정 대기 표기를 해소.
> - v1.7 (2026-07-27): **§1-9 「A 작업 지시 일람」 신설** — A 승인·작업 9건(P0 5건)을 한 표로 집약. **§2-12 GraphRAG 공용 계약 확장 제안** 신설(`VersionSet` 3필드 · evidence resolver 병합 · `graph_version` 재사용 금지). §3 OPEN 총괄에 B-8~B-10과 와이어프레임 충돌 W1~W7 등록.
> - v1.6 (2026-07-27): §2-4 증보 — B 테이블 편입의 승인 형태(PR 리뷰 = 승인)·원자 PR 파일 목록·컬럼 스펙 반영분·저장소 ORM 규약을 확정 수준으로 기술. 독립 크로스체크에서 새로 발견한 3건 등록: `WEAKNESS_MAP.overall_low` 컬럼 부재(계약 존재), `PROBLEM_SET.request`의 난이도 표기와 `ProblemRequest` 필드 부재 불일치, **RLS 구현 부재**(문서 원칙과 현행 구현 불일치 — §2-4.5).
> - v1.4 (2026-07-22): PR #10·#11의 A 판정을 B 추적표에 회신 반영 — 기존 크로스체크의 완료·후속 백로그를 분리하고, `REVISION_CONFLICT`·B 결과 어휘 5종의 A 정본 편입 완료를 갱신. A가 새로 확정한 **ongoing·R5 상한 제외**는 B가 수용하되, 공용/API 요약 동기화·병합 lifecycle 경계·회귀/데모 보강은 해당 A 문서에 새 크로스체킹 요청으로 등록.
> - v1.3 (2026-07-22): `develop`의 A PR 리뷰 요청 4건에 B 회신 — 승인 항목은 직접 반영하고, 그 과정에서 새로 발견한 간극만 해당 A·공용 원본에 **B 제안 해결안과 크로스체킹 요청**으로 등록. B 소유 충돌 규약·상태 필드 분류·HTTP DTO 경계와 §2-10 실제 완료 현황은 확정 반영.
> - v1.2 (2026-07-15): **공용 계약 B 확장 14항목 A+B 승인 완료 반영** — §2-3·§2-9를 승인·구현 완료(커밋 `d5283d0`)로 전환(제안 이력 보존), §2-10(공용 문서 동기화 요청 일람) 신설, §3 B-2 갱신.
> - v1.1 (2026-07-15): 파일 번호 이동(06→09) + 7/15 결정 정리 — 해소 안건 분리(§0), Kafka 이벤트·409 충돌 코드·라벨 사전 제안 추가, B-1 회신 갱신, 쿼터 제안 폐기 반영. part_b 재편(01~09)에 따른 참조 갱신.
> - v1 (2026-07-15): part_b 정리 과정에서 도출된 요청·제안 일괄 등록.

---

## §0. 7/15 회의로 해소된 안건 (기록 — 재론 불요)

| 안건 | 결과 | part_b 반영 |
| --- | --- | --- |
| Open-11 / B-3 / D-01 | ✅ 6영역 채택 · **v1 item_format = mcq만**(short·essay 예약) · 측정 대상 기준 경계 사례 7건 확정 | 05 §4·§9, taxonomy §2·08 코퍼스 |
| B-1 | ✅ 슈퍼바이저 1 + 워커 3(문제 생성 = B 워커) — 영속 Job·lease·부분 수렴 실행 계약 확정 | 01 §0·§6 · langgraph_state §5 |
| B-4 / D-08 | ❌ **폐기** — v1 서술형 없음. F17 OCR 소유만 Open-12와 P2 재론 | 05 §9 예약 |
| BE-4 / D-05 | ✅ 쿼터 전부 백엔드 — **AI는 쿼터 무관, meta.quota 폐기** | 01 §5, 05 §4.4, 07 §5 |
| Open-2 | ✅ 비동기 완료 통지 = **Kafka** | 02 §1-A — 이벤트 증분 제안(§2-1) |
| B-6 / D-07 | ✅ LangSmith 공통 1개 도입(마스킹 훅 게이트웨이 앞단) | 01 §5 |
| D-02 | ✅ (대화 확정) 검증 차단 문항 저장 가능·발행 차단·**수동 예외 승인 불허** | 06 §3 |
| 타겟 협소화 | ✅ 수능 고등 기준 — v1 중등 분기 금지 | 04 §2 |
| (7/15 후속) contracts 공용 5파일 | ✅ A가 구현 완료(`feat/contracts-base` — taxonomy·execution·llm·gates·evaluation + 테스트 74종) | B 회신 §1-5 · (당시) 확장 제안 §2-3·§2-9 → 아래 행에서 승인 완료 |
| (7/15 오프라인) **공용 계약 B 확장 14항목** | ✅ **A+B 승인 완료** — A와 실시간 협의로 승인, 커밋 `d5283d0` 구현 반영(Capability 2 · VersionSet 4 · GateName 3 · OwnerKind 1 · BlockedReason 3 · GoldenSuite 1) | §2-3·§2-9(승인 완료) — §2-10 문서 동기화 **3/7 완료, 4건 잔여** |

## §1. A 소유 문서·코드 변경 요청 (승인 주체: 박진희)

### 1-1. part_a의 OCR=A 표현 정정 요청 — Open-12 `(C-02)`

- 대상: `part_a/01_pipeline.md` F17 절("OCR 답안 추출(A: import 확장)", "OCR·수집·learning_event 변환 = A") 및 `part_a/02_design.md` 동일 표현.
- 근거: `docs/policies/f17_paper_exam.md` §5·`99_open_items` Open-12는 OCR 판독 소유를 **미정**으로 둔다(B-4 폐기 후 Open-12로 흡수). 공용 정책 우선 — "미정(Open-12·P2)"으로 정정 요청.
- 확정 분담(유지): 스캔 수신·실명 매칭·이미지 마스킹=백엔드.

### 1-2. `agents/` 반영 + 슈퍼바이저 확인 — B-1 완료

- `AGENT_RUN.agent_kind=problem_generation`과 공통 Job 실행 필드를 반영한다. 슈퍼바이저 실행 계약(`policies/langgraph_state.md` §5)은 **B 리뷰 완료** — Job 단위는 세트·문항 리비전·재검증 operation 각각 1건이며, 인터랙티브 작업은 실행 중 강제 선점하지 않고 워커 체크포인트 경계에서만 협력적으로 양보한다. `agents/` 구현은 A, 공통 Job·라우팅 계약은 양자 승인이다.
- LangGraph 버전은 llm/ 의존성과 함께 B가 고정하고 A 리뷰.

### 1-3. `gates/chain.py` — 변경 불요 확인 요청 `(C-07)`

- B 게이트 3단은 workflow 노드로 **자체 실행**, 기록 타입만 공용 `GateResult` 사용(chain.py 무변경). 이견 시 재론.

### 1-4. `part_a/08_evaluation_plan.md` §1 트리 증보 요청

- `golden/problems/`(하위 7종 — [`08`](08_evaluation_plan.md) §1)·`golden/diagnosis/` 행 추가. 편입 방식(§9 편입 vs 병렬)은 A 리뷰 — B는 병렬 유지 기본.

### 1-5. A → B 승인 요청 회신 — `feat/contracts-base` (contracts 5파일 + 테스트 74종)

A가 요청한 B 검토 2건에 대한 회신:

| 요청 | B 회신 |
| --- | --- |
| `execution.py` 신규 필드 2개(`threshold_version` nullable · `contract_version` non-null) — 양자 승인 | **승인** — 독자 설계가 아니라 §2.2/ERD 두 문서의 합집합 정합. **교집합 대안도 검토했으나 기각**: 교집합이면 `{pipeline, engine}` 2종만 남아 threshold(감지 판정 기준)·prompt(LLM 실행 기준)를 잃고 과거 실행 재현 불가(불변식 8). 두 문서는 별개 시스템이 아니라 같은 대상(실행 1건의 재현 키)의 불완전한 명세였으므로 합집합이 정답이고, 실행 유형별 차이(개별로 돌아가는 부분)는 필드 삭제가 아니라 **nullable로 흡수**(detection: prompt=null / LLM 실행: threshold=null). VersionSet은 워커 통합 스키마가 아니라 실행 1건마다 찍히는 재현 도장이라 슈퍼바이저 통합 여부와 무관. 같은 선례(용도별 nullable)에 따라 **B 버전 확장을 §2-9로 예고** — 승인 시점에 함께 논의 희망 |
| `llm.py`의 `LLMProvider` Protocol을 B의 FakeProvider가 구현 가능한지 | **구현 가능 — 이견 없음.** `name` + `async complete(request, context) → LLMResult` 시그니처로 결정론 응답·장애 시나리오(timeout·parse_fail·연속 실패)를 `outcome`/`LlmError` 계열로 전부 재현 가능. 재시도·백오프를 어댑터가 아닌 게이트웨이(tenacity) 소유로 둔 규약도 B의 전송 재시도 설계([`06`](06_quality_gates.md) §4 `transport_retry`)와 정합. blind 계약은 `LLMRequest.prompt`가 조립 완료본이므로 조립 단계(verification.py) 책임으로 유지 — 계약 충돌 없음 |
| OpenAI 호환 어댑터의 빈 응답 매핑 | **B 확정:** `ParseFailed`를 유지한다. 게이트웨이 전송 재시도 대상이 아니며 상위 소비자의 블록 재생성·`item_attempt` 예산이 소진한다. |

> **[PART_B 크로스체킹 요청 · 미확정 — 타임아웃 상한]** 어댑터의 호출 전체 상한 15초와 `error_codes.md` §1의 동기 10초가 다르다. 동기 경로에 별도 10초 상한이 있는지 A·백엔드 확인이 필요하다.

### 1-6. 7/22 A PR 리뷰 요청 4건 — B 회신

아래는 A가 PR에서 이미 요청한 검토에 대한 B 회신이다. 다시 A 확인 안건으로 돌리지 않고 승인·보완 여부를 직접 정리했다.

| A 요청 묶음 | B 회신 |
| --- | --- |
| 감지 계약(lifecycle AI 소유·3값, `display_label`, taxonomy 공용 enum) | ✅ 승인. 계약·구현·테스트가 일치하며 B 추가 변경 없음 |
| FakeSnapshot 위치·제외 학생·hash 플레이스홀더 | ✅ `evaluation/fake_snapshot.py` 위치 승인. A 감지 픽스처를 B 소유 `tests/ai/fakes/`에 두지 않는 근거가 타당하며, 프로덕션 import 금지 원칙과 재현용 placeholder 설명도 수용. `02_ownership.md`·`99_open_items.md`에 완료 반영 |
| score/readapt/R2·meta/capped_out 설계 | ✅ R2 v0 `consecutive_missing` 한정, 엔진 밖 `meta.versions`, `capped_out` 집계 한정을 승인. A 후속 판정으로 readapt=최근 30일 이력 존재, score 0점=원임계, `capped_out`=`new`·`follow_up` 상한 탈락분으로 확정. ongoing·R5 상한 제외 후속은 §1-8 |
| `api/` 구조·v0 한계 | ✅ `app.py`·`envelope.py` 공통 양자 승인 + 라우터 capability 오너 구조 승인. `02_ownership.md` 양자 승인 9곳과 99 ⑧에 확정 반영. 인메모리 멱등·DB 미적재 등 공지된 v0 한계는 백로그 유지; 별도로 발견한 wire·보안 간극만 §1-7로 요청 |

### 1-7. 독립 크로스체크에서 새로 발견한 A·백엔드 협업 요청

A PR 요청을 반영·검토한 뒤 B가 추가로 발견한 간극만 해당 원본 조항 바로 아래에 `[PART_B 크로스체킹 요청 · 미확정]`으로 남겼다. 기존 A 규약 값은 바꾸지 않았다.

| 원본·위치 | 새로 발견한 확인 요청 | 상태 |
| --- | --- | --- |
| `part_a/09_detect_spec.md` §2·§3·§4 | 증분 입력↔8주 baseline · date/enum 경계 검증 · 제외 이벤트 처리 순서 · AlertContext 불변식 · brief/evidence · lifecycle 다건/억제 순서·14일 경계 | ◐ A 판정 대부분 완료 — 영속 baseline은 D-②, 관련 실존 evidence 전무 시 처리는 D⑪, 미래 `resolved_at` 거부 경계는 잔여 |
| `part_a/04_threshold_config.md` §2·§3·§3.1 | readapt 시간 검증 주체 · lifecycle 억제/상한 순서 · score 0점 기준 | ✅ A 판정·엔진 반영 완료 — readapt=30일 이력, 억제 선적용, score=원임계, ongoing·R5 상한 제외(#14) |
| `part_a/02_design.md` §1-A · `03_usecases.md` D1 · `08_evaluation_plan.md` §2 · `09_detect_spec.md` §3·§4 · `06_erd.md` SIGNAL | 새 상한 정책과 기존 “전체 TOP 3~5” 요약 동기화 · 병합 primary/secondary lifecycle 경계 · 초과/capped_out 회귀 · rank/데모 | ☐ A+BE 확인 — 원본 조항에 신규 메모, §1-8 |
| `04_api_contract.md` §2.2·§2.3 | capability별 version 불변식 · 실패 meta · tenant/경로 멱등 스코프 · 요청 검증 · 실제 LLM 예외 매핑 · 민감 detail · tracing | ◐ A 방향 판정·detect 경로 일부 반영 — VersionSet 양자 협의, 공용 실패 meta/검증, LLM adapter, 민감 detail, 멱등 D-②, 로그 correlation 잔여 `[P0]` |
| `04_api_contract.md` §3.0·§3.1·§4.1 | `signals[]` 수·rank·`capped_out`을 ongoing·R5 상한 제외 정책과 동기화 | ☐ A+B+BE 확인 — 원본 조항에 신규 메모, §1-8 |
| `02_ownership.md` §5 아래 | 프로덕션 capability → `ai.evaluation` 역방향 import 금지 자동 검사 | ✅ A 수용·AST 회귀 테스트 반영 완료 (`tests/ai/contract/test_evaluation_isolation.py`) |

### 1-8. ongoing·R5 상한 제외 — A 확정 수용·후속 크로스체크

**A 확정(#14, 7/22)을 B도 수용한다.** 파이프라인은 `학생별 병합 → lifecycle 억제 탈락 → new·follow_up만 랭킹·상한 → ongoing·R5 상한 밖 합류 → 응답`이며, `capped_out`은 `new`·`follow_up` 후보의 탈락 수만 센다. 따라서 전체 응답 신호 수와 rank는 `cap_max`를 넘을 수 있다. 기존 Alert의 brief·evidence를 교체하는 백엔드 처리에는 변경이 없다.

다만 아래는 A 확정값을 바꾸지 않고 원본 조항에 `[PART_B 크로스체킹 요청 · 미확정]`으로 등록했다.

| 후속 | B 제안 · 확인 요청 | 상태 |
| --- | --- | --- |
| 요약 계약 동기화 | **확인된 불일치:** 공용 `04_api_contract` §3.0·§3.1은 아직 “신호 TOP 3~5”·“상한 적용 후의 신호만”·`capped_out=상한에 밀린 후보 수`로 적고, `contracts/detection.py`도 Signal·rank·capped_out을 전체 상한 기준으로 설명한다. `02_design`·`03_usecases`·`08_evaluation`·`09_detect_spec`까지 `new`·`follow_up` 대상, 최종 수/rank>5, `capped_out` 범위로 동기화하고 BE·FE의 길이/rank≤5 가정도 확인 | ☐ A+B+BE |
| 병합 lifecycle 경계 | **확인된 현행:** `merge_student`는 비-R5를 1경보로 병합하고, 엔진은 `primary.signal_type` 하나로만 lifecycle을 판정한다. 따라서 primary=ongoing·secondary=new이면 전체가 ongoing 상한 밖이고, secondary의 finding은 `_build_signal` evidence 조립에만 쓰여 new lifecycle은 응답에서 드러나지 않는다. 반대 방향(secondary만 ongoing)도 해당 이력을 판정하지 않는다. 현행 primary 기준 고정과 lifecycle별 분리 중 A+BE가 결정하고 양방향 회귀 필요 | ☐ A+BE(+B 리뷰) |
| 회귀 보강 | **확인된 커버리지:** `tests/ai/unit/detection/test_engine.py`에는 `cap_max`·`capped_out`·rank 조합 회귀가 0건이다. golden에는 new 6→5·억제 선탈락·`ongoing 3 + new 5 → 8, capped_out=0`만 있다. `ongoing 3 + new 6 → 8, capped_out=1`, new+follow_up 공동 상한, R5 상한 밖 조합, 다중 반 독립 rank/합산, 상한 밖 `student_ref` 정렬·정확한 rank, 병합 lifecycle 양방향을 추가 고정 | ☐ A |
| 데모 rank | ✅ **해소(7/26, `4a4f1b1`)** — `detect_demo_response.json`이 현 정책 순서인 `st_07=1` · `st_09=2` · `st_08(R5)=3` · `st_10(ongoing)=4`로 재생성돼 엔진 출력과 정합한다 | ✅ 완료 |

### 1-10. A 게이트웨이 협의 3건 — B 회신 `[2026-07-27]`

A가 브리핑·mapping_probe 배선 전에 요청한 게이트웨이(`llm/` — **B 단독 소유**) 협의 3건에 대한 B 확정 회신이다.

| 안건 | B 회신 | 작업 주체 |
| --- | --- | --- |
| **① role별 전송 재시도 0회 허용** | ✅ **승인 → `[2026-07-28] 구현 완료·머지`**(A PR `feat/llm-gateway-policy-v1`). A가 제시한 차선책 "어댑터 직결 유지"는 [`01`](01_pipeline.md) §5 "모든 LLM 호출은 gateway 경유 — 예외 없음" **위반이므로 수용 불가**였고, 대신 B가 파라미터를 열기로 했으나 **A가 합의문 그대로 구현**했다. 조건 (a)~(d) 전수 충족 확인: 생성자 주입 · `0..1` 기동 실패(`_validated_transport_retry`) · sheet-agnostic · 성공·예외 양쪽 경로 시도별 기록 | A(구현·머지) · B(리뷰 완료) |
| **② `LlmCallRecord.execution_id`** | ✅ **②안 동의 → `[2026-07-28] 구현 완료·머지`.** `LlmCallRecorder = Callable[[LlmCallRecord, ExecutionContext], None]`로 확장됐고, B 조건이던 **"적재 실패가 호출을 실패시키지 않되 조용한 누락도 금지"** 가 `LlmGateway.record_failures` 카운터로 구현됐다. `LlmCallRecord`의 정의("호출 1회분 비민감 관측 메타")를 지켜 실행 문맥을 record에 복제하지 않았다 | A(적재) · B(리뷰 완료) |
| **③ LangSmith 마스킹 훅** | ◐ **조건부 승인 — 훅을 2개로 분리**. "기본 no-op 단일 훅"은 `CLAUDE.md` 불변식 3과 충돌. **잔여 — 별도 PR** | B(훅 신설) · A(마스킹 함수 주입) |
| **(부수) `ModelRole.NARRATOR` 신설** | ✅ **O → `[2026-07-28] 구현 완료.`** `composer` 대신 **작업 성격 기반** 이름을 택해 기존 4종(생성·검증·매핑추론·분류) 관례와 정합. capability 전체를 뜻하지 않으므로 초안·리포트·refine이 자동 흡수되지 않는다 — 그 셋을 narrator에 넣을지는 배선 시점에 **재시도 정책이 브리핑과 같아도 되는지**로 판단한다. `llm_call.role`이 varchar라 마이그레이션 없음(`06_erd.md` §255 반영 완료) | A(신설) · B(승인) |

#### ① 전송 재시도 파라미터 — B 확정 사양

```python
LlmGateway(providers, *, recorder=..., transport_retry: Mapping[ModelRole, int] | None = None)
```

| 규약 | 내용 | 근거 |
| --- | --- | --- |
| 주입 지점 | **생성자**(호출별 금지) — 같은 role이 호출마다 다른 재시도를 가지면 재현성이 깨지고 `LLM_CALL` 원가 회계 해석이 갈린다 | 불변식 8 · [`06`](06_quality_gates.md) §4 |
| 값 범위 | `0..1`, 벗어나면 **기동 실패** | 불변식 6(모든 루프에 상한) |
| 기본값 | 미지정 role은 `1`(총 2회 — 현행 보존) → **문제생성 무변경** | [`06`](06_quality_gates.md) §4 최악 논리 6콜·전송 12요청 유지 |
| 단일 원천 | `generator`·`verifier`는 `verify_config.transport_retry`를 조립 시점에 주입. 게이트웨이는 시트를 모른다(계산·I/O 분리) | `CLAUDE.md` §6 · `03_coding_rules` |
| **가드 위치** `[2026-07-28 확정 — A 해석 승인]` | 단일 원천 보장은 **게이트웨이 내부 검사가 아니라 조립부 테스트**로 한다. 게이트웨이는 sheet-agnostic을 유지하고 `0..1` 범위 검증만 남긴다. **B 담당분(B-5 정리 PR):** `src/ai/problem_generation/provider.py`(신규 — `composition/provider.py`·`import_mapping/provider.py` 선례)가 `verify_config.transport_retry`를 읽어 `{GENERATOR, VERIFIER}`에 주입하고, `tests/ai/unit/problem_generation/test_provider.py`가 ① 시트값이 그대로 주입 ② 시트에 없는 role은 미주입(기본 1로 낙하) ③ 시트값이 `0..1` 밖이면 **조립 단계**에서 실패 를 고정한다. 시트를 patch했을 때 주입값이 따라 바뀌는지로 **리터럴 하드코딩 부재**를 증명한다 | 위 "단일 원천" 행의 문언 해석 — 게이트웨이에 시트 비교를 넣으면 계산·I/O 분리와 충돌 |

> **`[2026-07-28]` A 해석 확인 — O.** A가 게이트웨이에 `verify_config` 비교를 넣지 않고 sheet-agnostic을 유지한 것은 **위 "단일 원천" 행(line 124)의 문언 그대로**다. B 회신 본문에 쓴 "role 파라미터가 우회하지 못하게"라는 표현이 게이트웨이 내부 강제로 읽힐 여지를 준 **B 측 문언 문제**이며, 같은 회신의 다음 문장("게이트웨이는 시트를 모르고 주입만 받는다")과 이 표가 정본이다. 현재 `problem_generation` 패키지가 없어 **gen/verifier를 게이트웨이에 배선하는 조립부 자체가 존재하지 않으므로** 이번 PR(narrator 단독 배선)에는 실효 차이가 없다. 가드 착수 시점은 B-5 정리 PR이다.
| 회계 | 재시도 0회여도 **시도별 `LlmCallRecord` 기록 유지** | [`06`](06_quality_gates.md) §4 재생성/전송 회계 분리 |
| 예외 | `RedactionBlocked`는 재시도 대상 아님(정책 차단 ≠ 일시 오류) — 현행 `retry_if_exception_type` 유지 | 불변식 3 |

A 소비자 근거(수용): 브리핑은 결정론 템플릿 폴백이 있어 "LLM 실패 = 무재시도 즉시 폴백"이 확립 원칙이고, `04_api_contract` §2.4 [A 확정]의 detect 60s·브리핑 45s·호출당 15s 예산에서 게이트웨이 재시도가 얹히면 호출당 최악 30s가 되어 병렬 예산이 깨진다. mapping_probe도 `part_a/10_import_spec` §3.2 폴백 보유로 동일.

> **B-5 연결:** role 키 설정 구조가 생기므로 `gateway.py`의 `TODO(B-5)`(provider 1개일 때 verifier 패밀리 강제가 우회되는 현재 동작) 제거를 같은 구조에 얹는다. 단 **별도 PR**로 분리해 A 배선을 대기시키지 않는다.

#### ② recorder 시그니처 — 소유권 정정

`LlmCallRecord`는 **양자 계약이 아니라 B 단독 소유**다(`src/ai/llm/gateway.py`, `02_ownership.md` §2에서 `llm/` 전체가 B). 양자는 `contracts/llm.py`이며, 이 record가 **`runtime/metrics.py` 이벤트로 발행되는 시점의 스키마**만 양자다.

- 시그니처: `LlmCallRecorder = Callable[[LlmCallRecord, ExecutionContext], None]`
- **적재 실패가 LLM 호출을 실패시키지 않는다** — gateway의 원가 기록은 **관측**이지 게이트가 아니다([`01`](01_pipeline.md) §5). 실패는 경고+메트릭으로 처리하고 호출은 성공시킨다.
- 단 **조용한 누락도 금지** — 실패 카운터를 둔다.

#### ③ 마스킹 훅 — 2개 분리 + 순서 고정

`CLAUDE.md` 불변식 3과 [`05`](05_problem_generation.md) §5가 요구하는 것이 둘인데 A 제안은 하나로 묶여 있었다.

| 훅 | 대상 | 기본 no-op |
| --- | --- | --- |
| **(a) 전송 redaction** | provider로 나가는 페이로드 | ❌ **불가** — fail-closed. 미주입 시 **기동 실패** |
| **(b) 트레이스 마스킹** | LangSmith로 나가는 트레이스 | ✅ 허용 (B-6 — 트레이스 미사용 시) |

```text
조립(verification.py) → (a) redaction 훅 → (b) trace 훅 → provider 호출
```

- (b)가 (a) **뒤**에 있어야 B-6의 "트레이스엔 마스킹 통과분만"이 성립한다.
- (a) 위반은 `RedactionBlocked` → `CallOutcome.REDACTION_BLOCKED`로 매핑(계약 기존재). **전송 재시도 대상 제외.**
- 마스킹 함수는 `runtime/redaction`(A 소유) 주입, 훅 포인트 신설은 `llm/gateway.py`(B).

#### A 재회신에 대한 B 리코멘트 — 확인 2건 `[2026-07-27]`

A가 조건 포함 3건 전부 수용하며 확인 2건과 제안 1건을 추가했다. B 회신은 아래와 같다.

**확인 1 — `ModelRole` 신설: ✅ O (이름 범위 기준 1건 부기)**

- ERD 영향 축소 확인: `llm_call.role`은 PG enum이 아니라 `String`(`db/models.py`, D-② varchar 확정)이므로 **마이그레이션 불필요**. `contracts/llm.py`(양자) + `docs/06_erd.md` 값 집합 + 인라인 주석이면 충분하다.
- **확인된 구조적 부채:** `ModelRole.GENERATOR`의 docstring이 **"초안·지문 등 생성"** 이다 — **A의 초안과 B의 지문·문항이 현재 같은 role을 공유**한다. ① 로 role이 *정책 경계*가 되면서 드러난 문제이며, 브리핑 하나를 분리해도 초안(reply)·리포트·refine은 여전히 `generator`를 B와 공유한다.
- 당장은 안전하다 — 미지정 role 기본값이 `1`이라 `generator`는 현행 유지되고 B는 무변경이다.
- **이름 결정 기준(A 판단 사항):** 이 role을 초안·리포트·refine이 함께 쓸 것인가. ⑴ 함께 쓴다면 `composer`가 적절하나 그 셋의 전송 재시도가 **브리핑과 동일하게 0회로 강제**된다(refine은 폴백 구조가 달라 사전 확인 권장). ⑵ 브리핑 전용이라면, 기존 4종이 전부 **작업 성격**(생성·검증·매핑추론·분류)이므로 그 관례에 맞는 좁은 이름이 양자 계약 재개봉을 막는다.

**확인 2 — redaction 훅 멱등성: ✅ O (3건 추가)**

1. **토큰 번호 안정성** — 텍스트 동일성뿐 아니라 **매핑 자체의 동일성**까지. `⟪이름1⟫`이 2회 통과 후에도 같은 번호여야 감사 추적·근거 참조가 어긋나지 않는다.
2. **멱등 ≠ 검사 생략** — "이미 토큰이 있으니 통과"로 단축하면 불변식 3("불확실하면 전송 중단")이 깨진다. 훅은 매번 실제 검사하고 `⟪확인필요⟫` 판정도 매번 내린다.
3. **토큰 위조 케이스 `[신규 발견]`** — refine `instruction`·`topic_hint`는 **강사 자유 입력**이므로 강사가 `⟪이름1⟫`을 직접 타이핑할 수 있다. "`⟪⟫` 토큰은 건드리지 않는다"를 그대로 적용하면 **위조 토큰이 마스킹을 우회**한다. 진짜 토큰과 사용자 입력 토큰을 구분할 근거(예: 해당 호출 registry에 실재하는 토큰만 보존)가 필요하다. 성격이 [`05`](05_problem_generation.md) §8.2 injection 방어와 같다.
4. **테스트 위치:** ③ PR 단발이 아니라 **`golden/redaction/` 코퍼스**(미탐 0건 CI 게이트)에 편입해야 이후 패턴 개정에서도 유지된다.

**제안 — "모든 LLM 호출은 gateway 경유" 공용 승격: ✅ O**

규칙의 문서 위치가 곧 적용 범위인데 현재 [`01`](01_pipeline.md) §5에 있어 B 규율로 읽힌다. `docs/03_coding_rules.md` 승격이 적절하다("capability 간 직접 import 금지"와 같은 층). **A 소유 문서이므로 §1-9 A 작업 지시 일람에 등록한다.** 과도기(브리핑 #22 어댑터 직결)도 수용하되 조건 2건: **종료 시점을 ①+② 머지로 고정** · **그때까지 신규 어댑터 직결 추가 금지**.

#### PR 순서 (합의)

**①(B 구현) + ②(A 적재·B 리뷰, +`ModelRole` 신설) 선행 → 머지 후 B가 B-5 정리 → ③ 별도 PR.** `transport_retry` 파라미터는 B가 ① PR 이전에 선행 제공한다.

### 1-9. **A 작업 지시 일람** `[2026-07-27 신설 — A가 여기만 보면 됨]`

M2 문제생성 착수에 필요한 A 승인·작업을 한 표로 모았다. 각 행의 상세는 링크된 절에 있다. **B는 승인 대기 중에도 구현을 진행하며, 멈추는 지점은 머지뿐이다**(§2-4.1).

| # | A가 해야 하는 일 | 상세 | 유형 | 승인되면 풀리는 것 | 상태 |
| --- | --- | --- | --- | --- | --- |
| **A-1** | **B 8테이블의 `docs/06_erd.md` 편입 승인** + 이 PR 한정 `06_erd.md` 편집 go-ahead `[7/27 정정: 7 → 8테이블 — KEEP-4로 ITEM_CANDIDATE 추가]` | §2-4 · §2-4.2 · §2-4.6 | 문서(A 소유) | B 저장 계층 전체. 미승인 시 `problem_generation` 영속화 불가 | ☐ **P0** |
| **A-2** | `EVIDENCE_ITEM.owner_kind` += `problem_item` 양자 승인 | §2-3 마지막 행 | 공용 계약 | `PROBLEM_ITEM.rationale` 근거 저장. 공용 확장 14항목 중 **유일한 미승인 잔여** | ☐ **P0** |
| **A-3** | `tests/ai/db/test_erd_model_parity.py`의 `== 26` → **`== 34`** 상수 변경 동의 `[7/27 정정: 33 → 34]` | §2-4.2 · §2-4.6 | 테스트(양자 성격) | A-1과 같은 PR. 미변경 시 CI 적색 | ☐ P0 |
| **A-4** | **`VersionSet` GraphRAG 3필드 확장** 양자 승인 | §2-12 | 공용 계약 | GraphRAG 실행 재현 키. **`graph_version` 재사용 금지가 핵심** | ☐ **P0** |
| **A-5** | **evidence resolver 주입 시그니처 확정** (기존 B-7 + GraphRAG 경유 해소 병합) | §2-12 · §3 B-7 | `evidence/`(A 소유) | 게이트 ① R-1·R-4의 Graph path·quote·license 검증 | ☐ **P0** |
| **A-6** | ✅ **판정 완료 — 문서 표현을 현행 애플리케이션 계층 격리로 정정하고, RLS 실도입은 BE-11로 이관.** B 8테이블은 기존 26테이블과 동일한 `tenant_id` 컬럼 + 앱 계층 격리 패턴을 유지한다 | §2-4.5 · §3 BE-11 | 공용 정책 | B 저장 계층의 격리 방식 확정. RLS 실도입은 `db/session.py`·`db/store_factory.py` 연결·역할 설계와 함께 백엔드에서 재론 | ✅ 판정 완료 |
| **A-7** | **`[7/27 범위 축소]`** `docs/policies/langgraph_state.md` §2.4의 `ProblemGenerationState` 코드블록에 **필드 2줄 추가 리뷰** — `fallback_ref: str \| None` · `difficulty_regen_used: bool`. `state_schema_version`은 **`v1` 유지**(기본값 보유로 기존 체크포인트 그대로 재개 · 올리면 §3.2에 따라 진행 중 세트 전량 재기동) | §2-4.6 · [`10`](10_m2_problem_generation_architecture.md) §4.1 C3 | 공용 정책(A 리뷰) | 종전 "보존 규칙을 정해달라"에서 축소됨 — **B가 KEEP-1~9로 설계를 닫았고**, 본문은 `ITEM_CANDIDATE`에 두고 state엔 포인터만 둬 §2.4의 "본문 미복제" 원칙을 지킨다 | ☐ 확인 |
| **A-8** | §2-10 문서 동기화 **잔여 4건** — `99_open_items`(B-2 완료 표기) · `part_a/08 §1`(`golden/diagnosis/` 행) · `02_ownership §5`(`golden/diagnosis/` 소유 행) · `00_INDEX`(part_b 링크 절) | §2-10 | 문서(A·공용) | 승인·구현이 끝난 항목의 문서 지연분 | ☐ 잔여 |
| **A-9** | 7/22 감지·API 리뷰 잔여 회신 — ongoing 상한 제외 후 요약 동기화 · 병합 lifecycle 경계 · 회귀/데모 · 공용 실패 meta · 민감 detail 제거 `[P0]` | §1-7 · §1-8 · §2-11 | A(+BE) | B 무관하나 공용 wire 확정에 필요 | ◐ 진행 |
| **A-10** | **"모든 LLM 호출은 gateway 경유 — 예외 없음" 규칙을 `docs/03_coding_rules.md` §2에 승격 완료**(PR #29 · `1d2882c`). 브리핑의 gateway 배선도 `77e016f`로 완료돼 과도기가 종료됐다 | §1-10 | 문서(A 소유) | 규칙의 적용 범위가 A·B 공용으로 확정되고 신규 provider 직결 금지가 공용 규율이 됨 | ✅ 완료(PR #29 · `1d2882c`) |

**P0 5건(A-1~A-5)이 M2 착수의 실질 관문이다.** 나머지는 병렬로 진행 가능하다.

> **B가 A에게 요청하지 않는 것(참고):** `t1_light_mode` 시트 변경·`golden/problems/` 코퍼스·`curriculum_graph.yaml`·`verify_config`·`ProblemRequest.requested_difficulty` 신설·GraphRAG 규격 문서는 전부 **B 단독**이므로 A 승인 대상이 아니다.

## §2. 공용 문서·계약 변경 제안 (승인 주체: 02_ownership 절차)

### 2-1. API·Kafka 계약 증분 `[제안]` — D-10·BE-1 `(C-05)`

현재 정본 계약에 B 엔드포인트가 없다. 아래는 **경로 확정이 아닌 증분 초안** — 공통 규약(envelope·헤더·멱등·202) 준수, 완료 통지는 Kafka(7/15).

| 항목 | 내용 |
| --- | --- |
| `POST /problem-sets` `[가칭]` | 최초 요청 202 + job_id. 내부 command = `ProblemRequest`([`05`](05_problem_generation.md) §4.1 — `target_source` 포함). 같은 Idempotency-Key+같은 바디 재전송은 기존 job 상태·결과 200 |
| `GET /problem-sets/{job_id}` `[가칭]` | **디버그·복구 보조**(폴링 아님) — 상태·진행률·`ProblemSetResult` |
| `POST /problem-sets/{set}/items/{item}/refine` `[가칭 · MVP]` | `instruction`+`base_revision_no` — [`07_refine_policy.md`](07_refine_policy.md). 멱등 조회 → revision/진행 중 검사 순서. 롤백 `revert_to` |
| **Kafka 이벤트** | 공통 `worker_job.succeeded|failed` 사용. 이벤트에는 `job_id`·`operation`·`result_ref|error_code`만 싣고, 문제 세트 status와 성공·실패·미처리 수량은 `result_ref` 조회로 얻는다 |
| 약점 지도 조회 API | **상세 제안 보류** — 세트 응답 동봉 vs 별도 조회 vs 백엔드 사본 동기화는 `OPEN`(D-10, BE+B) |

**B HTTP 경계 확정:** `ProblemRequest`·`ItemRevisionRequest`는 워크플로 내부 command로 유지한다. 외부 HTTP body는 별도 DTO로 만들고 `X-Request-Id`·`Idempotency-Key`·`X-Tenant-Id`를 포함하지 않는다. 공통 헤더를 단일 원천으로 읽어 내부 command에 매핑한다. `api/` 소유권 승인은 완료됐으며, 실제 라우터 편입은 `04_api_contract.md`의 공통 wire 크로스체킹과 백엔드 D-10 합의가 닫힌 뒤 진행한다.

### 2-2. 상태·에러 코드 사전 증보 제안 — `docs/policies/error_codes.md` `(C-10)`

**B 결과 어휘 5종 — ✅ A 정본 편입 완료(7/22, `error_codes.md` §2.6):** 전부 HTTP 에러가 아니라 성공 응답의 `data` 안에 있지만 같은 `status` 필드가 아니다.

| enum · wire 필드 | 값 | 뜻 | 근거 |
| --- | --- | --- | --- |
| `ProblemSetStatus.status` | `partial_success` | 세트 일부 폐기 — 완료분 유효 + stop_reason·사유 보고 | 06 §6 |
| `ProblemItemStatus.items[].status` | `needs_review` | 게이트 통과 + 검토 필수 배지 — 승인 전 발행 불가 | 06 §5 |
| `ProblemItemStatus.items[].status` | `verification_unavailable` | 검증 불능 — 저장 가능·발행 차단·재검증 필수·**수동 우회 불가** | 06 §3 |
| `ProblemFailureReason.items[].failure_reason`·`dropped_reasons[]` | `generation_exhausted` | 재생성 상한(총 3회) 소진 폐기 | 06 §6 |
| `ProblemFailureReason.items[].failure_reason`·`dropped_reasons[]` | `source_unverified` | 사실검증 수단 부재 지문 — 발행 차단 | 05 §2.1 |

**409 멱등 의미 — ✅ A 정본 적용 완료:** 같은 멱등키+같은 바디는 기존 상태·결과 200, 같은 키+다른 바디는 `409 IDEMPOTENCY_CONFLICT`다. 폐기된 구 코드명과 “기존 결과를 409로 반환” 의미는 사용하지 않는다.

**별도 HTTP 충돌 코드 1종 — ✅ A 정본 편입 완료(7/22):** `409 REVISION_CONFLICT`. 새 키+stale `base_revision_no`면 `detail.reason=stale_base_revision`, 새 키+진행 중 refine이면 `detail.reason=revision_in_progress`다. 내부 API에서 두 reason 노출을 허용하며, 멱등 조회를 먼저 수행해 `IDEMPOTENCY_CONFLICT`와 의미를 섞지 않는다.

**refine `blocked_reason` B 증분 3종:** ✅ `answer_integrity` · `banned_topic` · `prompt_injection`은 `error_codes.md` §2.2 편입 완료([`07`](07_refine_policy.md) §3 — `pii_exposure`·`out_of_scope`는 A enum 재사용).

**`ReviewReason`·`DifficultyBand` 편입 `[제안 — A 소유 문서]`:** `docs/policies/error_codes.md` §2.6은 이미 `items[].status`·`failure_reason`·`dropped_reasons[]`를 B 결과 wire 정본으로 관리하므로, BE·FE가 함께 읽는 아래 두 enum도 같은 절에 편입을 요청한다. **값 정의의 소유는 B에 유지하고 공용 사전 편입만 요청한다.** 절차는 `BlockedReason` 3종 편입과 동일하다.

| enum · wire 필드 | 값 | 근거 |
| --- | --- | --- |
| `ReviewReason.items[].review_reason` | `low_confidence` · `area_mismatch` · `t3_literature` · `diagnostic_purpose` · `manual_target_first` · `difficulty_band_mismatch` | [`06`](06_quality_gates.md) §5의 `needs_review` 배지 사유. 폐기 사유인 `failure_reason`과 분리한다 |
| `DifficultyBand.items[].difficulty_band` | `low` · `medium` · `high` | 응답에서 `difficulty_est` 원값과 함께 제공하는 표시·필터용 밴드 |

**프론트 표시 라벨 사전 `[제안 — FE 확정]`:** 내부 상태→한국어 라벨 매핑([`06`](06_quality_gates.md) §0 표) — 검증 통과/검토 필수/검증 차단/생성 실패. 색상만으로 구분 금지, `검토 필수`와 `검증 차단`은 다른 아이콘·문구.

**참고(B 무관 공용 불일치):** ✅ **7/21 통일 완료(`error_codes.md` 정본).**

### 2-3. 공용 enum·계약 확장 — ✅ **A+B 승인 완료 · `d5283d0` 구현 반영** `(C-07·C-09)`

> **상태(7/21):** 아래 확장 중 EVIDENCE_ITEM 행을 제외한 전부가 A와 실시간 협의로 **승인 완료**됐고 커밋 `d5283d0`에 구현·테스트 반영됐다. `GoldenSuite.diagnosis`(`contracts/evaluation.py`)도 같은 승인에 포함. 공용 `error_codes.md` §2.2 동기화도 완료됐다. 아래 표·시안은 제안 당시 이력으로 보존한다.

| 대상 (코드 + ERD) | 변경 | 절차 |
| --- | --- | --- |
| `contracts/execution.py` `Capability` | `diagnosis`·`problem_generation` 추가 — 현 enum은 A 3종뿐(docstring이 "B 테이블 증분 시 함께 확장" 명시) | ✅ 승인·구현 완료(`d5283d0`) |
| `contracts/gates.py` `GateName` + `GATE_RESULT.gate_name` | `RuleValidation \| BlindCrossSolve \| ReleaseDecision` 추가 | ✅ 승인·구현 완료(`d5283d0`) |
| `contracts/gates.py` `OwnerKind` + `GATE_RESULT.owner_kind` | `problem_set` 추가 | ✅ 승인·구현 완료(`d5283d0`) |
| `contracts/gates.py` `BlockedReason` | `answer_integrity`·`banned_topic`·`prompt_injection` 추가([`07`](07_refine_policy.md) §3) | ✅ 승인·구현 완료(`d5283d0`) — ✅ `error_codes.md` §2.2 동기화 완료 |
| `EVIDENCE_ITEM.owner_kind` | `problem_item` 추가 | `evidence/models.py` 구현 시 양자 승인(현재 미구현 — ERD 제안 선반영) |

**제안 당시 코드 시안** (이력 보존 — `d5283d0`에 동일 내용 반영 완료):

```python
# contracts/execution.py — Capability 확장 [제안]
class Capability(StrEnum):
    ...
    DIAGNOSIS = "diagnosis"                      # B — 약점 진단 (결정론 · LLM 금지)
    PROBLEM_GENERATION = "problem_generation"    # B — 출제 워커 ③ (B-1 승인 구조)

# contracts/gates.py — 확장 3종 [제안]
class GateName(StrEnum):
    ...
    RULE_VALIDATION = "RuleValidation"           # B 게이트 ① (06 §1)
    BLIND_CROSS_SOLVE = "BlindCrossSolve"        # B 게이트 ② (06 §2)
    RELEASE_DECISION = "ReleaseDecision"         # B 게이트 ③ (06 §5)

class OwnerKind(StrEnum):
    ...
    PROBLEM_SET = "problem_set"

class BlockedReason(StrEnum):
    ...
    ANSWER_INTEGRITY = "answer_integrity"        # 07 §3 — 정답 구조 훼손 지시
    BANNED_TOPIC = "banned_topic"                # 07 §3 — 금칙 소재 지시
    PROMPT_INJECTION = "prompt_injection"        # 07 §3 · 05 §8.2
```

### 2-4. 공용 ERD 반영 요청 — `docs/06_erd.md` `(C-06)`

B 소유 8테이블(WEAKNESS_MAP·PASSAGE·PROBLEM_SET·PROBLEM_ITEM·VERIFICATION_RESULT·ITEM_REVISION·DIFFICULTY_CALIB·ITEM_CANDIDATE)을 [`02_design.md`](02_design.md) §2와 §2-4.6 기준으로 현재 애플리케이션 26→34테이블로 통합하는 제안이다. 통합 전 정본은 "`docs/06_erd.md`의 26테이블 + part_b 증분 8종"이다.

D-② ERD-parity 안전망은 `tests/ai/db/test_erd_model_parity.py`의 ERD↔`db/models.py` 대조와 `tests/ai/db/test_migration_parity.py`의 모델↔마이그레이션 대조로 연결되므로, B 8테이블 추가 시 `06_erd.md`(정본)+`db/models.py`(양자 승인 12곳)+마이그레이션을 동시에 반영한다.

#### 2-4.1 승인 형태 — 별도 결정 문서 왕복 없음 `[7/27 명확화]`

`02_ownership.md` §4가 "상대방도 코드는 자유롭게 읽고 PR을 보낼 수 있다 — **머지 승인만 오너가 한다**"로 정의하므로, 본 항목의 승인 절차는 **PR 리뷰 자체**다. B가 제안 문서를 내고 A의 결정 문서를 회신받은 뒤 구현에 착수하는 2왕복 절차가 아니다. 구현·문서 diff·테스트를 갖춘 PR이 곧 승인 요청서이며, 멈추는 지점은 **머지 한 곳**이다.

이 PR에서 A 승인이 필요한 항목은 **2건**이다.

| 항목 | 근거 |
| --- | --- |
| B 8테이블의 공용 ERD·ORM 편입 | 본 §2-4 |
| `EVIDENCE_ITEM.owner_kind` += `problem_item` | §2-3 마지막 행 — 공용 계약 B 확장 14항목 중 **유일한 미승인 잔여**. `PROBLEM_ITEM.rationale`의 근거 저장에 선결 |

#### 2-4.2 원자 PR 조건 — 쪼개면 CI가 깨진다

`test_erd_model_parity.py`의 대조는 **양방향**(`orm == erd` — 누락도 초과도 실패)이고, 테이블 수가 상수로 고정돼 있다. 따라서 아래 파일은 **한 PR에 함께** 들어가야 한다.

| 파일 | 변경 | 소유 |
| --- | --- | --- |
| `docs/06_erd.md` | 26 → 34테이블 | A — **A-1 승인 완료, 이 PR 한정 편집 go-ahead** |
| `src/ai/db/models.py` | ORM 8클래스 추가 | 공통 계약(양자) — **A-1 승인 범위** |
| `src/ai/db/migrations/versions/0003_*.py` | 신규 마이그레이션 | 테이블 오너(B) — 모델 diff PR에서 함께 리뷰(§4-1) |
| `tests/ai/db/test_erd_model_parity.py` | `== 26` → `== 34`, `EXPECTED_UNIQUES`에 `weakness_map` (`tenant_id`, `student_ref`, `graph_version`, 주차 컬럼)과 `item_candidate` (`tenant_id`, `set_id`, `slot_index`, `attempt_no`) 2행 추가 | 프로덕션 대칭(양자 성격) |
| `docs/part_b/09_integration_proposals.md` | 본 절 상태 갱신 | B |

`tests/ai/db/test_migration_parity.py`·`test_no_realname_columns.py`는 신규 테이블을 자동으로 검사 대상에 편입하므로 별도 수정이 없어야 정상이다.

#### 2-4.3 8테이블 컬럼 스펙

정본은 [`02_design.md`](02_design.md) §2의 ERD 블록이며, 아래는 그 위에 얹히는 **B 결정 반영분과 확인된 간극**만 적는다.

| 테이블 | 반영분 | 근거 |
| --- | --- | --- |
| `WEAKNESS_MAP` | **`overall_low` boolean 컬럼 추가** — `contracts/diagnosis.py`의 `WeaknessMap.overall_low`가 산출물에 존재하나 §2 ERD 블록에는 컬럼이 없다(확인된 간극) | [`04`](04_curriculum_graph.md) §4 |
| `WEAKNESS_MAP` | `UNIQUE(tenant_id, student_ref, graph_version, 주차)` — 주차 컬럼 표현을 `computed_at` 파생이 아니라 명시 컬럼으로 둘지 확정 필요 | §2 주석 |
| `PROBLEM_SET` | `request` jsonb가 이미 **"난이도"를 포함**한다고 적혀 있으나 `contracts/problem_generation.py`의 `ProblemRequest`에는 난이도 필드가 없다(확인된 간극). 요청 난이도 필드 신설과 함께 정합 | §2 · [`05`](05_problem_generation.md) §4.1 |
| `PROBLEM_ITEM` | **`difficulty_fit` numeric nullable 추가** — 절대 난이도(`difficulty_est`)와 학생 적합도를 분리한다. **v1은 값을 산출하지 않고 항상 null이며 처리 분기 코드를 만들지 않는다** | B-M2-01 = A `[2026-07-27 B 확정]` · 공용 ERD·ORM 편입은 A+B 승인 대상 · [`04`](04_curriculum_graph.md) §1(문항 단위 실측은 B 출제분 제출부터 축적) |

#### 2-4.6 `ITEM_CANDIDATE` — 8번째 테이블 `[KEEP-4 확정 2026-07-27]`

난이도 사유 재생성 시 **첫 검증본 보존**이 확정되면서(KEEP-1) 슬롯 후보 스냅숏 저장소가 필요해졌다. **`PROBLEM_ITEM`에 넣을 수 없는 구조적 이유**가 있다 — `PROBLEM_ITEM`은 슬롯당 1행이고 후보는 `attempt_no`별로 여러 행이라 **키 차수가 다르다.**

| 컬럼 | 타입 | 비고 |
| --- | --- | --- |
| `id` | uuid PK | |
| `set_id` | uuid FK → `PROBLEM_SET` | |
| `tenant_id` | varchar | 전 테이블 공통 |
| `slot_index` | int | `ProblemGenerationState.cursor` 대응 |
| `attempt_no` | int | 1..3 — `item_attempt` 회차 |
| `snapshot` | jsonb | `GeneratedItem` 전문(불변) |
| `gate_summary` | jsonb | ①② 판정 결과·confidence·정렬 판정 |
| `difficulty_est` | numeric | 후보 시점 추정값 |
| `created_at` | timestamptz | |

- **UNIQUE `(tenant_id, set_id, slot_index, attempt_no)`** — `langgraph_state.md` §2.4의 "슬롯 저장 키는 결정론적이며 저장소에서 unique/upsert로 강제"를 후보 축까지 확장한 것.
- **불변 스냅숏**이다. 생성 후 갱신하지 않는다.
- 슬롯 확정 시 승자를 `PROBLEM_ITEM`으로 승격하고, **state의 `fallback_ref`는 clear하되 이 행은 보존**한다(KEEP-8 — 난이도 회귀·골든셋 증보 재료).
- 기각한 대안: `ITEM_REVISION`(강사 수정 이력이라 화면에 오노출) · `VERIFICATION_RESULT.detail`(관측 상세지 본문 저장소 아님) · state 인라인(§2.4 위반) · `PROBLEM_ITEM` 후보 행(수량 불변식 오염).

**연쇄 영향:** B 테이블 **7 → 8**, 공용 ERD **26 → 34**, `test_erd_model_parity.py` 상수 **`== 34`**. A-1·A-3에 반영했다.

#### 2-4.4 저장소 ORM 규약 (편입 시 준수 — `db/models.py`·`db/base.py`에서 확인)

1. **enum성 컬럼은 PG enum이 아니라 `varchar` + 앱 계층 검증**(D-② 확정). `status`·`verdict`·`stage`·`revision_kind`·`drop_reason`·`stop_reason` 전부 `String`이며 값 검증은 `contracts/`의 `StrEnum`이 담당한다.
2. **`…_ref`는 varchar 논리 참조이고 물리 FK가 아니다.** `test_foreign_keys_match_erd`가 ERD의 FK 마커와 ORM 물리 FK를 대조한다.
3. **실명·연락처 컬럼 없음**(불변식 3) — `DIFFICULTY_CALIB.approved_by_ref`도 alias다. `test_no_realname_columns.py`가 강제한다.
4. 타입 매핑 고정: `uuid=Uuid · varchar=String · text=Text · jsonb=JSONB · int=Integer · numeric=Numeric · timestamptz=DateTime(timezone=True) · boolean=Boolean`.
5. 제약·인덱스 이름은 `db/base.py`의 `NAMING_CONVENTION`을 따른다(alembic autogenerate diff 안정화). `UniqueConstraint`는 `uq_<table>_<의미>` 형태로 이름을 명시한다.

#### 2-4.5 ✅ 해소 — 현행 앱 계층 격리 확정 · RLS 실도입은 BE-11 이관

[`02_design.md`](02_design.md) §2와 `docs/06_erd.md`의 원칙은 "전 테이블 `tenant_id`+**RLS**"로 적혀 있으나, **현재 저장소에는 RLS가 구현돼 있지 않다** — `src/ai/` 전체에서 `ROW LEVEL SECURITY`·`CREATE POLICY` 검색 결과 0건이며, 기존 26테이블은 `tenant_id` varchar 컬럼만 두고 격리를 앱 계층에서 수행한다.

**A 판정 완료:** 문서 표현을 현행 구현인 **전 테이블 `tenant_id` 컬럼 + 애플리케이션 계층 격리**로 정정한다. B 8테이블도 기존 26테이블과 동일한 패턴으로 편입하며, B 테이블에만 RLS를 도입하지 않는다.

RLS 실도입 여부는 `db/session.py`·`db/store_factory.py`의 연결·역할 설계와 함께 다뤄야 하므로 **BE-11(백엔드 합의 안건)**로 이관한다. 도입 시 기존 26테이블과 B 8테이블에 일괄 적용하며, 테이블마다 격리 방식을 갈라놓지 않는다.

### 2-5. `docs/02_ownership.md` §5 트리 증보 제안

`evaluation/golden/problems/` 하위 7종([`08`](08_evaluation_plan.md) §1)·`golden/diagnosis/` 신설 행(전부 [염준영]). `pg_banned_topics.yaml`을 B 데이터 파일 규칙(§4-5 — 골든 통과가 머지 조건)에 추가.

### 2-6. `docs/99_open_items.md` 증분 제안 — B 산출물 등록

| 항목 | 선결 |
| --- | --- |
| 문법 DAG 실제 YAML 25~40노드 (`curriculum_graph.yaml`) | B-3 잔여(경계 사례) |
| `golden/problems/` 코퍼스 실파일(§2 18건·§3 11건·§4 10건·§7·§8·§9 RF1~11) | — |
| `golden/diagnosis/` 회귀 실파일 | ✅ 완료 |
| `t1_reference/` 케이스 | D-03 |
| `pg_banned_topics.yaml` 실파일 | — |
| B 기본값 시트 `verify_config` v1 실데이터 | — |
| `contracts/diagnosis.py`·`problem_generation.py` 초안 | ✅ 완료(`d5283d0`) |

### 2-7. `docs/05_request_json.md` 주석 수정 제안 `(C-03)`

learning_events의 `item_format` 주석 "R6·약점 지도가 **형식별로 분리 집계**"가 판정 축 포함으로 오독될 수 있다. 정정 제안: **"편중·약점 판정 축은 `area_tag × type_tag`, `item_format`은 분리 리포팅만"**(+ v1 데이터는 mcq 중심).

### 2-8. `docs/00_INDEX.md` 링크 추가 제안

'B 파트 상세 (`part_b/`)' 절 신설 — 01~09 문서 행 추가. 승인 후 반영.
§2-8 추가 제안: [`12_suneung_format_alignment.md`](12_suneung_format_alignment.md) 수능형 문항 포맷 적합성·확장 경계 행을 같은 절에 등록한다.

### 2-9. `contracts/execution.py` `VersionSet` B 확장 — ✅ **A+B 승인 완료 · `d5283d0` 구현 반영**

> **상태(7/22):** 아래 4필드 확장(+`RunMetadata`·`to_run_metadata` 동시 확장)은 A와 실시간 협의로 **승인 완료**됐고 커밋 `d5283d0`에 구현·테스트 반영됐다. `04_api_contract §2.2`·`06_erd AI_RUN` 문서 동기화도 완료됐다. 아래는 제안 당시 근거·시안의 이력 보존이다.

(제안 당시 배경) `VersionSet`은 6종 고정(`extra="forbid"`)이라 B 실행의 재현 키가 실릴 자리가 없다. `threshold_version`(detection 전용 nullable)과 같은 선례로 **B 전용 nullable 필드 확장**을 제안:

| 필드 | 의미 | null 조건 |
| --- | --- | --- |
| `graph_version` | curriculum_graph.yaml 버전 | 진단·출제 외 실행 |
| `taxonomy_version` | contracts/taxonomy 어휘 버전 | 〃 |
| `verify_config_version` | B 기본값 시트([`06`](06_quality_gates.md) 부록) | 〃 |
| `difficulty_calib_version` | 난이도 보정 버전 | 〃 |

- 근거: 불변식 8 — 이 버전들 없이는 과거 진단·검증 판정을 재현할 수 없다(threshold_version과 동일 논리 — 스키마는 합집합, 실행별 차이는 nullable로 흡수. §1-5 회신의 교집합 기각 사유와 동일).
- 대안(확장 부결 시): 산출물 행(WEAKNESS_MAP·PROBLEM_ITEM)에만 저장하고 meta.versions는 6종 유지 — 단 API 응답만으로 재현 키를 못 얻는 비대칭 발생.
- (제안 당시 절차 항목) 양자 승인 + `04_api_contract §2.2`·`06_erd AI_RUN` 동시 개정 대상 → ✅ **승인·구현·두 문서 동기화 완료**.

**제안 당시 코드 시안** (이력 보존 — `d5283d0`에 동일 내용 반영 완료 · `threshold_version` docstring 패턴 준용):

```python
# contracts/execution.py — VersionSet B 확장 [제안 · 양자 승인]
class VersionSet(BaseModel):
    ...
    graph_version: str | None = None
    """curriculum_graph.yaml 버전 — **diagnosis·problem_generation 실행에만 의미.**
    그래프 개정은 버전으로만 이뤄지므로(part_b/04 §6) 이 값 없이는 과거 약점
    판정을 재현할 수 없다. A capability 실행에서는 None."""

    taxonomy_version: str | None = None
    """어휘 버전 — 진단·출제 실행 외 None (태깅ⓒ 등 A 소비처 확장 시 재론)."""

    verify_config_version: str | None = None
    """B 판정 파라미터 시트 버전 (part_b/06 부록) — 출제 실행 외 None."""

    difficulty_calib_version: str | None = None
    """난이도 보정 버전 — 출제 실행 외 None."""
```

### 2-10. 승인 결과 공용 문서 동기화 실제 현황 `[7/22 재대조 — 3/7 완료, 4건 잔여]`

공용 계약 B 확장 14항목의 승인·구현(`d5283d0`)은 완료됐다. 7/22 원본을 다시 대조한 결과 아래 7건 중 3건은 이미 반영됐고 4건이 남아 있다.

| 문서 | 소유 | 요청 내용 | 실제 상태 |
| --- | --- | --- | --- |
| `docs/04_api_contract.md` §2.2 | 공용 | meta.versions = 공통 6종 + B nullable 4종 | ✅ 완료 — 10키 예시·정본 규칙 반영 |
| `docs/06_erd.md` | A | AI_RUN B 버전 4컬럼·capability 2종, GATE_RESULT B enum 반영 | ✅ 완료 — B 8테이블 통합은 §2-4 별도 |
| `docs/99_open_items.md` | 공용 | B-2 승인·구현 완료 상태 반영 | ☐ 잔여 — B-2가 아직 미완료 표기 |
| `docs/policies/error_codes.md` §2.2·§2.6·§1 | A | BlockedReason 3종·B 결과 어휘 5종·`REVISION_CONFLICT` 편입 | ✅ 완료 — 세 범주 분리·409 reason 공개 범위·멱등 선조회까지 정본 반영 |
| `docs/part_a/08_evaluation_plan.md` §1 | A | `golden/diagnosis/` 행 추가 | ☐ 잔여 |
| `docs/02_ownership.md` §5 | 공용 | `golden/diagnosis/` [염준영] 소유 행 추가 | ☐ 잔여 — `api/`·FakeSnapshot 소유 반영과는 별개 |
| `docs/00_INDEX.md` | 공용 | part_b 문서 9종 링크 절 신설 | ☐ 잔여 |

※ Kafka 완료 이벤트는 공통 `worker_job.*` 계약으로 확정됐다. B API 경로와 `result_ref` 조회 방식은 **백엔드 합의(D-10) 전 — 제안 유지**다.

### 2-11. `api/` 공통 계층 B 리뷰 — 구조 승인 완료·wire 크로스체크 잔여 `(99 ⑧·⑨)`

> **B 회신:** `app.py`·`envelope.py`는 공통 양자 승인, capability 라우터는 해당 오너 소유로 확정 승인했다(`02_ownership.md` §4·§5, 99 ⑧). 아래 wire 간극은 이번 독립 리뷰에서 새로 발견해 `04_api_contract.md`·`error_codes.md`의 관련 조항에 해결안과 확인 요청을 남겼다. 해당 정본 값은 바꾸지 않았다.

| 항목 | 원본 크로스체킹 위치 | 상태 |
| --- | --- | --- |
| 소유권 | `02_ownership.md` §4·§5 | ✅ B 승인·9곳 편입 완료 |
| 실패 envelope | `04_api_contract.md` §2.2 | ◐ detect 경로의 실패 `meta.versions` 구현·테스트 완료, 공용 `error_envelope`의 versions 필수화 또는 전 라우터 조립 보장 잔여(양자) |
| 요청 검증 | `04_api_contract.md` §2.3 · `error_codes.md` §1 | ◐ `INVALID_SCHEMA` 범위 확정·detect 구현 완료, 공용 재사용 handler 보장 잔여 |
| LLM 예외 매핑 | `04_api_contract.md` §2.3 · `error_codes.md` §4 | ◐ `runtime/errors.py` adapter 위치·정본 트리 확정, `contracts.llm` 실제 예외 연결·503/504 통합 테스트 잔여 |
| 민감 detail 강제 제거 | `04_api_contract.md` §2.3 · `error_codes.md` §4 | ☐ 공통 handler의 `RedactionUncertain.detail` 제거 보장·통합 테스트 확인 `[P0]` |
| tenant/경로 멱등 스코프·서버 digest·TTL·영속화 | `04_api_contract.md` §2.3 | ☐ D-② DB 교체 안건(99 ⑨)으로 이관 `[P0]` |
| tracing | `04_api_contract.md` §2.3 | ◐ `X-Request-Id` 응답 echo 완료, 로그 correlation 구현 근거·회귀 잔여 |
| B HTTP DTO | §2-1 | ✅ 외부 body DTO와 내부 command 분리 확정 |
| 동의 오류 경계(404/422) | `policies/error_codes.md` §1 | ◐ A 구분안 채택·정본 반영, 백엔드 보안 의도 확인 대기 |

### 2-12. GraphRAG 지식 계층 — 공용 계약 확장 `[제안 · 2026-07-27]` `(A-4·A-5)`

GraphRAG 채택은 확정이다([`10`](10_m2_problem_generation_architecture.md) §1·§4.2). 규격은 **[`11_graphrag_knowledge_layer.md`](11_graphrag_knowledge_layer.md)로 정본 편입 완료**(B 단독)이며, **아래 2건만 양자 승인 파일을 건드리므로 B 단독으로 확정하지 않는다.** A는 `11` 문서를 함께 보면 된다 — §0에 소유·승인 경계표가 있다.

#### 2-12-①. `contracts/execution.py` `VersionSet` — GraphRAG 3필드 `[A-4]`

| 필드 | 의미 | null 조건 |
| --- | --- | --- |
| `content_graph_version` | 콘텐츠·근거 그래프(Document·Chunk·Claim·Rule·License)의 스키마 버전 | GraphRAG를 경유하지 않는 실행 |
| `graph_index_version` | 색인 스냅숏 버전 — 같은 그래프라도 색인이 갱신되면 검색 결과가 달라진다 | 〃 |
| `retrieval_config_version` | 검색 파라미터(top-k·필터·재랭킹) 시트 버전 | 〃 |

- **근거:** 불변식 8(재현성). 이 셋이 없으면 "그때 그 ContextPack이 왜 그 근거를 골랐는가"를 재현할 수 없다. `threshold_version`·B 버전 4종(§2-9)과 **동일한 선례**다 — 스키마는 합집합, 실행별 차이는 nullable로 흡수.
- **⚠️ 이름 재사용 금지(핵심):** 기존 `VersionSet.graph_version`은 **교육과정 DAG 버전**이며 이미 A+B 승인·구현 완료(`d5283d0`, §2-9)다. 콘텐츠 GraphRAG 버전으로 **재사용하지 않는다.** GraphRAG 설계서도 이 충돌을 직접 경고한다.
- **A-4 조건 ① — 동시 개정 절차:** 필드 추가로 `meta.versions`는 **10 → 13키**가 된다. `contracts/execution.py`와 함께 `docs/04_api_contract.md` §2.2의 키 수·예시와 `docs/06_erd.md`의 `AI_RUN` 3컬럼을 **같은 PR에서 함께 개정**한다. 백엔드 통보는 A가 BE-1(v1.0 승격)과 묶어 수행한다.
- **A-4 조건 ② — 축 독립성:** `graph_index_version`과 `retrieval_config_version`은 실제로 독립적으로 움직인다. ① 색인을 재빌드하지 않고 top-k·필터 조건·재랭킹 on/off 같은 검색 파라미터만 바꿀 수 있고, ② 반대로 검색 파라미터는 유지한 채 승인 자료 추가나 라이선스 만료 자료 제외로 색인만 갱신할 수 있다. 두 경우 모두 같은 질문에서 다른 근거가 선택되지만 원인이 다르므로, 한 필드로 합치면 재현 실패 원인을 분해할 수 없다(불변식 8).
- **확장 부결 시 대안:** 산출물 행에만 저장하고 `meta.versions`는 유지 — 단 API 응답만으로 재현 키를 못 얻는 비대칭이 §2-9과 동일하게 발생한다.

#### 2-12-②. `evidence/` resolver 주입 시그니처 — B-7 안건과 병합 `[A-5]`

`evidence/`는 A 소유다. GraphRAG 도입으로 근거 해소 경로가 "앵커 → 원문 직접 조회"에서 "앵커 → `EvidencePack`(path·quote·license·hash) 대조"로 확장되므로, **기존 B-7(evidence resolver 주입 시그니처, A 초안 리뷰)과 하나로 묶어 확정**할 것을 제안한다.

resolver가 만족해야 하는 조건(B 요구):

1. **fail-closed** — 해소 실패·검색 0건에 대해 빈 결과를 반환하지 않고 실패를 신호한다. 상위에서 `verification_unavailable`로 수렴시킨다([`06`](06_quality_gates.md) §3).
2. **권리 게이트** — `rights_status != approved` 자료는 해소 대상에서 제외한다.
3. **결정론** — 같은 `(EvidencePack, anchor)` 입력에 같은 결과. 시계·난수 주입 금지.
4. **예산 불변** — resolver 내부에 별도 재시도 루프를 두지 않는다. 문항당 `item_attempt` 총 3회를 그대로 소모한다(FIX-06).
5. **blind 무오염** — resolver 반환값이 verifier 페이로드로 흘러들지 않는다([`05`](05_problem_generation.md) §4.3).

#### 2-12-③. B 단독으로 진행하는 부분 (A 승인 불요 — 참고)

`docs/part_b/` GraphRAG 규격 문서 신설, [`05`](05_problem_generation.md) §4.2의 `EvidenceAnchor`↔`EvidencePack` 대응, [`06`](06_quality_gates.md) §1의 R-1·R-4 확장, [`07`](07_refine_policy.md) §4의 `ResolveRevisionContext` 선행, 자료 권리 매니페스트 스키마, 검색 모드(`reuse_only`·`delta_retrieve`) 규약.

### 2-13. `type_tag` 어휘 확장 `[제안 · A+B 양자 승인]` `(W9)`

**근거.** 2027학년도 6월 모의평가 국어 45문항(공통 34문항 + 선택 11문항)을 현행
4종(`fact`·`infer`·`critic`·`concept`)으로 분류하면 약 18문항만 무리 없이
분류된다. 다음 문항군은 현재 어휘로 측정 능력을 정직하게 표현할 수 없다.

- 어휘 문항 9·17: 매회 고정 2문항으로 출제되지만 현행 4종 어디에도 속하지 않는다.
- 글의 전개 방식·공통점 문항 4·10·22.
- 문학 표현·서술 방식 문항 19·20·26·27·32.
- `<보기>` 외적 준거 적용·감상 문항 3·8·13·16·21·24·31·34:
  `infer`로 밀어 넣으면 강사가 이를 “추론 약점”으로 오독한다. 외적 준거 적용은
  추론과 구별해 진단해야 하는 능력이다.
- 화법과작문 11문항 전체와 매체 6문항 전체.

**제안.** 어휘 의미, 글의 조직·표현 방식, 외적 준거 적용·감상, 화법·작문·매체
수행을 서로 구별해 진단할 수 있도록 `type_tag`의 표현 범위를 확장한다. 이 절은
필요한 능력 범주와 확장 방향만 제안하며, **신규 enum 식별자와 최종 개수는
확정하지 않는다.** `contracts/taxonomy.py`는 양자 승인 파일이므로 실제 어휘와
경계 사례는 A+B가 태깅 골든셋과 함께 확정한다.

**승인 시 동시 개정 대상.** `docs/policies/taxonomy.md`의 어휘·경계 사례,
`area_tag × type_tag` 진단 셀 구조, 약점 지도 API 응답을 같은 변경 단위로
개정한다. 이 제안에서는 해당 파일을 직접 수정하지 않는다.

**부결 시 대안.** 셀을 현행 6×4로 유지하고 `skill_node`만 세분한다. 계약 변경은
피할 수 있지만, 4종에 매핑할 수 없는 어휘·화법·작문·매체 약점은 셀 진단으로
표현할 수 없어 **진단 자체가 불가능**해지는 비용을 감수해야 한다.

## §3. OPEN 총괄 표 (잔여만 — 해소분은 §0)

| 번호 | 항목 | B 권고안 | 담당 | 관련 part_b |
| --- | --- | --- | --- | --- |
| B-3 잔여 | taxonomy 경계 사례 7건 판정 | 태깅 골든셋 시드와 동시 확정 | A+B | 04·06 §5 |
| Open-12 | F17 OCR 실명→alias·OCR 소유 (P2) | 스캔·매칭·마스킹=BE 유지, 판독 소유는 벤더 선정과 함께 | BE(+A·B) | 02 §1-C |
| B-2 | 공용 계약 리뷰·구현·14항목 승인 완료 — **문서 동기화 3/7 완료, 4건 잔여(§2-10)** | 잔여 4건 소유자 반영 요청 | A+B | 02 §5 |
| B-5 / D-06 | ✅ PR #15로 로컬 OpenAI 호환·Gemma 계열 공급자와 어댑터 확정 — verifier 폴백 패밀리만 잔여. **B-5 정리 PR 할 일 2건** `[2026-07-28]`: ① `gateway.py`의 `TODO(B-5)` 제거(provider 1개일 때 패밀리 강제가 우회되는 현행 동작) ② **`problem_generation/provider.py` 조립부 가드 + `test_provider.py`**(§1-10 "가드 위치") | 폴백 패밀리 확보 후 generator/verifier 패밀리 분리 강제. 두 항목 모두 role 키 설정 구조를 공유하므로 같은 PR에서 처리 | A+B | 06 §2·§3 · §1-10 |
| B-7 | evidence resolver 주입 시그니처 — **GraphRAG 경유 근거 해소와 병합**(§2-12-②) | fail-closed·권리 게이트·결정론·예산 불변·blind 무오염 5조건 | A+B | §2-12 · `10` §4.2 |
| **B-8ⓑ** | **GraphRAG `VersionSet` 3필드 확장** — `content_graph_version`·`graph_index_version`·`retrieval_config_version`. **`graph_version` 재사용 금지** | B-8ⓐ capability별 validator와 병합. ⓐ·ⓑ 모두 `contracts/execution.py`(양자) + `04_api_contract.md` §2.2 + `06_erd.md` AI_RUN 동시 개정이 필요해 PR 단위가 같다. A-5→B-7 병합과 대칭 | A+B | §2-12-① · `10` §4.2 |
| **B-9** `[신규]` | 난이도 사유 재생성 시 **이전 검증본 보존 규칙** — 검증 통과 문항이 미검증 문항으로 대체될 수 있는 미정의 동작 | `07` §4의 "마지막 검증본 유지"를 생성 경로에 대칭 적용 제안. 확정 전 `difficulty_regen_enabled=false` 유지 | B 초안 → A+B | `10` §4.1 C3 |
| **BE-11** `(구 B-10)` | ✅ **A 판정 완료 — RLS 구현 부재는 문서 표현을 앱 계층 격리로 정정해 해소.** RLS 실도입 여부는 백엔드 합의 안건으로 이관 | 도입 시 `db/session.py`·`db/store_factory.py` 연결·역할 설계와 함께 기존 26+B 8테이블에 일괄 적용. 현재 B 8테이블은 기존 패턴 준수 | **BE** | §2-4.5 |
| **W1** `[신규]` | **다중 목표·다중 measured area 세트** — M2 와이어프레임 Step 1은 셀 여러 개를 담고 개수를 각각 지정하나, `05` §4.1은 **v1 단일 영역 제한** | 요청 분할 vs 요청 형식 확장 중 택일. 협업설명서도 "회의 결정 필요"로 등재 | A+B+제품 | `05` §4.1 · `10` §6 |
| **W2** `[신규]` | 화면이 **셀에 `suspect`를 표시**하나 `04` §4의 셀 verdict는 `unknown\|weak\|ok` 3종이고 `suspect`는 **노드** verdict | 셀 verdict 확장 vs 화면이 노드 verdict를 셀에 투영 중 택일 | B(+FE) | `04` §4·§5.1 |
| **W3** `[신규]` | 완료 알림 payload — 화면 문서는 수량(통과·검토·폐기)을 알림에 싣고, §2-1은 `result_ref` 조회로 얻는다 | §2-1 유지 권고(알림 경량화) | BE+B | §2-1 |
| **W4** `[신규]` | 문항 상세 응답에 **`available_actions`·`current_revision_no`** 포함 요구(협업설명서) | B 단독 신설 가능 — 계약 확정 후 FE 통보 | B(+FE) | `07` §1 |
| **W5** `[신규]` | `needs_review` 문항의 **"강사 확인 완료로 표시"** 액션이 `ItemAction` 5종에 없음 | 확인 기록 소유가 BE일 가능성 — AI는 발행 플래그 미반환 원칙 유지(U14) | BE+제품+B | `03` U14 · `07` §1 |
| **W6** `[신규]` | **반 단위 출제** — 화면에 반 카드가 있으나 `DiagnosisInput.student_ref`는 단수, 반 집계 진단 규격 없음 | 구성원 기준시점·집계·alias 입력 규격 선결 | BE+제품+B | `04` §3.1 · `10` §6 |
| **W7** `[신규]` | **교육과정 개정판** — 2026년에 고2·고3 동시 지원 시 2022 개정·2015 개정 정본 2벌 필요 | 지원 학년 확정 선결. `curriculum_graph.yaml` 작성 범위가 갈림 | 기획+B | `04` §2 · `10` §7 |
| **W8** `[신규]` | **M2 수능형 포맷 적합성 총괄** — 상세는 [`12`](12_suneung_format_alignment.md) FMT-1~11 | 개별 FMT 안건을 이 표에 중복하지 않고 `12` 정본에서 오너별로 추적 | B(+각 오너) | `12` §5 |
| **W9** `[신규]` | **`type_tag` 4종이 실제 문항 유형의 절반을 못 담음** | 능력 범주 확장 방향을 §2-13에서 제안하고 실제 어휘·경계는 양자 승인 | A+B | §2-13 · `12` §3 |
| **W10** `[신규]` | **롤백 재검증이 FIX-09와 충돌** — `07` 정정 | 롤백 본문 복원 후 게이트 ①②③ 전체 재검증으로 정합화 | B 단독 | `07` §1 · `10` §3 FIX-09 |
| D-03 | T1 기준 자료(공급처·버전·라이선스) | 버전·라이선스 명확한 자료만, 장애 시 발행 차단 | B+기획 | 05 §1.1 |
| D-04 / C-14 `[P0]` | T2 사실성 보장 수단 | 승인 자료 기반+source_ref, 수단 없으면 `source_unverified` 차단 | B+기획 | 05 §2.1 |
| C-15 `[P0]` | 외부 표절·유사도 + injection 코퍼스 | 05 §8.2·§8.3, 08 §8 예약 | B+기획 | 05·08 |
| D-09 | PDF Phase·소유자 | B 조판 P2 확정 표기, MVP 단순 내보내기는 별도 결정 | 기획+BE+FE+B | 05 §9 |
| D-10 | B API·Kafka 이벤트 편입(약점 지도 API 형태 포함) | §2-1 초안으로 BE 리뷰 | BE+B | 02 §1 |
| — | 승인된 문항 재수정 시 승인 철회·재검수 절차 | 철회 전 수정 거부 `[제안]` | BE | 07 §8 |
| — | 리비전 보존 기간(개수 무제한 `[잠정]`) | BE 보존 정책과 함께 | BE+B | 07 §6 |
| — | 지문 수정 허용·공유 문항 연쇄 재검증 | v1 비허용 `[잠정]` | B+기획 | 07 §7 |
| — | refine 쿼터 차감 단위(백엔드 집행 설계) | 정적 차단 턴 미차감 권장 | BE | 07 §5 |
| — | 문학 풀 초기 등재 규모·라이선스 | — | BE+기획 | 05 §3 |
| — | 프론트 라벨 문구·대화 UI 형태 | §2-2 라벨 사전 기준 | FE+기획 | 06 §0 |
| Open-9 잔여 | 전국 백분위 출처(A 소관 — 참고) | — | BE+A | — |
| 7/22 감지 리뷰 | 기존 크로스체크 A 판정·구현 완료 · ongoing 상한 제외 후 요약 동기화·병합 lifecycle·회귀/데모 잔여 | 원본의 신규 `[PART_B 크로스체킹 요청]` 검토 후 A·BE 회신 | A+BE(+B 리뷰) | §1-6·§1-7·§1-8 |
| 7/22 API 리뷰 | `api/` 소유 ✅ · 실패 meta/검증/tracing 일부 반영 · VersionSet 양자 협의·LLM adapter·민감 detail·D-② 멱등·ongoing 응답 의미 잔여 | 원본의 `[PART_B 크로스체킹 요청]` 검토 후 공통 wire 확정 | A+B+BE | §1-7·§1-8·§2-11 |
| 7/22 B HTTP 경계 | 공통 헤더와 내부 command의 중복 | ✅ 외부 HTTP DTO와 내부 command 분리 확정 | B(+A·BE 편입 리뷰) | 05 §4.1 · 07 · §2-1·§2-11 |
