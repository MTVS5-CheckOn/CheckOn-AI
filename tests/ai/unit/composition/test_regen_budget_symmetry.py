"""초안 경로와 다듬기 경로가 **같은 `regen_max`를 같게 해석한다**.

🔴 **이 파일이 있는 이유가 곧 이번 결함이다.** 두 루프가 같은 상수를 받는데
(`routers/counsel.py`의 `_REGEN_MAX` 하나가 `open_counsel_pack_runner`와 `refine_draft`
양쪽으로 간다) 해석이 갈렸다 — 초안은 재생성 3회, 다듬기는 2회. #110이 `graph.py`만
고쳤을 때 **CI는 초록이었다.** 대칭을 보는 테스트가 없었기 때문이다.

여기서는 두 경로를 **같은 입력으로 나란히 돌려 시도 수를 비교**한다. 한쪽만 고치면 red다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from ai.api.routers.counsel import _REGEN_MAX
from ai.composition.counsel.graph import build_counsel_graph
from ai.composition.counsel.refine import refine_draft
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

#: 근거(62%)에 없는 수치 — 게이트가 **매번** 막아 예산을 끝까지 쓰게 만든다.
_UNGROUNDED = "정답률이 88%까지 올랐습니다."

_NOW = datetime(2026, 8, 6, tzinfo=UTC)


class _CountingWriter:
    """호출 횟수만 세는 대역 — plan·write 둘 다 만족한다(두 경로가 같은 객체를 받는다)."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.calls = 0

    async def plan(self, **_kwargs: object) -> dict[str, list[str]]:
        return {}

    async def write(
        self,
        *,
        context: DraftContext,
        execution_context: ExecutionContext,
        emphasis: Sequence[str] = (),
        gate_feedback: str = "",
        refine_instruction: str = "",
        previous_text: str = "",
    ) -> str:
        del context, execution_context, emphasis, gate_feedback, refine_instruction
        self.calls += 1
        return self._text


