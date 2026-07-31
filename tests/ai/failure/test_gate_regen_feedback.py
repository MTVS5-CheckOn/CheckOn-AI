"""㉙ 재생성 루프가 게이트 사유를 버린다 — 같은 프롬프트를 상한까지 반복한다.

결함(99 D ㉙ · 점검 3-8): `counsel/graph.py`의 재생성 루프와 `briefing.make_brief`가
게이트 실패 사유(`gate.reason`)를 **다음 시도 프롬프트에 넣지 않는다**. 생성 파라미터가
결정론(counsel은 temperature 0.0)이므로 같은 프롬프트 = 같은 출력 → 상한까지 같은 실패를
반복하고 **비용만 N배**가 된다(개선 0).

규정: `docs/part_a/05_tone_mapping.md` §6-2 — 직전 사유를 수정 지시 문구로 바꿔 다음 시도
프롬프트에 주입한다. 매핑은 `gate_feedback.yaml` 소유(결정론·LLM 없음)이고, **1회차는 지시
없이 조립해 문면이 종전과 바이트 동일**해야 한다(24조합 골든 불변).
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from langgraph.checkpoint.memory import InMemorySaver

from ai.composition.counsel.graph import build_counsel_graph
from ai.composition.counsel.prompt import assemble_prompt
from ai.composition.counsel.state import CounselPackState
from ai.composition.counsel.stores import InMemoryDraftResultStore
from ai.contracts.composition import (
    CommStyle,
    DraftContext,
    DraftStatus,
    EvidenceFact,
    Frequency,
    Interest,
    LabelSnapshot,
    Sensitivity,
)
from ai.contracts.execution import Capability, ExecutionContext, VersionSet

_NOW = datetime(2026, 7, 30, tzinfo=UTC)
_REGEN_MAX = 3

#: 게이트에 반드시 걸리는 본문 — 근거에 없는 숫자(불변식 1·2).
_UNGROUNDED = "정답률이 83%까지 올랐습니다."
#: 게이트를 통과하는 본문 — 컨텍스트가 제공한 수치만 쓴다.
_GROUNDED = "이번 주 정답률은 62%였습니다. 다음 주에는 오답 정리를 함께 해보겠습니다."


def _context(student_ref: str = "st_1") -> DraftContext:
    return DraftContext(
        student_ref=student_ref,
        guardian_ref=f"gd_{student_ref}",
        label_snapshot=LabelSnapshot(
            comm=CommStyle.DATA,
            sensitivity=Sensitivity.ANXIOUS,
            interest=Interest.GRADE,
            frequency=Frequency.FREQUENT,
        ),
        facts=(EvidenceFact(label="이번 주 정답률", value="62%"),),
        evidence_summaries=(),
        period_label="2026년 7월",
        fallback_text="이번 주 학습 상황을 정리해 보내드립니다.",
    )


def _execution_context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=UUID("00000000-0000-4000-8000-000000000031"),
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


class _FeedbackSensitiveWriter:
    """수정 지시를 **받았을 때만** 게이트 통과분을 낸다 — 결정론 LLM 대역.

    실 LLM의 "같은 프롬프트 → 같은 출력"(temperature 0.0)을 모사한다. 지시가 비어 있으면
    몇 번을 불러도 같은 실패 본문을 낸다 — ㉙ 결함이 있으면 상한까지 그렇게 돈다.
    """

    def __init__(self) -> None:
        self.feedbacks: list[str] = []

    async def write(
        self,
        *,
        context: DraftContext,
        execution_context: ExecutionContext,
        emphasis: Sequence[str] = (),
        gate_feedback: str = "",
    ) -> str:
        del context, execution_context, emphasis
        self.feedbacks.append(gate_feedback)
        return _GROUNDED if gate_feedback else _UNGROUNDED

    async def plan(
        self,
        *,
        contexts: Mapping[str, DraftContext],
        student_refs: Sequence[str],
        execution_context: ExecutionContext,
    ) -> dict[str, list[str]]:
        del contexts, student_refs, execution_context
        return {}  # 무강조 — 이 테스트의 축은 재생성 피드백이다


def _draft_ids() -> Callable[[], UUID]:
    box = {"n": 0}

    def _next() -> UUID:
        box["n"] += 1
        return UUID(int=box["n"])

    return _next


def _run_counsel(writer: _FeedbackSensitiveWriter) -> dict[str, Any]:
    graph = build_counsel_graph(
        planner=writer,
        writer=writer,
        contexts={"st_1": _context()},
        execution_context=_execution_context(),
        checkpointer=InMemorySaver(),
        regen_max=_REGEN_MAX,
        llm_failure_circuit=99,
        draft_store=InMemoryDraftResultStore(),
        tenant_id="t1",
        agent_run_id=UUID("00000000-0000-4000-8000-000000000032"),
        new_draft_id=_draft_ids(),
        now=lambda: _NOW,
    )
    state = CounselPackState(
        tenant_id="t1",
        class_ref="cl_a1",
        student_refs=["st_1"],
        context_ref="context://11111111-1111-4111-8111-111111111111",
        context_hash="sha256:" + "b" * 64,
        plan_version="0.1",
    )
    config = {"configurable": {"thread_id": "regen"}}
    result: dict[str, Any] = asyncio.run(graph.ainvoke(state, config=config))
    return result


# ── counsel: 사유가 다음 시도에 전달된다 ──────────────────────────


def test_counsel_regen_feeds_gate_reason_to_next_attempt() -> None:
    """🔴 ㉙ 재현 — 수정 전에는 3회 전부 같은 실패(gate_exhausted)로 소진된다."""
    writer = _FeedbackSensitiveWriter()
    final = _run_counsel(writer)

    result = final["results"][0]
    assert result.status is DraftStatus.GENERATED, result.fail_reason
    assert len(writer.feedbacks) == 2, "2회차에 통과했어야 한다 — 상한 소진이 아니다"


def test_counsel_first_attempt_gets_no_feedback() -> None:
    """1회차는 지시 없이 조립한다 — 프롬프트 바이트 동일의 전제(05 §6-2)."""
    writer = _FeedbackSensitiveWriter()
    _run_counsel(writer)
    assert writer.feedbacks[0] == ""


def test_counsel_feedback_names_the_actual_failure() -> None:
    """빈 문자열만 아니면 되는 게 아니다 — 직전 사유가 문면에 반영돼야 한다."""
    writer = _FeedbackSensitiveWriter()
    _run_counsel(writer)
    assert "83" in writer.feedbacks[1], writer.feedbacks[1]


# ── 1회차 프롬프트 바이트 동일 (24조합 골든 보호) ─────────────────


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_empty_feedback_keeps_prompt_byte_identical() -> None:
    """지시가 비면 조립 결과가 종전과 **바이트 동일**하다(sha256 대조)."""
    context = _context()
    assert _sha(assemble_prompt(context)) == _sha(assemble_prompt(context, (), ""))


def test_feedback_changes_the_prompt() -> None:
    """역방향 — 지시가 있으면 문면이 달라져야 한다(주입이 실제로 일어난다는 증거)."""
    context = _context()
    with_feedback = assemble_prompt(context, (), "직전 시도의 숫자를 고치세요.")
    assert _sha(with_feedback) != _sha(assemble_prompt(context))
    assert "직전 시도의 숫자를 고치세요." in with_feedback
