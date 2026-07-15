# [체크온] LangGraph 상태 스키마 명세 v0 — 에이전트 2종 (⚠ 초안)

> **지위: B-1 승인 (7/15) — 착수 가능.** 단 회의에서 구조가 확장됐다: **상위 슈퍼바이저 1 + 워커 에이전트 3** — ① counsel_pack(상담팩, A) ② mapping_probe(매핑 조사, A) ③ problem_generation(문제 생성, B). 본 문서의 워커 2종 state는 그대로 유효. **슈퍼바이저 state 초안은 §5에 작성 완료(B 리뷰 대기)**. 문제 생성 워커의 state는 B 소유.
> 체크포인터: LangGraph PostgresSaver → AI PG(`AGENT_RUN.state_checkpoint`) — 별도 스토리지 없음.

---

## 1. counsel_pack — 상담팩 오케스트레이터

### 1.1 그래프 (파이프라인 v2 확정 흐름)

```
plan → [학생 루프: assemble_context → generate_draft → gate_check → record] → summarize
                              ↑ gate 실패 시 블록 재시도(≤3) · 학생 단위 실패는 스킵하고 계속
```

### 1.2 State (Pydantic)

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
    contexts: dict[str, DraftContext]          # 학생별 — label_snapshot 동결 포함
    plan_version: str

    # plan 노드 산출 (LLM 1회 — 확정 수치 내 강조점만, 새 사실 생성 금지)
    emphasis_points: dict[str, list[str]]      # student_ref → 강조점(근거 record_id 필수)

    # 진행 상태 (체크포인트 대상)
    cursor: int = 0                            # student_refs 인덱스 — 재개 지점
    results: list[StudentResult] = []
    quota_consumed: int = 0                    # 미터링 리포트용 (차단은 백엔드)

    # 종료 산출
    summary: str | None = None                 # "22명 중 19명 생성·2명 데이터 부족·1명 실패"
```

**불변식:** ① `emphasis_points`의 모든 강조점은 `record_id` 동반(plan 노드도 Evidence 규칙 적용) ② `cursor`는 단조 증가 — 재개 시 `results` 길이와 일치 검증(불일치 = 체크포인트 손상 → failed) ③ 학생 1명 실패가 루프를 멈추지 않는다(계약: failed여도 완료분 보존).

### 1.3 중단·재개

- 중단 지점: 학생 루프 경계에서만(블록 생성 중간 아님) — 체크포인트 단위 = 학생 1명 완료.
- `paused` 진입: 강사 수동 중단 · LLM 연속 실패 3학생(서킷) · 배치 윈도 초과.
- `POST /v1/agents/{id}/resume`: 체크포인트 로드 → `cursor`부터 — 이미 생성된 draft 재생성 없음(멱등).

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

## 3. 공통 규칙

### 3.1 체크포인트 직렬화

- Pydantic `model_dump_json()` → JSONB. **저장 전 redaction 통과 필수**(state 안 자유 텍스트: `thought`·`emphasis_points` — 마스킹 정의서 §3 훅).
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

## 4. B-1 미팅에서 확인할 것

1. state에 B 소유 타입(DraftContext 등) 의존 — `contracts/`로 승격할 최소 집합
2. PostgresSaver 테이블을 AGENT_RUN.state_checkpoint(jsonb)로 흡수할지, LangGraph 기본 테이블 별도로 둘지 (A 제안: 기본 테이블 별도 + AGENT_RUN은 최신 스냅숏 캐시)
3. probe 도구 3종의 시그니처 동결(마스킹 규칙 포함 — 마스킹 정의서 §4)


---

## 5. (7/15 신규 — B-1 확장) 슈퍼바이저 state 초안 `[B 리뷰 대상]`

구조: **슈퍼바이저 1 + 워커 3** — counsel_pack(A) · mapping_probe(A) · problem_generation(B). 슈퍼바이저의 역할은 **큐·라우팅·상태 집계·재개 관리**이며, 판단을 하지 않는다.

```python
class WorkerJob(BaseModel):
    job_id: UUID
    worker: Literal["counsel_pack", "mapping_probe", "problem_generation"]
    tenant_id: str
    payload_ref: str                   # 요청 원본 참조 (state에 페이로드 복제 금지)
    status: Literal["queued", "running", "paused", "done", "failed"]
    agent_run_id: UUID | None          # 기동 후 연결
    priority: int = 0                  # 인터랙티브(문항 생성) > 배치(상담팩)

class SupervisorState(BaseModel):
    jobs: list[WorkerJob] = []
    running: dict[str, UUID] = {}      # worker → 현재 job (워커당 동시 1 — v1 단순화)
    completed_count: int = 0
    schema_version: str = "sup-1"
```

**불변식(제안):**
1. **라우팅은 결정론** — 요청 kind → worker 매핑은 고정 테이블. 슈퍼바이저에 LLM 없음(LLM 호출 0회).
2. **워커 결과 불변** — 슈퍼바이저는 워커 산출물(draft·spec·문항)을 수정·요약·재작성하지 않는다. 상태 집계만.
3. **격리 유지** — 워커 간 state 공유 금지. 슈퍼바이저는 job_id·status만 알고 내용을 모른다(내용은 각 워커 소유 테이블에).
4. 재개는 워커 체크포인트에 위임 — 슈퍼바이저는 `paused` job의 resume 신호만 전달.

**B와 확인할 것:** ① problem_generation 워커의 잡 단위(문항 세트 1건?) ② 우선순위 정책(인터랙티브 선점 여부) ③ 슈퍼바이저 소유 — 제안: `agents/`(A)에 두되 라우팅 테이블은 양자 승인.