def _context() -> DraftContext:
    return DraftContext(
        student_ref="st_1",
        guardian_ref="gd_1",
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
        execution_id=UUID("00000000-0000-4000-8000-00000000001a"),
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


def _draft_path_attempts(regen_max: int, *, thread: str) -> int:
    """초안 경로(`graph.py`)가 실제로 writer를 몇 번 부르는가."""
    writer = _CountingWriter(_UNGROUNDED)
    ids = iter(UUID(int=n) for n in range(1, 99))
    graph = build_counsel_graph(
        planner=writer,
        writer=writer,
        contexts={"st_1": _context()},
        execution_context=_execution_context(),
        checkpointer=InMemorySaver(),
        regen_max=regen_max,
        llm_failure_circuit=99,
        draft_store=InMemoryDraftResultStore(),
        tenant_id="t1",
        agent_run_id=UUID("00000000-0000-4000-8000-00000000001b"),
        new_draft_id=lambda: next(ids),
        now=lambda: _NOW,
    )
    state = CounselPackState(
        tenant_id="t1",
        class_ref="cl_1",
        student_refs=["st_1"],
        context_ref="context://11111111-1111-4111-8111-111111111111",
        context_hash="sha256:" + "0" * 64,
        plan_version="0.1",
    )
    asyncio.run(graph.ainvoke(state, config={"configurable": {"thread_id": thread}}))
    return writer.calls


def _refine_path_attempts(regen_max: int) -> int:
    """다듬기 경로(`refine.py`)가 실제로 writer를 몇 번 부르는가."""
    writer = _CountingWriter(_UNGROUNDED)
    outcome = asyncio.run(
        refine_draft(
            context=_context(),
            instruction="조금 더 부드럽게 써줘",  # A 분류(스타일) — 사전 차단에 안 걸린다
            writer=writer,
            execution_context=_execution_context(),
            regen_max=regen_max,
        )
    )
    assert not outcome.applied, "게이트가 막아야 예산을 끝까지 쓴다"
    return writer.calls


@pytest.mark.parametrize("regen_max", [1, 2, 3])
def test_both_paths_spend_the_same_budget(regen_max: int) -> None:
    """🔴 **이번 누락을 막는 장치.** 한쪽 루프만 고치면 여기서 red가 난다.

    #110이 `graph.py`의 off-by-one을 고치고 `refine.py`를 빠뜨렸을 때 CI가 초록이었던
    이유는 두 경로를 나란히 보는 테스트가 없었기 때문이다.
    """
    draft = _draft_path_attempts(regen_max, thread=f"sym{regen_max}")
    refine = _refine_path_attempts(regen_max)
    assert draft == refine, (
        f"regen_max={regen_max}인데 초안 {draft}회 · 다듬기 {refine}회 — "
        "같은 상수를 두 경로가 다르게 해석한다"
    )


@pytest.mark.parametrize("regen_max", [1, 2, 3])
def test_budget_means_n_regenerations_not_n_attempts(regen_max: int) -> None:
    """관례 고정 — **재생성 N회 = 시도 N+1회**(`classify`의 `MAX_PARSE_RETRY + 1` 선례).

    대칭만 보면 둘 다 틀린 값(N회)으로 맞춰도 통과한다 — 절대값도 함께 못 박는다.
    """
    assert _draft_path_attempts(regen_max, thread=f"abs{regen_max}") == regen_max + 1
    assert _refine_path_attempts(regen_max) == regen_max + 1


def test_the_router_feeds_one_constant_to_both_paths() -> None:
    """두 경로가 **같은 상수**를 받는다는 것이 대칭 요구의 근거다.

    값(3)을 고정하는 게 아니라 *하나를 공유한다*는 사실을 남긴다 — 값이 바뀌어도 이 테스트는
    살아야 하고, 두 경로가 서로 다른 상수를 받게 되면 그때는 대칭 요구 자체를 다시 봐야 한다.
    """
    import ast
    import inspect
    from pathlib import Path

    from ai.api.routers import counsel as router

    tree = ast.parse(Path(inspect.getfile(router)).read_text(encoding="utf-8"))
    fed = [
        node.value.id
        for node in ast.walk(tree)
        if isinstance(node, ast.keyword)
        and node.arg == "regen_max"
        and isinstance(node.value, ast.Name)
    ]
    assert len(fed) == 2, f"regen_max 주입 지점이 2곳이 아니다: {fed}"
    assert set(fed) == {"_REGEN_MAX"}, fed


@pytest.mark.parametrize("regen_max", [0, -1])
def test_zero_budget_is_refused_on_both_paths(regen_max: int) -> None:
    """🔴 예산 0은 **시도 0회**다 — LLM을 한 번도 안 부르고 "상한 소진"으로 나간다.

    ⚠ 거부 지점이 경로마다 다르다(초안=조립 시점 · 다듬기=함수 진입). refine에는 조립
    단계가 없어 라우터가 매 턴 직접 부르기 때문이고, **"값이 처음 들어오는 곳"** 이라는
    규칙은 같다.
    """
    writer = _CountingWriter(_UNGROUNDED)
    with pytest.raises(ValueError, match="regen_max"):
        _draft_path_attempts(regen_max, thread=f"zero{regen_max}")
    with pytest.raises(ValueError, match="regen_max"):
        asyncio.run(
            refine_draft(
                context=_context(),
                instruction="조금 더 부드럽게 써줘",
                writer=writer,
                execution_context=_execution_context(),
                regen_max=regen_max,
            )
        )
    assert writer.calls == 0, "거부 전에 LLM을 불렀다"


def test_symmetry_survives_a_budget_change() -> None:
    """값(3)을 바꿔도 대칭은 유지된다 — 이 PR은 값이 아니라 **해석**을 맞춘 것이다."""
    assert _REGEN_MAX >= 1
    for budget in (_REGEN_MAX, _REGEN_MAX + 2):
        assert _draft_path_attempts(budget, thread=f"chg{budget}") == _refine_path_attempts(
            budget
        )
