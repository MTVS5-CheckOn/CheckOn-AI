"""AI_RUN 원장 **완전성** 판정 — 🔴 **없어진 FK를 흉내 내는 검사가 아니다** (99 #36 G2).

③ 판정으로 `fk_agent_run_run_id_ai_run`이 사라지면 **고아를 DB가 안 막는다.** 그 자리를
메우는 것이 이 모듈인데, **똑같은 명제를 애플리케이션으로 옮기면 안 된다**:

> ❌ `AGENT_RUN`이 있는데 `AI_RUN`이 없으면 전부 red

**잡은 모델 실행보다 먼저 존재한다.** 위 조건은 **정상 생애주기를 결함으로 오인**한다 —
그게 애초에 FK를 못 쓰게 만든 이유다(99 #36 §0).

🔴 **그래서 다섯 값을 섞지 않는다.** 「정상적인 부재」·「확정 결함」·「증명 불가」·
「㉾의 별도 결손」은 **서로 다른 사실**이고, 하나로 뭉치면 어느 쪽이든 거짓이 된다.

## 🔴 관측 가능성 전수 (2026-08-10 실측) — 무엇이 「실 호출의 독립 증거」가 되는가

| 자료 | 독립 증거인가 |
| --- | --- |
| `LLM_CALL` | ❌ **아니다.** `run_id`가 **`ai_run` FK**라 |
| | **`AI_RUN`이 없으면 `LLM_CALL`도 못 남는다** |
| `AI_RUN` | ❌ 그 부재 자체가 판정 대상이다 |
| **`AGENT_STEP`** | ✅ **된다.** `agent_run_id`가 **`agent_run` FK**이고 |
| | `llm_call_id`는 **FK가 없는 nullable UUID**라 |
| | 원장이 비어도 **행과 값이 남는다** |
| `AGENT_RUN.result_ref` | ✅ 결과 계약 저장 = **실행이 끝까지 갔다** |
| `AGENT_RUN.status`·`agent_kind` | ⚠ 생애주기 축이지 호출 축이 아니다 |
| `LlmCallCollector` · Fake 기록 | ❌ **프로세스 안**에만 있다 — 사후 점검이 못 본다 |

🔴 **결정적 한계 — 「DB에 증거가 없다」를 「LLM 0콜」로 판정하지 않는다.**
`record_run()`은 `LlmCallCollector.take()` **뒤에** DB에 쓴다. 실패 경로는
`swallow_errors=True`라 **쓰기가 터져도 삼킨다** ⇒ 그 순간 **수집기에서도 호출이 빠지고
DB에도 `AI_RUN`·`LLM_CALL`이 없다.** 「안 불렀다」와 「부르고 기록을 잃었다」가
**증거상 같아 보인다** ⇒ 그 상태는 `unknown`이다. **초록으로 세지 않는다.**
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ai.contracts.agents import JobPhase, WorkerKind
from ai.contracts.execution import Capability


class LedgerVerdict(StrEnum):
    """🔴 **다섯이 섞이면 안 된다** — 각각 다른 사실이다."""

    OK = "ok"
    """원장이 있고 논리 결합도 맞다."""
    ALLOWED_ABSENCE = "allowed_absence"
    """**없는 것이 정상**인 생애주기 — 아직 실행 전이거나 실행 없이 끝났다."""
    VIOLATION = "violation"
    """🔴 **확정 결함** — 있어야 하는데 없다."""
    UNKNOWN = "unknown"
    """⚠ **증명 불가** — 호출 여부를 가릴 증거가 없다. **초록이 아니다.**"""
    SEPARATE_GAP = "separate_gap"
    """⚠ **㉾의 별도 결손**(`mapping_probe`) — 이 관문에 섞지 않는다."""


#: 🔴 **워커 → capability는 프로덕션이 실제로 쓰는 값에서 온다**(문자열 재기입 금지).
#: `composition/counsel/worker.py`가 `Capability.COMPOSITION`을,
#: `problem_generation/assembly.py`가 `Capability.PROBLEM_GENERATION`을 쓴다.
#: ⚠ **`mapping_probe`는 일부러 비웠다** — 그 워커는 `record_run()`을 **한 번도 안 부르므로**
#: 기대 capability를 **관측한 적이 없다.** 임의로 정하지 않는다 — **㉾ 구현이 정한다.**
WORKER_CAPABILITY: Final[dict[WorkerKind, Capability]] = {
    WorkerKind.COUNSEL_PACK: Capability.COMPOSITION,
    WorkerKind.PROBLEM_GENERATION: Capability.PROBLEM_GENERATION,
}

#: 실행이 **아직 안 끝난** 상태 — 원장이 없는 것이 정상이다.
_IN_FLIGHT: Final = frozenset(
    {JobPhase.QUEUED, JobPhase.LEASED, JobPhase.RUNNING, JobPhase.PAUSED}
)
#: **종단이고 원장이 반드시 있어야 하는** 워커.
_LEDGER_REQUIRED_ON_SUCCESS: Final = frozenset(WORKER_CAPABILITY)


class LedgerObservation(BaseModel):
    """중립 관측 행 — **판정을 담지 않는다.** 조회가 만들고 판정이 읽는다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    job_id: UUID
    run_id: UUID
    agent_kind: WorkerKind
    status: JobPhase
    started_at_is_set: bool
    error_code: str | None = None
    result_ref: str | None = None
    #: `AGENT_STEP` — 🔴 **원장 없이도 남는 유일한 증거**(위 표).
    step_count: int = 0
    steps_with_llm_call: int = 0
    #: `AI_RUN` 쪽(**같은 테넌트만**) — 없으면 전부 `None`.
    ai_run_execution_id: UUID | None = None
    ai_run_tenant_id: str | None = None
    #: 🔴 **enum이 아니라 raw string이다.** 변환 단계에서 바로 `Capability(...)`로 바꾸면
    #: **미등록 값 하나가 점검 전체를 예외로 죽인다** — 그 행만 결함이어야 한다.
    ai_run_capability: str | None = None
    #: 🔴 **같은 `execution_id`가 남의 테넌트에 있는가**(존재 여부만).
    #: ⚠ **남의 테넌트 ID는 안 담는다** — 조인에서 테넌트를 빼고 전문을 읽는 방식으로
    #: 고치지 않는다. 그건 경계를 넘는 것이다.
    run_id_exists_in_other_tenant: bool = False

    @property
    def has_ledger(self) -> bool:
        return self.ai_run_execution_id is not None

    @property
    def consumed_a_call(self) -> bool:
        """🔴 **실 호출의 「양성」 증거만** 센다 — 없다고 0콜로 읽지 않는다.

        ⚠ **`result_ref`는 여기 안 넣는다** — 그건 **산출물 저장·종단 증거**이고
        **LLM 0콜 성공 경로도 결과를 만든다.** 호출 증거라고 부르면
        **관측의 이름이 실제로 보는 것보다 넓어진다**(로그 85 계열).
        """
        return self.steps_with_llm_call > 0


class LedgerFinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    observation: LedgerObservation
    verdict: LedgerVerdict
    reason: str


def judge_ledger_row(observation: LedgerObservation) -> LedgerFinding:
    """관측 한 행 → 판정 하나. **I/O 없음.**"""
    verdict, reason = _verdict_for(observation)
    return LedgerFinding(observation=observation, verdict=verdict, reason=reason)


def _verdict_for(o: LedgerObservation) -> tuple[LedgerVerdict, str]:
    #: 🔴 **교차 테넌트 충돌이 가장 먼저다.** 뒤에 두면 `queued`가 먼저 걸려
    #: **정상 부재로 조용히 통과**한다(실측 8/10 — 그 분기는 실 PG로 도달 불가였다).
    if o.run_id_exists_in_other_tenant:
        return (
            LedgerVerdict.VIOLATION,
            "같은 run_id의 AI_RUN이 **다른 테넌트에** 있다 — 논리 결합이 경계를 넘었다"
            "(남의 식별자는 싣지 않는다)",
        )
    if o.has_ledger:
        return _logical_binding(o)
    if o.agent_kind is WorkerKind.MAPPING_PROBE:
        return (
            LedgerVerdict.SEPARATE_GAP,
            "mapping_probe는 record_run()을 한 번도 안 부른다 — ㉾의 별도 결손이다"
            "(이 관문에 섞지 않는다)",
        )
    #: ⚠ **`running`은 증거가 있어도 허용한다** — `finally`의 원장 적재 **직전**일 수 있다.
    #:   「아직 안 썼다」와 「안 쓸 것이다」를 이 시점엔 못 가른다.
    if o.status is JobPhase.RUNNING:
        return (
            LedgerVerdict.ALLOWED_ABSENCE,
            "running — 원장 적재 전 정상 순간일 수 있다(증거가 있어도 red로 만들지 않는다)",
        )
    #: 🔴 **`paused`는 다르다** — 서킷 개방은 **호출을 이미 소비한 뒤**에 온다.
    #:   증거가 있으면 원장이 있어야 한다. 종전엔 `_IN_FLIGHT`가 먼저 걸려 **증거를 덮었다.**
    if o.status in _IN_FLIGHT and not o.consumed_a_call:
        return (
            LedgerVerdict.ALLOWED_ABSENCE,
            f"{o.status.value}는 실행이 안 끝났다 — 원장이 없는 것이 정상이다",
        )
    if o.status is JobPhase.CANCELLED and not o.started_at_is_set:
        return (
            LedgerVerdict.ALLOWED_ABSENCE,
            "실행 전 취소 — 만들 실행이 없었다",
        )
    if o.status is JobPhase.SUCCEEDED and o.agent_kind in _LEDGER_REQUIRED_ON_SUCCESS:
        return (
            LedgerVerdict.VIOLATION,
            f"{o.agent_kind.value}가 succeeded인데 AI_RUN이 없다 — 종단인데 기록이 없다",
        )
    if o.consumed_a_call:
        return (
            LedgerVerdict.VIOLATION,
            "실제 LLM 호출을 소비한 양성 증거가 있는데 AI_RUN이 없다"
            f"(agent_step llm_call {o.steps_with_llm_call}건 · result_ref="
            f"{'있음' if o.result_ref else '없음'})",
        )
    return (
        LedgerVerdict.UNKNOWN,
        f"{o.status.value}이고 호출 여부를 가릴 증거가 없다 — "
        "실패 경로는 적재 실패를 삼키므로(swallow_errors) 「안 불렀다」와 "
        "「부르고 기록을 잃었다」가 증거상 같다. 초록이 아니다",
    )


