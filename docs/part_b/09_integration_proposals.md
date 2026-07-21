# [체크온] B 통합 제안서 v1 — A 변경 요청 · 공용 문서/계약 변경 제안 · OPEN 총괄

> **지위:** member-B(염준영)의 공식 통합 제안과 승인 이력. `[제안]` 항목은 오너 승인 전까지 확정되지 않으며, `✅ A+B 승인 완료`로 표시된 항목은 승인된 결정 기록이다. A 소유 문서·공용 계약·정책·ERD 변경은 `docs/02_ownership.md` 절차를 따르며, B는 `docs/part_b/` 외 파일을 직접 수정하지 않는다.
>
> **변경 이력**
> - v1.2 (2026-07-15): **공용 계약 B 확장 14항목 A+B 승인 완료 반영** — §2-3·§2-9를 승인·구현 완료(커밋 `d5283d0`)로 전환(제안 이력 보존), §2-10(공용 문서 동기화 요청 일람) 신설, §3 B-2 갱신.
> - v1.1 (2026-07-15): 파일 번호 이동(06→09) + 7/15 결정 정리 — 해소 안건 분리(§0), Kafka 이벤트·409 충돌 코드·라벨 사전 제안 추가, B-1 회신 갱신, 쿼터 제안 폐기 반영. part_b 재편(01~09)에 따른 참조 갱신.
> - v1 (2026-07-15): part_b 정리 과정에서 도출된 요청·제안 일괄 등록.

---

## §0. 7/15 회의로 해소된 안건 (기록 — 재론 불요)

| 안건 | 결과 | part_b 반영 |
| --- | --- | --- |
| Open-11 / B-3 / D-01 | ✅ 6영역 채택 · **v1 item_format = mcq만**(short·essay 예약) | 05 §4·§9, 08 코퍼스 — 경계 사례 7건 판정만 잔여(§3) |
| B-1 | ✅ 슈퍼바이저 1 + 워커 3(문제 생성 = B 워커) — checkpointer·recorder 재사용 승인 | 01 §0·§6 — 반영 PR·슈퍼바이저 state 리뷰 잔여(§1-2) |
| B-4 / D-08 | ❌ **폐기** — v1 서술형 없음. F17 OCR 소유만 Open-12와 P2 재론 | 05 §9 예약 |
| BE-4 / D-05 | ✅ 쿼터 전부 백엔드 — **AI는 쿼터 무관, meta.quota 폐기** | 01 §5, 05 §4.4, 07 §5 |
| Open-2 | ✅ 비동기 완료 통지 = **Kafka** | 02 §1-A — 이벤트 증분 제안(§2-1) |
| B-6 / D-07 | ✅ LangSmith 공통 1개 도입(마스킹 훅 게이트웨이 앞단) | 01 §5 |
| D-02 | ✅ (대화 확정) 검증 차단 문항 저장 가능·발행 차단·**수동 예외 승인 불허** | 06 §3 |
| 타겟 협소화 | ✅ 수능 고등 기준 — v1 중등 분기 금지 | 04 §2 |
| (7/15 후속) contracts 공용 5파일 | ✅ A가 구현 완료(`feat/contracts-base` — taxonomy·execution·llm·gates·evaluation + 테스트 74종) | B 회신 §1-5 · (당시) 확장 제안 §2-3·§2-9 → 아래 행에서 승인 완료 |
| (7/15 오프라인) **공용 계약 B 확장 14항목** | ✅ **A+B 승인 완료** — A와 실시간 협의로 승인, 커밋 `d5283d0` 구현 반영(Capability 2 · VersionSet 4 · GateName 3 · OwnerKind 1 · BlockedReason 3 · GoldenSuite 1) | §2-3·§2-9(승인 완료) — 잔여: 공용 문서 동기화 요청 §2-10 |

## §1. A 소유 문서·코드 변경 요청 (승인 주체: 박진희)

### 1-1. part_a의 OCR=A 표현 정정 요청 — Open-12 `(C-02)`

- 대상: `part_a/01_pipeline.md` F17 절("OCR 답안 추출(A: import 확장)", "OCR·수집·learning_event 변환 = A") 및 `part_a/02_design.md` 동일 표현.
- 근거: `docs/policies/f17_paper_exam.md` §5·`99_open_items` Open-12는 OCR 판독 소유를 **미정**으로 둔다(B-4 폐기 후 Open-12로 흡수). 공용 정책 우선 — "미정(Open-12·P2)"으로 정정 요청.
- 확정 분담(유지): 스캔 수신·실명 매칭·이미지 마스킹=백엔드.

