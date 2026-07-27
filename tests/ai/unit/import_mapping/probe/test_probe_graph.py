"""mapping_probe LangGraph — 수렴·상한 도달·재개·불변식 (langgraph_state §2).

FakeProbeTools·FakeProbePlanner·InMemorySaver로 결정론화. 실 LLM·PG 없이 그래프 계약을 고정.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from langgraph.checkpoint.memory import InMemorySaver

from ai.import_mapping.probe.graph import build_probe_graph
from ai.import_mapping.probe.planner import FakeProbePlanner
from ai.import_mapping.probe.state import MappingProbeState
from ai.import_mapping.probe.tools import FakeProbeTools
from ai.import_mapping.profiling import ColumnProfile, SheetProfile, SourceProfile

_PID = UUID("00000000-0000-4000-8000-000000000001")


def _profile(headers: list[str]) -> SourceProfile:
    cols = tuple(
        ColumnProfile(
            name=h, n_total=1, n_null=0, n_unique=1, dtype_guess="string", suspect_pii=False
        )
        for h in headers
    )
    return SourceProfile(filename="r.xlsx", sheets=(SheetProfile("s", 1, cols, ()),))


def _run(
    headers: list[str], *, loop_max: int, tid: str, interrupt: tuple[str, ...] = ()
) -> Any:  # noqa: ANN401 — LangGraph 컴파일 그래프 제네릭이 버전별로 달라 Any
    profile = _profile(headers)
    graph = build_probe_graph(
        tools=FakeProbeTools(profile),
        planner=FakeProbePlanner(),
        loop_max=loop_max,
        checkpointer=InMemorySaver(),
        interrupt_before=interrupt,
    )
    state = MappingProbeState(
        tenant_id="t1", source_profile_id=_PID, sheets_meta={"columns": headers}
    )
    config = {"configurable": {"thread_id": tid}}
    return graph, graph.invoke(state, config=config), config


_ROSTER = ["원생명", "반", "등원일", "상태", "동의"]


def test_converges_all_resolved_under_cap() -> None:
    _g, out, _c = _run(_ROSTER, loop_max=6, tid="conv")
    draft = out["spec_draft"]
    assert len(draft.resolved) == 5 and len(draft.unresolved) == 0
    assert out["loop_count"] == 5  # 후보 소진으로 조기 수렴(상한 미도달)
    assert draft.overall_confidence > 0.8


def test_no_rule_column_is_unresolved_not_forced() -> None:
    """규칙 없는 컬럼은 억지 매핑 없이 '모름'(사유 필수)."""
    _g, out, _c = _run([*_ROSTER, "비고"], loop_max=8, tid="nr")
    draft = out["spec_draft"]
    unresolved = {u.source: u.reason for u in draft.unresolved}
    assert "비고" in unresolved and unresolved["비고"]


def test_loop_cap_forces_remaining_unresolved() -> None:
    """상한 도달 시 미조사 컬럼 전부 unresolved(불변식 ①)."""
    _g, out, _c = _run(_ROSTER, loop_max=2, tid="cap")
    draft = out["spec_draft"]
    assert out["loop_count"] == 2
    assert len(draft.resolved) == 2 and len(draft.unresolved) == 3
    assert all("상한" in u.reason for u in draft.unresolved)


def test_column_count_invariant() -> None:
    """최종 spec 컬럼 수 = resolved + unresolved = 전체(불변식 ③)."""
    _g, out, _c = _run([*_ROSTER, "비고"], loop_max=3, tid="cnt")
    draft = out["spec_draft"]
    assert len(draft.resolved) + len(draft.unresolved) == 6


def test_checkpoint_resume_matches_uninterrupted() -> None:
    """propose_spec 앞에서 중단→재개해도 결과 동일(체크포인트 재개, §2.3)."""
    graph, first, config = _run(_ROSTER, loop_max=6, tid="resume", interrupt=("propose_spec",))
    # 중단 지점: 조사 루프는 돌았고 spec_draft는 아직 없음.
    assert first.get("spec_draft") is None
    snapshot = graph.get_state(config)
    assert snapshot.values["loop_count"] == 5
    resumed = graph.invoke(None, config=config)  # 재개
    assert resumed["spec_draft"] is not None
    assert len(resumed["spec_draft"].resolved) == 5
