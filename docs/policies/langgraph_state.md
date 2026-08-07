# [체크온] LangGraph 상태 스키마 명세 v1 — 워커 3종 + 결정론 슈퍼바이저

> **지위: B-1 승인(7/15) · 슈퍼바이저 실행 계약 확정(7/27) — 구현 착수 가능.** 구조는 **슈퍼바이저 1 + 워커 에이전트 3** — ① counsel_pack(상담팩, A) ② mapping_probe(매핑 조사, A) ③ problem_generation(문제 생성, B). 세 워커의 체크포인트 state와 슈퍼바이저 실행 경계는 본 문서를 정본으로 따른다.
> **정본 분리:** 워커 그래프 체크포인트의 정본은 LangGraph PostgresSaver, 큐·lease·실행 phase의 정본은 `WorkerJob` 계약을 영속 투영한 AI PostgreSQL `AGENT_RUN` 행이다. `AGENT_RUN`에는 Job 원장과 최신 체크포인트 참조·진행률만 두며 중앙 `SupervisorState`는 두지 않는다.

---

## 1. counsel_pack — 상담팩 오케스트레이터

### 1.1 그래프 (파이프라인 v2 확정 흐름)

```
plan → [학생 루프: assemble_context → generate_draft → gate_check → record] → summarize
                              ↑ gate 실패 시 블록 재시도(≤3) · 학생 단위 실패는 스킵하고 계속
```

### 1.2 State (Pydantic)

상담팩 워커는 반의 학생을 순차 처리하므로 재개 지점이 학생 경계다. 체크포인트에는 학생 컨텍스트 본문·초안 본문을 복제하지 않고, 불변 입력 참조와 완료 결과 포인터만 둔다. 학생별 컨텍스트는 `context_ref`를 역참조해 그때그때 읽고, 생성된 초안은 `results[].draft_id`로만 가리킨다. `WorkerJob.phase`·lease·`result_ref`는 슈퍼바이저 정본이므로 이 state에 넣지 않는다.[^cp-body]

[^cp-body]: state는 PostgresSaver 체크포인트와 LangSmith 노드 트레이스 두 경로로 나간다. 후자의 훅 위치는 `part_b/09_integration_proposals.md` §2-16(B 제안 진행 중).

```python
class StudentResult(BaseModel):
    student_ref: str
    draft_id: UUID | None          # 성공 시
    status: Literal["done", "skipped_insufficient", "failed"]
    fail_reason: str | None

class CounselPackState(BaseModel):
    # 불변 입력 (기동 시 고정 — 재개해도 안 바뀜)
    tenant_id: str
    class_ref: str
    student_refs: list[str]                    # 처리 순서 고정 (재현성)
    context_ref: str                           # 학생 컨텍스트 묶음의 저장소 참조 — 본문 미복제
    context_hash: str                          # sha256:<64 lowercase hex> — 재개 시 대조
    plan_version: str

    # plan 노드 산출 (LLM 1회 — 확정 수치 내 강조점만, 새 사실 생성 금지)
    emphasis_points: dict[str, list[str]]      # student_ref → 강조점(근거 record_id 필수)
    plan_outcome: PlanOutcome = "ok"           # 강조점 0건의 **이유** — 사유 코드만(본문 금지)
    plan_dropped: int = 0                      # 근거 실존 검증에서 드롭된 강조점 수

    # 진행 상태 (체크포인트 대상)
    cursor: int = 0                            # student_refs 인덱스 — 재개 지점
    results: list[StudentResult] = []
    quota_consumed: int = 0                    # 미터링 리포트용 (차단은 백엔드)

    # 종료 산출
    summary: str | None = None                 # "22명 중 19명 생성·2명 데이터 부족·1명 실패"
```

> **트레이스 노출 실측(2026-07-30):** 이 §1.2의 본문 미복제 결정이 트레이스 노출을 실제로 줄인다 — LangSmith span에 `prompt`·`facts`·`fallback_text`가 **등재되지 않았다**. 단 `emphasis_points`는 근거 라벨·수치·`record_id`가 문면 그대로 실린다(P2 1순위). 실측: `part_a/11_langsmith_trace_probe.md`.

