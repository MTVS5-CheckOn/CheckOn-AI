"""3-3·3-4 재현 — plan 경로가 비용만 쓰고 검증도 허술하다.

**3-3 강조점 미사용** — `graph.py`의 plan 노드가 planner를 호출해 `emphasis_points`를 state에
넣지만, `assemble_prompt(context)`는 그 값을 **받지 않는다.** LLM 1회 비용을 지불하고 산출을
버린다.

**불변식 ① 검증이 문자열 존재 검사뿐** — `state._RECORD_ID_RE`가 `record_id=[\\w-]+` 패턴이
있는지만 본다. LLM이 지어낸 `record_id=fake_1`도 통과하고, 위반이면 `EmptyEvidenceError`가
Pydantic `ValidationError`로 감싸여 **잡 전체를 크래시**시킨다(불변식 4 위반 — 게이트 거부는
에러가 아니다).

**3-4 실 Planner 부재 + silent fallback** — `assembly.py`가 `planner or fake`로 조용히
`FakeCounselProvider`로 넘어간다. 운영 배선 실수 = **날조 산출이 저장된다.**
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from uuid import UUID

import pytest

from ai.composition.counsel.prompt import assemble_prompt
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

_REAL_ID = "le_1029"
_FAKE_ID = "fake_1"


def _run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


def _context(*, with_record_id: bool = True) -> DraftContext:
    """근거 fact 2개 — 하나는 record_id 보유, 하나는 집계 파생(record_id 없음)."""
    facts = (
        EvidenceFact(
            label="과제 미제출",
            value="2건",
            record_id=_REAL_ID if with_record_id else None,
        ),
        EvidenceFact(label="평소 정답률(개인 기준선)", value="71%"),  # 집계 — record_id 없음
    )
    return DraftContext(
        student_ref="st_1",
        guardian_ref="gd_1",
        label_snapshot=LabelSnapshot(
            comm=CommStyle.DATA,
            sensitivity=Sensitivity.ANXIOUS,
            interest=Interest.GRADE,
            frequency=Frequency.FREQUENT,
        ),
        facts=facts,
        evidence_summaries=(),
        period_label="2026년 7월",
        fallback_text="이번 주 학습 상황을 정리해 보내드립니다.",
    )


def _execution_context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=UUID("00000000-0000-4000-8000-0000000000e1"),
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


# ── (a) record_id 계보 ────────────────────────────────────────────


def test_evidence_fact_carries_record_id() -> None:
    """`EvidenceFact`가 record_id를 보존한다 — 실존 검증의 원천(04 §4.2)."""
    fact = EvidenceFact(label="과제 미제출", value="2건", record_id=_REAL_ID)
    assert fact.record_id == _REAL_ID


def test_aggregate_fact_may_omit_record_id() -> None:
    """집계·기준선 파생 fact는 record_id가 없다 — 가짜 ID를 지어내지 않는다."""
    assert EvidenceFact(label="평소 정답률(개인 기준선)", value="71%").record_id is None


def test_context_exposes_cited_record_ids() -> None:
    """인용 가능한 record_id 집합 = record_id가 **있는** fact들만."""
    assert _context().cited_record_ids() == frozenset({_REAL_ID})


# ── (b) 실존 대조 — 날조는 드롭, 크래시 아님 ──────────────────────


def test_fabricated_record_id_is_dropped_with_reason() -> None:
    """LLM이 지어낸 record_id는 드롭되고 사유가 남는다(불변식 4 — 거부는 에러가 아니다)."""
    from ai.composition.counsel.grounding import ground_emphasis

    outcome = ground_emphasis(
        {"st_1": [f"과제 미제출 2건 (record_id={_FAKE_ID})"]},
        contexts={"st_1": _context()},
    )
    assert outcome.emphasis_points == {}
    assert outcome.drops == (("st_1", "unknown_record_id"),)


def test_real_record_id_survives() -> None:
    from ai.composition.counsel.grounding import ground_emphasis

    point = f"과제 미제출 2건 (record_id={_REAL_ID})"
    outcome = ground_emphasis({"st_1": [point]}, contexts={"st_1": _context()})
    assert outcome.emphasis_points == {"st_1": [point]}
    assert outcome.drops == ()


def test_point_without_record_id_is_dropped_with_distinct_reason() -> None:
    """'ID 없는 인용'과 '없는 ID 인용'을 구분해 기록한다."""
    from ai.composition.counsel.grounding import ground_emphasis

    outcome = ground_emphasis({"st_1": ["그냥 걱정됩니다"]}, contexts={"st_1": _context()})
    assert outcome.drops == (("st_1", "missing_record_id"),)


def test_all_bad_plan_does_not_crash_the_job() -> None:
    """전량 불량이면 **강조점 없이 진행** — state ValidationError로 잡을 죽이지 않는다."""
    from ai.composition.counsel.grounding import ground_emphasis
    from ai.composition.counsel.state import CounselPackState

    outcome = ground_emphasis(
        {"st_1": [f"과제 미제출 (record_id={_FAKE_ID})"]},
        contexts={"st_1": _context()},
    )
    state = CounselPackState(
        tenant_id="t1",
        class_ref="cl_a1",
        student_refs=["st_1"],
        context_ref="context://11111111-1111-4111-8111-111111111111",
        context_hash="sha256:" + "b" * 64,
        plan_version="0.1",
        emphasis_points=outcome.emphasis_points,  # 빈 dict — 통과해야 한다
    )
    assert state.emphasis_points == {}


# ── (c) 강조점이 프롬프트에 실린다 ────────────────────────────────


def test_emphasis_appears_in_prompt() -> None:
    """검증 통과분이 프롬프트 문면에 실제로 등장한다 — 3-3의 핵심."""
    point = f"과제 미제출 2건 (record_id={_REAL_ID})"
    prompt = assemble_prompt(_context(), emphasis=[point])
    assert point in prompt


def test_empty_emphasis_keeps_prompt_byte_identical() -> None:
    """강조점이 없으면 프롬프트가 **현행과 바이트 동일** — 24조합 골든이 흔들리지 않는다."""
    ctx = _context()
    assert assemble_prompt(ctx, emphasis=[]) == assemble_prompt(ctx)
    assert assemble_prompt(ctx, emphasis=None) == assemble_prompt(ctx)


# ── (d) plan 실패는 잡 실패가 아니다 ──────────────────────────────


def test_plan_llm_failure_proceeds_without_emphasis() -> None:
    """plan LLM이 죽어도 초안 생성은 계속된다 — plan은 부가정보다."""
    from langgraph.checkpoint.memory import InMemorySaver

    from ai.composition.counsel.graph import build_counsel_graph
    from ai.composition.counsel.provider import FakeCounselProvider
    from ai.composition.counsel.state import CounselPackState
    from ai.composition.counsel.stores import InMemoryDraftResultStore
    from ai.contracts.composition import DraftStatus
    from ai.contracts.llm import LlmUnavailable

    class _PlanBoom(FakeCounselProvider):
        async def plan(self, **kwargs: object) -> dict[str, list[str]]:
            raise LlmUnavailable("plan down")

    provider = _PlanBoom(drafts=["과제 미제출이 2건 있었습니다."] * 10)
    graph = build_counsel_graph(
        planner=provider,
        writer=provider,
        contexts={"st_1": _context()},
        execution_context=_execution_context(),
        checkpointer=InMemorySaver(),
        regen_max=3,
        llm_failure_circuit=99,
        draft_store=InMemoryDraftResultStore(),
        tenant_id="t1",
        agent_run_id=UUID("00000000-0000-4000-8000-0000000000e2"),
        new_draft_id=lambda: UUID(int=1),
        now=lambda: __import__("datetime").datetime(2026, 7, 30, tzinfo=__import__("datetime").UTC),
    )
    out = _run(graph.ainvoke(
        CounselPackState(
            tenant_id="t1",
            class_ref="cl_a1",
            student_refs=["st_1"],
            context_ref="context://11111111-1111-4111-8111-111111111111",
            context_hash="sha256:" + "b" * 64,
            plan_version="0.1",
        ),
        config={"configurable": {"thread_id": "plan-boom"}},
    ))
    assert out["emphasis_points"] == {}
    assert out["results"][0].status is DraftStatus.GENERATED  # 초안은 나왔다


def test_plan_failure_does_not_trip_llm_circuit() -> None:
    """plan 실패는 서킷 카운터에 넣지 않는다 — 서킷은 **학생 단위 write 실패** 기준."""
    from langgraph.checkpoint.memory import InMemorySaver

    from ai.composition.counsel.graph import build_counsel_graph
    from ai.composition.counsel.provider import FakeCounselProvider
    from ai.composition.counsel.state import CounselPackState
    from ai.composition.counsel.stores import InMemoryDraftResultStore
    from ai.contracts.llm import LlmUnavailable

    class _PlanBoom(FakeCounselProvider):
        async def plan(self, **kwargs: object) -> dict[str, list[str]]:
            raise LlmUnavailable("plan down")

    provider = _PlanBoom(drafts=["과제 미제출이 2건 있었습니다."] * 10)
    graph = build_counsel_graph(
        planner=provider,
        writer=provider,
        contexts={"st_1": _context()},
        execution_context=_execution_context(),
        checkpointer=InMemorySaver(),
        regen_max=3,
        llm_failure_circuit=1,  # 임계 1 — plan 실패가 카운트되면 즉시 서킷이 열린다
        draft_store=InMemoryDraftResultStore(),
        tenant_id="t1",
        agent_run_id=UUID("00000000-0000-4000-8000-0000000000e3"),
        new_draft_id=lambda: UUID(int=1),
        now=lambda: __import__("datetime").datetime(2026, 7, 30, tzinfo=__import__("datetime").UTC),
    )
    out = _run(graph.ainvoke(
        CounselPackState(
            tenant_id="t1",
            class_ref="cl_a1",
            student_refs=["st_1"],
            context_ref="context://11111111-1111-4111-8111-111111111111",
            context_hash="sha256:" + "b" * 64,
            plan_version="0.1",
        ),
        config={"configurable": {"thread_id": "plan-circuit"}},
    ))
    assert out["summary"] is not None  # 서킷이 열리지 않고 완주했다


# ── 3-4 실 Planner ───────────────────────────────────────────────


def test_gateway_planner_is_fail_closed_on_uncertain_redaction() -> None:
    """redaction 불확실이면 **전송하지 않는다**(불변식 3 · writer와 같은 규율)."""
    from ai.composition.counsel.provider import GatewayPlanner, RedactionBlockedError

    class _CountingGateway:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, request: object, context: object) -> object:
            self.calls += 1
            raise AssertionError("전송돼서는 안 된다")

    gateway = _CountingGateway()
    planner = GatewayPlanner(gateway)  # type: ignore[arg-type]

    import ai.composition.counsel.provider as provider_module
    from ai.runtime.redaction import RedactionResult

    original = provider_module.redact
    provider_module.redact = lambda _t: RedactionResult(masked_text="x", uncertain=True)  # type: ignore[assignment]
    try:
        with pytest.raises(RedactionBlockedError):
            _run(
                planner.plan(
                    contexts={"st_1": _context()},
                    student_refs=["st_1"],
                    execution_context=_execution_context(),
                )
            )
    finally:
        provider_module.redact = original  # type: ignore[assignment]
    assert gateway.calls == 0


def test_assembly_requires_explicit_planner() -> None:
    """planner 미주입이면 조용한 Fake 폴백이 아니라 **기동 실패**(날조 산출 저장 방지)."""
    import inspect

    from ai.composition.counsel.assembly import open_counsel_pack_runner

    params = inspect.signature(open_counsel_pack_runner).parameters
    assert params["planner"].default is inspect.Parameter.empty, "planner가 옵셔널이다"
    assert params["writer"].default is inspect.Parameter.empty, "writer가 옵셔널이다"
