"""plan 사유 시나리오 하네스 — 그래프를 실제로 돌려 state를 돌려준다(㉲).

테스트 파일 안에 두면 `test_` 접두 함수가 아닌 헬퍼가 200줄을 먹어 본문이 안 보인다.
`_` 접두라 pytest가 수집하지 않는다.

⚠ **plan 노드만 태운다** — 학생 루프까지 돌리면 이 하네스가 초안 생성·게이트까지 안게
되고, 그러면 plan 사유가 아니라 게이트 사유를 재는 테스트가 된다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from uuid import UUID

from langgraph.checkpoint.memory import InMemorySaver

from ai.composition.counsel.graph import build_counsel_graph
from ai.composition.counsel.provider import (
    PlanUnparsedError,
    RedactionBlockedError,
)
from ai.composition.counsel.state import CounselPackState
from ai.composition.counsel.stores import InMemoryDraftResultStore
from ai.contracts.composition import (
    CommStyle,
    DraftContext,
    EvidenceFact,
    Frequency,
    Interest,
    LabelSnapshot,
    Sensitivity,
)
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import LlmUnavailable

_NOW = datetime(2026, 8, 8, 3, 0, tzinfo=UTC)
_HASH = "sha256:" + "a" * 64
_REFS = ("st_1", "st_2")

#: 시나리오 → plan이 낼 강조점. `Exception`이면 그걸 던진다.
_PLANS: dict[str, dict[str, list[str]] | Exception] = {
    # 정상 — 실존 record_id를 인용한다.
    "ok": {"st_1": ["정답률 흐름 (record_id=le_2041)"]},
    # 호출 자체가 실패한다.
    "llm_failed": LlmUnavailable("plan 업스트림 장애"),
    # 🔴 마스킹 불확실 — **전송 자체를 안 했다**(fail-closed · 불변식 3).
    #    ⚠ `RedactionBlockedError`도 `LlmError` 하위라, 그래프의 절 순서가 뒤집히면
    #      `llm_failed`로 뭉개진다 — 이 시나리오가 그 순서를 고정한다(99 #05).
    "redaction_blocked": RedactionBlockedError("plan 프롬프트의 마스킹이 불확실하다"),
    # 응답은 왔는데 형식을 안 지켰다 — planner가 갈라서 올린다(그래프는 dict로는 못 안다).
    "unparsed": PlanUnparsedError("plan 응답이 형식을 지키지 않았다 — 파싱 0건(응답 24자)"),
    # 파싱은 됐는데 전부 없는 record_id다 → 근거 검증에서 전량 드롭.
    "all_dropped": {"st_1": ["지어낸 근거 (record_id=fake_1)"]},
    # 진짜로 강조할 게 없었다 — 모델이 비워 뒀다(빈 응답 = 적법한 답).
    "nothing_to_say": {},
    # 일부만 드롭 — 하나는 살고 하나는 죽는다.
    "partial_drop": {
        "st_1": ["정답률 흐름 (record_id=le_2041)", "지어냄 (record_id=fake_1)"]
    },
}

#: 🔴 `unparsed`와 `nothing_to_say`는 **반환값이 똑같이 `{}`** 다. 그래서 planner가
#: `PlanUnparsedError`로 **갈라서 올린다** — 그래프가 반환값만 보고는 영원히 못 가른다.
#: 이 하네스가 그 계약을 그대로 흉내낸다(실 planner는 `GatewayPlanner`가 같은 판정을 한다).


def _context(ref: str) -> DraftContext:
    return DraftContext(
        student_ref=ref,
        guardian_ref=f"gd_{ref}",
        label_snapshot=LabelSnapshot(
            comm=CommStyle.DATA,
            sensitivity=Sensitivity.ANXIOUS,
            interest=Interest.GRADE,
            frequency=Frequency.FREQUENT,
        ),
        facts=(
            EvidenceFact(label="이번 주 정답률", value="62%", record_id="le_2041"),
        ),
        evidence_summaries=(),
        period_label="2026년 8월",
        fallback_text="이번 주 학습 상황을 정리해 보내드립니다.",
    )


class _ScriptedPlanner:
    """시나리오대로 답하는 planner — 결정론(시계·난수 없음)."""

    def __init__(self, scenario: str) -> None:
        self._scenario = scenario

    async def plan(
        self,
        *,
        contexts: Mapping[str, DraftContext],
        student_refs: Sequence[str],
        execution_context: ExecutionContext,
    ) -> dict[str, list[str]]:
        del contexts, student_refs, execution_context
        planned = _PLANS[self._scenario]
        if isinstance(planned, Exception):
            raise planned
        return dict(planned)


class _UnusedWriter:
    """plan 노드만 태우므로 write는 안 불린다 — 불리면 그게 버그다."""

    async def write(self, **kwargs: object) -> str:
        raise AssertionError("plan 시나리오에서 write가 불렸다 — 하네스 범위를 넘었다")


def _execution_context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=UUID("00000000-0000-4000-8000-0000000000e2"),
        tenant_id="t1",
        capability=Capability.COMPOSITION,
        input_snapshot_hash="h",
        versions=VersionSet(
            pipeline_version="0.1",
            engine_version="0.1",
            schema_version="0.1",
            contract_version="0.1",
        ),
    )


def run_plan_scenario(scenario: str) -> CounselPackState:
    """plan 노드 1회를 돌리고 그 결과 state를 돌려준다."""
    contexts = {ref: _context(ref) for ref in _REFS}
    graph = build_counsel_graph(
        planner=_ScriptedPlanner(scenario),
        writer=_UnusedWriter(),
        contexts=contexts,
        execution_context=_execution_context(),
        checkpointer=InMemorySaver(),
        regen_max=3,
        llm_failure_circuit=3,
        draft_store=InMemoryDraftResultStore(),
        tenant_id="t1",
        agent_run_id=UUID("00000000-0000-4000-8000-0000000000e3"),
        new_draft_id=lambda: UUID("00000000-0000-4000-8000-0000000000e4"),
        now=lambda: _NOW,
        #: 🔴 학생 루프 **직전**에 멈춘다 — plan 사유만 재기 위해서다.
        interrupt_before=("student",),
    )
    initial = CounselPackState(
        tenant_id="t1",
        class_ref="cl_a1",
        student_refs=list(_REFS),
        context_ref="context://11111111-1111-4111-8111-111111111111",
        context_hash=_HASH,
        plan_version="0.1",
    )
    config = {"configurable": {"thread_id": f"plan-{scenario}"}}
    asyncio.run(graph.ainvoke(initial, config=config))
    snapshot = asyncio.run(graph.aget_state(config))
    return CounselPackState.model_validate(snapshot.values)