> **`plan_outcome`·`plan_dropped`의 단위와 근거(2026-08-08 확정 · 99 ㉲):** 초안에 강조점이 0건으로 나가는 이유가 **다섯 가지**인데 산출물만 보면 다섯이 같았다 — ⓐ plan LLM 호출 실패 ⓑ 응답은 왔는데 **파싱 0건** ⓒ 파싱은 됐는데 **근거 실존 검증에서 전량 드롭** ⓓ 진짜로 강조할 게 없었다 ⓔ 🔴 **plan 프롬프트의 마스킹이 불확실해 전송을 안 했다**(8/7 추가 · 99 #05). 종전에 남는 것은 `logger.info` 두 줄뿐이라 *"강조점이 왜 없지"* 를 물었을 때 볼 것이 없었다.
>
> | 값 | 뜻 | 대응 |
> | --- | --- | --- |
> | `ok` | 정상 — 강조점이 0건이면 **진짜로 없었던 것**이다 | 없음 |
> | `llm_failed` | plan 호출이 **전송 오류**로 실패(`LlmTimeout`·`LlmUnavailable`) | 업스트림·게이트웨이 확인 |
> | `redaction_blocked` | 🔴 **마스킹 불확실 — 전송 안 함**(fail-closed · 불변식 3) | **BE가 보낸 컨텍스트**를 본다. 업스트림이 아니다 |
> | `unparsed` | 응답은 왔는데 파싱 결과가 0건 | **프롬프트 형식 준수** 문제 |
> | `all_dropped` | 파싱분이 근거 실존 검증에서 전량 드롭 | 날조·`record_id` 누락 |
>
> ⚠ **`redaction_blocked`의 파급은 학생 단위보다 크다** — `assemble_plan_prompt(contexts, student_refs)`가 **잡의 모든 학생 컨텍스트를 한 프롬프트에** 담으므로 한 학생의 fact 하나가 불확실해도 **잡 전체가 무강조**다(student 노드는 그 학생만 실패한다). 값 문자열을 student의 `fail_reason="redaction_blocked"`와 **같게** 둔 것이 그 대칭이다.
>
🔴 **분모는 잡이다.** plan은 잡당 1회 사건이므로 학생 단위(`StudentResult`·`AGENT_STEP`)에 두지 않는다 — 잡당 1회 사건을 개체 단위 카운터에 태우지 않는다(99 ㊻ⓑ에서 세운 규율이고, 그래프가 이미 같은 근거로 plan 실패를 **서킷 카운터에서 제외**한다). ⚠ `plan_dropped`는 `all_dropped`가 아닌 경우에도 0이 아닐 수 있다(일부만 드롭) — 그래서 사유 코드와 개수가 **둘 다** 필요하다.
>
> 🔴 **본문을 담지 않는다** — 사유 코드와 개수까지다. state는 PostgresSaver 체크포인트와 LangSmith 노드 트레이스 두 경로로 나가므로(위 §1.2 각주 · 7/30 실측) 응답 원문·드롭된 강조점 문구를 담으면 그 두 경로로 그대로 샌다. `unparsed`와 "응답이 비었다"는 응답 **길이**로만 가른다.
>
> 이 두 필드는 `summarize`와 함께 `pack://` 결과 계약에 실려 워커가 저장한다(99 ㉕ — 그 레코드의 ERD 자리는 아직 없다).

**불변식:** ① `emphasis_points`의 모든 강조점은 `record_id` 동반(plan 노드도 Evidence 규칙 적용) ② `cursor`는 단조 증가 — 재개 시 `results` 길이와 일치 검증(불일치 = 체크포인트 손상 → failed) ③ 학생 1명 실패가 루프를 멈추지 않는다(계약: failed여도 완료분 보존) ④ 재개 시 `context_ref`를 역참조한 컨텍스트 묶음의 해시를 `context_hash`와 대조 — 불일치 = 체크포인트 손상 → failed.

> **`quota_consumed`의 단위(2026-08-06 확정 · 99 ㊻ⓑ):** **인터랙티브 생성 1건 = 1**이며 **LLM 호출 수가 아니다.** 게이트 실패 재생성으로 3번 불러도 1이고, 전송이 아예 없었던 학생(컨텍스트 부재·마스킹 fail-closed)은 0, 게이트 소진은 1이다(산출물은 없지만 원가는 발생했다). 호출 수는 `LLM_CALL` 행수가 이미 정확히 세므로 여기서 중복하면 **두 지표가 갈려 어느 쪽이 원장인지 알 수 없게 된다.** 증가 지점은 그래프의 `_record` **1곳**이다 — `cursor`·`results`를 전진시키는 유일한 관문이라 여기 두면 경로 누락이 구조적으로 불가능하다(종전에는 증가 지점이 `src/` 전체에 0개였다). ⚠ refine 턴은 이 state 밖이라 포함되지 않는다(AI_RUN·LLM_CALL로 남는다). 차단·카운트·표시는 전부 백엔드다(CLAUDE.md §7).

### 1.3 중단·재개

- 중단 지점: 학생 루프 경계에서만(블록 생성 중간 아님) — 체크포인트 단위 = 학생 1명 완료.
- `paused` 진입: 강사 수동 중단 · LLM 연속 실패 3학생(서킷) · 배치 윈도 초과.
- `POST /v1/agents/{id}/resume`: 체크포인트 로드 → `cursor`부터 — 이미 생성된 draft 재생성 없음(멱등).

> **결정 로그 (8/7 · #121).** 위 재개 규약은 ⑰에서 **지어졌지만**, 인메모리 백엔드의 서비스 경로에서는 **8/7까지 한 번도 실제로 돌지 않았다.** 체크포인터(`_open_saver`)와 잡 원장(`build_agent_job_store`)이 요청마다 새로 만들어져 응답과 함께 소멸했기 때문이다 — `aget_state`가 항상 빈 스냅숏을 받아 매번 init(`cursor=0`)이 투입됐고, 그래서 **불변식 ④ `context_hash` 대조도 서비스 경로에서는 실행된 적이 없다**(체크포인트가 남지도 않았으므로 손상될 것도 없었다). 워커 수준 테스트가 초록이었던 것은 그 테스트가 공유 인스턴스를 **손으로 만들어 줬기** 때문이다.
>
> 8/7에 둘을 프로세스 공용 싱글턴으로 바꿔 재개가 **처음으로 실제로 동작**했고(`tests/ai/failure/test_counsel_runtime_lifetime.py`), 그 테스트는 라우터가 부르는 함수만 부른다. 실측: 서킷 개방(임계 3) → `paused` → resume → **확정된 학생 재생성 0명**, 중단 지점 학생부터 순서대로 재개. ⚠ 서킷이 열린 그 학생은 **결과 기록 전**이라 재개가 다시 부른다 — 재생성이 아니라 미완료 재시도이며 위 "`cursor`부터"의 정확한 의미다.
>
> ⚠ **PG 백엔드는 처음부터 이 문제가 없었다**(저장소가 외부라 인스턴스 수명과 무관). 그리고 프로세스 공용 싱글턴은 **한 프로세스 안에서만** 참이라 멀티 워커에서는 다시 갈린다(99 ㉬) — 진짜 답은 PG 백엔드를 기본으로 올리는 것이고 그건 배포 축이다.

## 2. mapping_probe — 매핑 조사 ReAct

### 2.1 그래프

```
profile_read → reason → {tool_call ⇄ observe}(루프 ≤5) → propose_spec → confidence_check → end
```

### 2.2 State

```python
class ProbeStep(BaseModel):
    seq: int
    thought: str                               # 마스킹 후 저장 (AGENT_STEP)
    tool: Literal["get_unique_values", "get_more_sample", "check_join_key"] | None
    tool_args: dict
    observation_masked: str                    # 도구 반환 = 마스킹 통과분만 (구조적 차단)

class MappingProbeState(BaseModel):
    tenant_id: str
    source_profile_id: UUID
    sheets_meta: dict                          # 헤더·타입·샘플 통계 (원본 행 아님)

    steps: list[ProbeStep] = []
    loop_count: int = 0                        # ≤ 5 — 초과 시 강제 propose
    resolved_columns: dict[str, ColumnMapping] = {}
    unresolved_columns: list[UnresolvedColumn] = []   # '모름' 명시 — 사유 필수

    spec_draft: MappingSpecDraft | None = None
    overall_confidence: float | None = None
```

**불변식:** ① `loop_count > 5` 도달 시 남은 컬럼은 전부 `unresolved`로 — 억지 매핑 금지 ② `observation_masked`에 마스킹 실패 흔적(`⟪확인필요⟫`) 있으면 해당 관찰 폐기 후 루프 1회 소모 ③ 최종 spec의 컬럼 수 = resolved + unresolved (누락 없음 검증).

### 2.3 중단·재개

- 체크포인트 단위 = 도구 호출 1회. 단 이 에이전트는 수 분 내 종료가 정상 — 재개는 장애 복구용이지 UX가 아님. `paused` 12시간 초과 시 자동 `failed`(파일 상태가 바뀌었을 수 있음 — 재기동 권장).

### 2.4 워커③ problem_generation state

문제생성 워커는 세트 내 중복 판정·공통 재시도 예산·조기중단이 순서에 의존하므로 v1에서 문항 슬롯을 순차 처리한다. 체크포인트에는 문항 본문·프롬프트·원문을 복제하지 않고, 불변 요청 참조와 완료 결과 포인터만 둔다. `WorkerJob.phase`·lease·`result_ref`는 슈퍼바이저 정본이므로 이 state에 넣지 않는다.

```python
class ProblemGenerationState(BaseModel):
    state_schema_version: Literal["problem_generation.v1"]

    # 불변 입력 — WorkerJob.payload_ref/hash에서 기동 시 복사하고 이후 변경 금지
    request_ref: str
    request_hash: str                 # sha256:<64 lowercase hex>
    set_id: UUID
    target_source: TargetSource
    requested_count: int              # 1..20

    # 순차 진행
    cursor: int = 0                   # 완료된 슬롯 수이자 다음 슬롯의 0-based index
    items: tuple[ItemResult, ...] = ()
    item_attempt: int = 0             # 현재 cursor 슬롯에서 이미 소모한 공통 예산, 0..3
    fallback_ref: str | None = None   # 첫 검증본 ITEM_CANDIDATE 포인터 — 본문 미복제(part_b/10 §4.1 C3 · part_b/09 §2-4.6)
    difficulty_regen_used: bool = False   # 난이도 사유 재생성 1회 소모 여부(part_b/10 §4.1 C3)
    stop_reason: SetStopReason | None = None
```

**불변식:**

1. `state_schema_version`·`request_ref/hash`·`set_id`·`target_source`·`requested_count`는 기동 후 불변이다. `request_ref`를 역참조한 `ProblemRequest`의 해시와 투영 필드를 매 재개 시 검증하고 불일치하면 손상된 체크포인트로 실패한다.
2. `cursor == len(items)`이며 `items[n]`은 슬롯 `n`의 확정 `ItemResult`다. 한 전이에서 `cursor`는 그대로이거나 정확히 1만 증가하고 기존 `items` prefix를 바꿀 수 없다.
3. 진행 중 수량은 `requested_count == cursor + int(item_attempt > 0) + unstarted_count`다. `item_attempt > 0`인 현재 슬롯은 이미 시작했으므로 미착수 수량에 포함하지 않는다. 문항 완료·조기중단 체크포인트는 `item_attempt=0`이며, 최종 `ProblemSetResult.unstarted_count`는 이 경계에서 `requested_count - cursor`로 산출한다.
4. `item_attempt`는 현재 `cursor` 슬롯에서 **이미 시작해 예산에서 소모한 외부 생성 호출 수**이며 3을 넘지 않는다. 모든 외부 호출 전에 먼저 `item_attempt`를 1 증가시켜 체크포인트하고, 완료 전이의 `ItemResult.attempt_no`는 직전 state의 `item_attempt`와 정확히 같아야 한다. 첫 호출 성공은 `0 → 1 → cursor+1(attempt_no=1)`, 세 번째 호출 소진 폐기는 `2 → 3 → cursor+1(attempt_no=3)` 순서다. 완료 후 다음 슬롯의 `item_attempt`는 0으로 초기화하며 이전 슬롯의 재시도 수를 이월하지 않는다.
5. `stop_reason`이 설정되거나 `cursor == requested_count`이면 terminal worker state다. terminal state에는 `item_attempt`를 남길 수 없고 이후 state 전이를 금지한다.
6. terminal state는 성공 문항 수로 `generated|partial_success|failed`를 결정해 `ProblemSetResult`로 변환한다. `processed_count=cursor`, `unstarted_count=requested_count-cursor`, `items`는 그대로 보존한다. `verification_unavailable`도 처리 완료 슬롯이므로 `cursor`와 `items`에 포함한다.

**체크포인트·멱등 재개:**

- 안전한 pause·우선순위 양보 경계는 문항 하나의 최종 `ItemResult`를 확정하고 `item_attempt=0`으로 초기화한 직후다. LangGraph는 generator 등 외부 호출 **직전** 증가한 `item_attempt`를 체크포인트한다. 호출 도중 장애가 나도 해당 시도는 이미 소모된 것으로 간주해 재개 시 총 3회 예산을 되돌리지 않는다.
- 슬롯 저장 키는 결정론적 `(set_id, cursor)`(`current_slot_key`)이며 저장소에서 unique/upsert로 강제한다. 결과 저장 후 체크포인트 전에 프로세스가 종료돼도 재개 시 같은 키의 기존 결과를 먼저 읽어 `items`에 연결하고 LLM 생성을 반복하지 않는다.
- 재개는 마지막 `cursor`부터 한 슬롯씩 수행한다. state 전이 검증은 불변 요청 변경, cursor 건너뛰기, 완료 items 교체를 거부한다.

## 3. 공통 규칙

### 3.1 체크포인트 직렬화

- 워커 state는 Pydantic 모델로 검증한 뒤 PostgresSaver에 저장한다. **저장 전 redaction 통과 필수**(state 안 자유 텍스트: `thought`·`emphasis_points` — 마스킹 정의서 §3 훅).
- PostgresSaver 테이블이 워커 state 정본이다. `WorkerJob`을 투영한 `AGENT_RUN`에는 `checkpoint_ref`와 조회용 진행 상태만 두고 state 본문을 복제하지 않는다. 기존 `state_checkpoint` 컬럼은 마스킹된 관측 캐시로만 남기며 재개에 사용하지 않는다.
- 스키마 버전 필드 `state_schema_version` 포함 — 역직렬화 시 버전 검사.

### 3.2 재개 시나리오 3종

| 시나리오 | 동작 |
| --- | --- |
| **장애 복구**(프로세스 다운) | 마지막 체크포인트에서 자동 재개 — 멱등키로 중복 draft 방지 |
| **수동 중단→재개**(강사) | `paused` → resume API. counsel_pack만 해당(probe는 비노출) |
| **버전 업그레이드 중** | `state_schema_version` 불일치 시 **재개 거부 + 처음부터 재기동**(마이그레이션 시도 안 함 — 완료분 draft는 유효하므로 멱등 스킵으로 사실상 이어짐) |

### 3.3 관측·기록

- 노드 진입/종료마다 `AGENT_STEP` 행(ERD v2) — LangSmith 도입(B-6) 전에도 자체 추적 가능하게.
- LLM 노드는 `llm_call_id` 연결 — 상담팩 원가 = AGENT_RUN 단위 집계(쿼터 명세 `batch_jobs`).

## 4. 워커 공통 후속 항목

1. ✅ **해소(7/30)** — "state에 **B 소유** 타입(`DraftContext` 등) 의존"은 stale 서술이었다. `02_ownership.md` §3은 `composition.py`를 **박진희 단독**으로 지정하므로 상담 타입인 `DraftContext`는 **A 소유**이고 양자 승격 대상이 아니다. 게다가 §1.2 개정으로 `contexts`가 state에서 빠져(`context_ref`+`context_hash` 참조로 교체) **state의 타입 의존 자체가 사라졌다** — `contracts/` 승격할 최소 집합은 없다. `DraftContext`의 정의 위치(`composition/` 내부 vs `contracts/composition.py`)는 counsel_pack 워커 구현 시 A가 단독 결정한다.
2. ✅ 체크포인트 정본은 LangGraph PostgresSaver 테이블, `AGENT_RUN`은 최신 `checkpoint_ref`·진행률 투영으로 확정
3. probe 도구 3종의 시그니처 동결(마스킹 규칙 포함 — 마스킹 정의서 §4)


---

## 5. 슈퍼바이저 실행 계약 `[A+B 확정 7/27]`

### 5.1 역할과 정본

슈퍼바이저는 LangGraph 에이전트가 아니라 **LLM 없는 결정론 디스패처**다. 역할은 영속 큐의 lease 획득, 고정 라우팅, 실행 phase 전이, 재개 신호 전달, terminal 이벤트 기록으로 제한한다.

- 중앙 `jobs: list`·`running: dict`를 가진 `SupervisorState`는 사용하지 않는다. 프로세스 메모리는 정본이 아니며 재시작·멀티 프로세스 경합을 견딜 수 없기 때문이다.
- `contracts/agents.py`의 `WorkerJob`을 1:1 투영한 AI PostgreSQL `AGENT_RUN` 행이 큐·lease·우선순위·실행 phase의 유일한 정본이다.
- 각 워커만 독립 LangGraph state와 PostgresSaver 체크포인트를 가진다. 워커 간 state 공유와 슈퍼바이저 state로의 결과 본문 복제를 금지한다.
- 슈퍼바이저는 워커 산출물(draft·spec·문항)을 수정·요약·재작성하지 않고 `result_ref`만 기록한다.

공통 계약의 정본은 `contracts/agents.py`다. 영속 Job에는 최소한 다음 정보가 보존되어야 한다.

```python
class WorkerJob(BaseModel):
    job_id: UUID
    execution_id: UUID
    tenant_id: str
    worker_kind: WorkerKind
    operation: OperationKind
    payload_ref: str
    payload_hash: str
    phase: JobPhase
    priority_class: PriorityClass
    dispatch_attempt: int
    lease_generation: int
    recovery_count: int
    max_recovery_attempts: int             # 기본 3
    lease_owner: str | None
    lease_acquired_at: datetime | None
    lease_expires_at: datetime | None
    checkpoint_ref: str | None
    result_ref: str | None
    error_code: str | None
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
```

`payload_ref`의 대상은 실행 중 불변이어야 하며 `payload_hash`로 변조·교체를 검출한다. lease 획득과 phase 전이는 조건부 갱신으로 원자 처리하고, 만료된 실행자의 후속 저장은 `lease_generation` fencing으로 거부한다. `dispatch_attempt`와 `lease_generation`은 최초 실행·장애 재개·수동 resume을 포함해 실제 lease를 획득할 때마다 함께 1 증가한다. `recovery_count`는 만료 회수 전용 카운터이며 수동 resume은 소모하지 않는다.

### 5.2 라우팅과 operation

요청 1건은 정확히 Job 1건과 워커 1종으로 라우팅한다. 한 Job을 세 워커에 나누어 보내거나 LLM으로 워커를 선택하지 않는다.

| operation | worker_kind | Job 단위 |
| --- | --- | --- |
| `counsel_pack.generate` | `counsel_pack` | 상담팩 1건(학생 목록 포함) |
| `mapping_probe.resolve` | `mapping_probe` | Import source profile 1건 |
| `problem_set.generate` | `problem_generation` | 문항 세트 1건 |
| `problem_item.refine` | `problem_generation` | 문항 리비전 1건 |
| `problem_item.reverify` | `problem_generation` | 문항 재검증 1건 |

라우팅 테이블은 양자 승인 계약이다. 새 operation 추가나 worker 변경은 문자열을 임의로 분기하지 않고 `contracts/agents.py`와 계약 테스트를 함께 변경한다.

### 5.3 공통 실행 phase

- `queued`: 실행 가능 영속 대기 상태
- `leased`: 한 실행자가 제한 시간 동안 원자적으로 소유권을 획득한 상태
- `running`: 워커가 시작되어 lease를 갱신하는 상태
- `paused`: 유효한 체크포인트가 있어 재개 가능한 상태
- `succeeded`: 워커가 계약에 맞는 도메인 결과를 영속화한 상태
- `failed`: 복구 불가능한 실행 장애로 계약에 맞는 결과를 확정하지 못한 상태
- `cancelled`: 취소 요청을 안전한 체크포인트 경계에서 반영한 상태

| 전이 종류 | 허용 전이 |
| --- | --- |
| 일반 | `queued → leased`, `queued → cancelled` |
| 일반 | `leased → running`, `leased → failed`, `leased → cancelled` |
| 일반 | `running → paused`, `running → succeeded`, `running → failed`, `running → cancelled` |
| 일반 | `paused → cancelled` |
| 명시적 resume 전용 | `paused → queued` |
| 만료 회수 전용 | `leased → queued`, `running → queued`; 상한 소진 시 각각 `failed` |

`succeeded`·`failed`·`cancelled`는 terminal이며 이후 전이를 금지한다. `leased|running → queued`는 일반 워커 전이가 아니라 Job 저장소의 `recover_expired` 전용 전이이다. 실행 중 lease를 잃은 워커는 오래된 `lease_generation`으로 체크포인트·결과를 기록할 수 없다.

`recover_expired`는 최신 `checkpoint_ref`가 있으면 그 지점부터 재개한다. 체크포인트가 없어도 동결된 `payload_ref`·`payload_hash`와 멱등 산출물 저장을 전제로 처음부터 재실행할 수 있다. 만료 회수에서만 `recovery_count`를 정확히 1 증가시키며, `max_recovery_attempts=3`이면 세 번째 만료 회수에서 `queued`로 되돌리지 않고 `failed(error_code=worker_recovery_exhausted)`로 수렴한다. 회수 자체는 `dispatch_attempt`·`lease_generation`을 바꾸지 않고, 회수 후 `queued → leased`로 실제 재획득할 때 두 값을 함께 1 증가시킨다. 수동 resume은 `paused → queued`만 수행하고 `recovery_count`를 소모하지 않으며, 이후 lease 획득 시에만 `dispatch_attempt`·`lease_generation`을 증가시킨다.

### 5.4 실행 phase와 도메인 결과 분리

공통 phase는 “작업 실행이 계약대로 끝났는가”만 나타낸다. 워커가 유효한 부분 결과·명시적 미해결 결과를 저장했다면 도메인 status와 무관하게 Job은 `succeeded`다.

| 워커 | 정상 수렴하는 도메인 결과 예 | Job phase |
| --- | --- | --- |
| counsel_pack | 일부 학생 `skipped_insufficient`·`failed`, 완료 draft 보존 | `succeeded` |
| mapping_probe | 일부 컬럼 `unresolved`·`needs_review`, 상한 5회 후 명시적 미해결 | `succeeded` |
| problem_generation | `RejectedInsufficientOutcome`, 세트 `partial_success`·도메인 `failed`, 문항 `verification_unavailable`·`dropped` | `succeeded` |

워커 프로세스 장애, 손상된 체크포인트, 결과 저장 불가처럼 유효한 결과 계약 자체를 확정하지 못한 경우만 Job `failed`다. `paused`는 terminal 실패가 아니라 재개 가능한 실행 상태다.

### 5.5 fan-out·fan-in과 v1 순차 처리

- **슈퍼바이저 fan-out:** 서로 독립인 여러 Job은 워커 풀 용량 안에서 병렬 배분할 수 있다. 단일 Job을 여러 워커에 fan-out하지 않는다.
- **슈퍼바이저 fan-in:** 워커 본문 결과를 병합하지 않는다. 워커가 영속화한 `result_ref`를 검증해 terminal phase와 이벤트로 수렴시키는 것이 전부다.
- **counsel_pack:** 학생 순서가 재현성과 연속 실패 서킷에 영향을 주므로 학생 단위 순차 처리한다. 학생 1명 완료 경계에서 체크포인트한다.
- **mapping_probe:** 이전 observation이 다음 reason 입력이므로 도구 호출을 순차 처리한다. 도구 호출 1회 경계에서 체크포인트한다.
- **problem_generation:** 세트 내 중복 판정·공통 재시도 예산·조기 중단이 처리 순서에 의존하므로 문항 단위 순차 처리한다. 문항 1개 완료 경계에서 체크포인트한다.

문항 생성 병렬화는 v1 범위 밖이다. 도입하려면 문항 슬롯, 중복 판정 순서, 조기 중단과 미처리 수량의 결정론을 별도 계약으로 먼저 확정해야 한다.

### 5.6 우선순위·양보·재개

- 기본 우선순위는 `problem_item.refine`·`problem_item.reverify`=`interactive`, `mapping_probe.resolve`·`problem_set.generate`=`standard`, `counsel_pack.generate`=`batch`다.
- 실행 중 강제 중단은 금지한다. 실행 중 Job은 각 워커의 안전한 체크포인트 경계에서만 협력적으로 양보하고 `paused`로 수렴한다.
- 같은 우선순위는 `queued_at → job_id` 순으로 결정론 정렬하며, 오래 대기한 배치가 영구 기아 상태가 되지 않도록 aging을 적용한다.
- 재개 로직은 워커가 소유한다. 슈퍼바이저는 `checkpoint_ref`를 전달하고 phase를 관리할 뿐 state를 해석하지 않는다.

### 5.7 이벤트 멱등

terminal phase 전이와 Kafka outbox 기록은 같은 DB 트랜잭션에서 수행한다. 재전송은 저장된 동일 `event_id`를 사용하며, `(job_id, terminal phase)`당 terminal 이벤트는 논리적으로 하나만 존재한다. 전송은 at-least-once이므로 백엔드는 `event_id`로 중복을 제거한다. 워커가 Kafka terminal 이벤트를 직접 발행해서는 안 된다.