def _logical_binding(o: LedgerObservation) -> tuple[LedgerVerdict, str]:
    """🔴 FK가 사라졌으므로 `run_id`는 **논리 결합 키**다 — 있을 때는 맞는지 본다."""
    if o.ai_run_execution_id != o.run_id:
        return (
            LedgerVerdict.VIOLATION,
            f"run_id({o.run_id})와 AI_RUN.execution_id({o.ai_run_execution_id})가 다르다",
        )
    if o.ai_run_tenant_id != o.tenant_id:
        return (
            LedgerVerdict.VIOLATION,
            "AI_RUN이 다른 테넌트의 행이다 — 논리 결합이 경계를 넘었다",
        )
    expected = WORKER_CAPABILITY.get(o.agent_kind)
    #: 🔴 **문자열로 대조한다** — 관측이 raw string을 들고 오므로 미등록 값도 **여기서**
    #: 결함이 된다(변환 단계에서 죽지 않는다).
    if expected is not None and o.ai_run_capability != expected.value:
        return (
            LedgerVerdict.VIOLATION,
            f"{o.agent_kind.value} 잡인데 AI_RUN.capability가 "
            f"{o.ai_run_capability!r}다(기대 {expected.value!r})",
        )
    return (LedgerVerdict.OK, "원장이 있고 논리 결합이 맞다")


def summarize(findings: Sequence[LedgerFinding]) -> dict[LedgerVerdict, int]:
    """판정별 건수 — **0건도 키로 남긴다**(없는 것과 안 센 것을 가르려고)."""
    counts = dict.fromkeys(LedgerVerdict, 0)
    for finding in findings:
        counts[finding.verdict] += 1
    return counts


def blocks_flip(counts: dict[LedgerVerdict, int]) -> bool:
    """🔴 **플립을 막는 것** — `violation`뿐 아니라 `unknown`도 막는다.

    ⚠ `unknown`을 통과시키면 **증명 못 한 것이 초록으로 세어진다.**
    ⚠ `separate_gap`은 **안 막는다** — ㉾는 별도 결손이고 이 관문의 조건이 아니다.
    """
    return counts[LedgerVerdict.VIOLATION] > 0 or counts[LedgerVerdict.UNKNOWN] > 0