### 1-2. `agents/` 반영 PR + 슈퍼바이저 확인 — B-1 후속

- B-1 승인(7/15)에 따른 실행 항목: ① `AGENT_RUN.agent_kind`에 `problem_gen` 추가 PR(A 승인) ② 슈퍼바이저 state 초안(`policies/langgraph_state.md` §5) **B 리뷰 회신 완료** — [`01_pipeline.md`](01_pipeline.md) §6(잡 단위=세트 1건 · 인터랙티브 선점 `[제안]` · 슈퍼바이저 `agents/`(A) 소유 동의+라우팅 테이블 양자).
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

## §2. 공용 문서·계약 변경 제안 (승인 주체: 02_ownership 절차)

### 2-1. API·Kafka 계약 증분 `[제안]` — D-10·BE-1 `(C-05)`

현재 정본 계약에 B 엔드포인트가 없다. 아래는 **경로 확정이 아닌 증분 초안** — 공통 규약(envelope·헤더·멱등·202) 준수, 완료 통지는 Kafka(7/15).

| 항목 | 내용 |
| --- | --- |
| `POST /problem-sets` `[가칭]` | 202 + job_id. Request = `ProblemRequest`([`05`](05_problem_generation.md) §4.1 — `target_source` 포함). 멱등: Idempotency-Key |
| `GET /problem-sets/{job_id}` `[가칭]` | **디버그·복구 보조**(폴링 아님) — 상태·진행률·`ProblemSetResult` |
| `POST /problem-sets/{set}/items/{item}/refine` `[가칭 · MVP]` | `instruction`+`base_revision_no` — [`07_refine_policy.md`](07_refine_policy.md). 롤백 `revert_to` |
| **Kafka 이벤트 증분** | `docs/08_kafka_events.md` §4에 `problem_set.completed` / `problem_set.failed` `{ job_id, status, verified/review/dropped 수, stop_reason }` 추가 — 문항 본문 미포함(ID 참조만, 기존 규약 동일) |
| 약점 지도 조회 API | **상세 제안 보류** — 세트 응답 동봉 vs 별도 조회 vs 백엔드 사본 동기화는 `OPEN`(D-10, BE+B) |

### 2-2. 상태·에러 코드 사전 증보 제안 — `docs/policies/error_codes.md` `(C-10)`

**'정상 상태'(200 + status) 5종 추가:**

| 코드 | 뜻 | 근거 |
| --- | --- | --- |
| `partial_success` | 세트 일부 폐기 — 완료분 유효 + stop_reason·사유 보고 | 06 §6 |
| `needs_review` | 게이트 통과 + 검토 필수 배지 — 승인 전 발행 불가 | 06 §5 |
| `verification_unavailable` | 검증 불능 — 저장 가능·발행 차단·재검증 필수·**수동 우회 불가** | 06 §3 |
| `generation_exhausted` | 재생성 상한(총 3회) 소진 폐기 — drop_reason | 06 §6 |
| `source_unverified` | 사실검증 수단 부재 지문 — 발행 차단 | 05 §2.1 |

**HTTP 충돌 코드 1종 추가:** `409 REVISION_CONFLICT` — refine의 `base_revision_no`가 최신과 불일치(낙관적 잠금). 기존 `409 IDEMPOTENT_REPLAY`(동일 멱등키 재생)와 의미가 다르므로 별도 코드 필요.

**refine `blocked_reason` B 증분 3종:** `answer_integrity` · `banned_topic` · `prompt_injection`([`07`](07_refine_policy.md) §3 — `pii_exposure`·`out_of_scope`는 A enum 재사용).

**프론트 표시 라벨 사전 `[제안 — FE 확정]`:** 내부 상태→한국어 라벨 매핑([`06`](06_quality_gates.md) §0 표) — 검증 통과/검토 필수/검증 차단/생성 실패. 색상만으로 구분 금지, `검토 필수`와 `검증 차단`은 다른 아이콘·문구.

**참고(B 무관 공용 불일치 — A 확인 요청):** `04_api_contract.md` §2.3과 `error_codes.md` §1의 코드명 불일치 — `IDEMPOTENT_REPLAY`↔`IDEMPOTENCY_CONFLICT`(의미도 상이), `VALIDATION_FAILED`↔`INVALID_SCHEMA`, `LLM_UNAVAILABLE`↔`LLM_UPSTREAM_DOWN`. B 엔드포인트 편입 전 통일 필요.

### 2-3. 공용 enum·계약 확장 — ✅ **A+B 승인 완료 · `d5283d0` 구현 반영** `(C-07·C-09)`

> **상태(7/15):** 아래 확장 중 EVIDENCE_ITEM 행을 제외한 전부가 A와 실시간 협의로 **승인 완료**됐고 커밋 `d5283d0`에 구현·테스트 반영됐다. `GoldenSuite.diagnosis`(`contracts/evaluation.py`)도 같은 승인에 포함. 잔여는 공용 문서 동기화 요청(§2-10)뿐. 아래 표·시안은 제안 당시 이력으로 보존한다.

| 대상 (코드 + ERD) | 변경 | 절차 |
| --- | --- | --- |
| `contracts/execution.py` `Capability` | `diagnosis`·`problem_generation` 추가 — 현 enum은 A 3종뿐(docstring이 "B 테이블 증분 시 함께 확장" 명시) | ✅ 승인·구현 완료(`d5283d0`) |
| `contracts/gates.py` `GateName` + `GATE_RESULT.gate_name` | `RuleValidation \| BlindCrossSolve \| ReleaseDecision` 추가 | ✅ 승인·구현 완료(`d5283d0`) |
| `contracts/gates.py` `OwnerKind` + `GATE_RESULT.owner_kind` | `problem_set` 추가 | ✅ 승인·구현 완료(`d5283d0`) |
| `contracts/gates.py` `BlockedReason` | `answer_integrity`·`banned_topic`·`prompt_injection` 추가([`07`](07_refine_policy.md) §3) | ✅ 승인·구현 완료(`d5283d0`) — error_codes §2.2 동기화는 §2-10 요청 |
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

B 소유 7테이블(WEAKNESS_MAP·PASSAGE·PROBLEM_SET·PROBLEM_ITEM·VERIFICATION_RESULT·ITEM_REVISION·DIFFICULTY_CALIB)을 [`02_design.md`](02_design.md) §2 기준으로 24→31테이블 통합. 통합 전 정본은 "A 24테이블 + part_b 증분".

### 2-5. `docs/02_ownership.md` §5 트리 증보 제안

`evaluation/golden/problems/` 하위 7종([`08`](08_evaluation_plan.md) §1)·`golden/diagnosis/` 신설 행(전부 [염준영]). `pg_banned_topics.yaml`을 B 데이터 파일 규칙(§4-5 — 골든 통과가 머지 조건)에 추가.

### 2-6. `docs/99_open_items.md` 증분 제안 — B 산출물 등록

| 항목 | 선결 |
| --- | --- |
| 문법 DAG 실제 YAML 25~40노드 (`curriculum_graph.yaml`) | B-3 잔여(경계 사례) |
| `golden/problems/` 코퍼스 실파일(§2 18건·§3 11건·§4 10건·§7·§8·§9 RF1~10) | — |
| `golden/diagnosis/` 회귀 실파일 | — |
| `t1_reference/` 케이스 | D-03 |
| `pg_banned_topics.yaml` 실파일 | — |
| B 기본값 시트 `verify_config` v1 실데이터 | — |
| `contracts/diagnosis.py`·`problem_generation.py` 초안 | ✅ 완료(`d5283d0`) |

### 2-7. `docs/05_request_json.md` 주석 수정 제안 `(C-03)`

learning_events의 `item_format` 주석 "R6·약점 지도가 **형식별로 분리 집계**"가 판정 축 포함으로 오독될 수 있다. 정정 제안: **"편중·약점 판정 축은 `area_tag × type_tag`, `item_format`은 분리 리포팅만"**(+ v1 데이터는 mcq 중심).

### 2-8. `docs/00_INDEX.md` 링크 추가 제안

'B 파트 상세 (`part_b/`)' 절 신설 — 01~09 문서 행 추가. 승인 후 반영.

### 2-9. `contracts/execution.py` `VersionSet` B 확장 — ✅ **A+B 승인 완료 · `d5283d0` 구현 반영**

> **상태(7/15):** 아래 4필드 확장(+`RunMetadata`·`to_run_metadata` 동시 확장)은 A와 실시간 협의로 **승인 완료**됐고 커밋 `d5283d0`에 구현·테스트 반영됐다. 잔여는 `04_api_contract §2.2`·`06_erd AI_RUN` 문서 동기화 요청(§2-10)뿐. 아래는 제안 당시 근거·시안의 이력 보존이다.

(제안 당시 배경) `VersionSet`은 6종 고정(`extra="forbid"`)이라 B 실행의 재현 키가 실릴 자리가 없다. `threshold_version`(detection 전용 nullable)과 같은 선례로 **B 전용 nullable 필드 확장**을 제안:

| 필드 | 의미 | null 조건 |
| --- | --- | --- |
| `graph_version` | curriculum_graph.yaml 버전 | 진단·출제 외 실행 |
| `taxonomy_version` | contracts/taxonomy 어휘 버전 | 〃 |
| `verify_config_version` | B 기본값 시트([`06`](06_quality_gates.md) 부록) | 〃 |
| `difficulty_calib_version` | 난이도 보정 버전 | 〃 |

- 근거: 불변식 8 — 이 버전들 없이는 과거 진단·검증 판정을 재현할 수 없다(threshold_version과 동일 논리 — 스키마는 합집합, 실행별 차이는 nullable로 흡수. §1-5 회신의 교집합 기각 사유와 동일).
- 대안(확장 부결 시): 산출물 행(WEAKNESS_MAP·PROBLEM_ITEM)에만 저장하고 meta.versions는 6종 유지 — 단 API 응답만으로 재현 키를 못 얻는 비대칭 발생.
- (제안 당시 절차 항목) 양자 승인 + `04_api_contract §2.2`·`06_erd AI_RUN` 동시 개정 대상 → **승인·구현은 완료**, 문서 동기화만 §2-10 요청으로 잔존.

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

### 2-10. 승인 결과 공용 문서 동기화 요청 일람 `[신규 — 각 문서 소유자 반영 요청]`

공용 계약 B 확장 14항목의 승인·구현(`d5283d0`)은 완료됐으나 아래 공용·A 소유 문서가 이전 상태로 남아 있다. B는 직접 수정하지 않는다 — 문서 소유자 반영 요청:

| 문서 | 소유 | 요청 내용 |
| --- | --- | --- |
| `docs/04_api_contract.md` §2.2 | 공용 | meta.versions "6종" → **공통 6종 + B nullable 4종**(graph·taxonomy·verify_config·difficulty_calib) |
| `docs/06_erd.md` | A | AI_RUN에 B 버전 4컬럼 · capability 값 `diagnosis`·`problem_generation` · GATE_RESULT `gate_name` 3종·`owner_kind` `problem_set` 반영 (B 7테이블 통합은 §2-4 별도) |
| `docs/99_open_items.md` | 공용 | B-2 승인·구현 완료 상태 반영(7/15 결정 로그 증분) |
| `docs/policies/error_codes.md` §2.2 | A | BlockedReason 3종(`answer_integrity`·`banned_topic`·`prompt_injection`) 편입 + B 상태 코드 5종은 §2-2 제안 유지 |
| `docs/part_a/08_evaluation_plan.md` §1 | A | `golden/diagnosis/` 행 추가 — `GoldenSuite.diagnosis` 승인 반영(§1-4와 동일 요청) |
| `docs/02_ownership.md` §5 | 공용 | `golden/diagnosis/` [염준영] 소유 행 추가(§2-5와 동일 요청) |
| `docs/00_INDEX.md` | 공용 | part_b 문서 9종 링크 절 신설(§2-8과 동일 요청) |

※ Kafka `problem_set.completed`·`problem_set.failed` 이벤트와 B API 경로(§2-1)는 **백엔드 합의(D-10) 전 — 제안 유지, 이번 승인 범위가 아니다.**

## §3. OPEN 총괄 표 (잔여만 — 해소분은 §0)

| 번호 | 항목 | B 권고안 | 담당 | 관련 part_b |
| --- | --- | --- | --- | --- |
| B-3 잔여 | taxonomy 경계 사례 7건 판정 | 태깅 골든셋 시드와 동시 확정 | A+B | 04·06 §5 |
| Open-12 | F17 OCR 실명→alias·OCR 소유 (P2) | 스캔·매칭·마스킹=BE 유지, 판독 소유는 벤더 선정과 함께 | BE(+A·B) | 02 §1-C |
| B-2 | 공용 계약 리뷰 — 공용 5파일 구현 ✅(§0) · execution.py 신규 필드 승인 ✅(§1-5) · **Capability·VersionSet 확장 ✅ 승인·구현 완료(`d5283d0` — §2-3·§2-9)** · **B 소유 2파일(diagnosis·problem_generation) 초안 ✅ 구현 완료(`d5283d0`)** — **잔여: 공용 문서 동기화(§2-10)** | §2-10 요청 발송 | A+B | 02 §5 |
| B-5 / D-06 | LLM 공급자·모델 패밀리 조합 + verifier 폴백 | generator/verifier 패밀리 분리, 폴백 패밀리 지정 | A+B | 06 §2·§3 |
| B-7 | evidence resolver 주입 시그니처 | A 초안 리뷰 | A+B | — |
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
