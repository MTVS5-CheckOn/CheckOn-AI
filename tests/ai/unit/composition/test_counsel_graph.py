"""counsel_pack 그래프 — 학생 루프·게이트 재생성·부분 실패·체크포인트 재개 (§1.1·§1.3).

FakeCounselProvider + InMemorySaver로 결정론화. 실 LLM·PG 없음.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from counsel_text import draft
from langgraph.checkpoint.memory import InMemorySaver

from ai.composition.counsel.graph import build_counsel_graph, summarize
from ai.composition.counsel.provider import FakeCounselProvider, RedactionBlockedError
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
    StudentResult,
)
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import LlmTimeout

_HASH = "sha256:" + "b" * 64
_NOW = datetime(2026, 7, 30, tzinfo=UTC)


def _draft_ids() -> Callable[[], UUID]:
    """결정론 draft_id 발급기 — 기본 카운터가 제거됐으므로 테스트가 주입한다."""
    box = {"n": 0}

    def _next() -> UUID:
        box["n"] += 1
        return UUID(int=box["n"])

    return _next
_REGEN_MAX = 3


def _context(student_ref: str) -> DraftContext:
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
        execution_id=UUID("00000000-0000-4000-8000-00000000000d"),
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


def _state(refs: list[str]) -> CounselPackState:
    return CounselPackState(
        tenant_id="t1",
        class_ref="cl_a1",
        student_refs=refs,
        context_ref="context://11111111-1111-4111-8111-111111111111",
        context_hash=_HASH,
        plan_version="0.1",
    )


def _run(
    refs: list[str],
    provider: FakeCounselProvider,
    *,
    contexts: dict[str, DraftContext] | None = None,
    interrupt: tuple[str, ...] = (),
    thread: str = "t",
) -> tuple[Any, dict[str, Any]]:
    graph = build_counsel_graph(
        planner=provider,
        writer=provider,
        contexts=contexts if contexts is not None else {r: _context(r) for r in refs},
        execution_context=_execution_context(),
        checkpointer=InMemorySaver(),
        regen_max=_REGEN_MAX,
        llm_failure_circuit=99,
        draft_store=InMemoryDraftResultStore(),
        tenant_id="t1",
        agent_run_id=UUID("00000000-0000-4000-8000-00000000000e"),
        new_draft_id=_draft_ids(),
        now=lambda: _NOW,
        interrupt_before=interrupt,
    )
    config = {"configurable": {"thread_id": thread}}
    return graph, asyncio.run(graph.ainvoke(_state(refs), config=config))


# ── §1.1 학생 루프 ────────────────────────────────────────────────


def test_all_students_processed_in_order() -> None:
    """student_refs 순서 고정(재현성) — cursor가 끝까지 간다."""
    provider = FakeCounselProvider(drafts=[draft("정답률은 62%였습니다. 함께 살펴보겠습니다.")])
    _graph, out = _run(["st_1", "st_2", "st_3"], provider)
    assert out["cursor"] == 3
    assert [r.student_ref for r in out["results"]] == ["st_1", "st_2", "st_3"]
    assert all(r.status is DraftStatus.GENERATED for r in out["results"])


def test_plan_runs_once_and_emits_record_id_points() -> None:
    provider = FakeCounselProvider(drafts=[draft("정답률은 62%였습니다.")])
    _graph, out = _run(["st_1", "st_2"], provider)
    assert len(provider.plan_calls) == 1  # plan은 LLM 1회
    for points in out["emphasis_points"].values():
        assert all("record_id=" in p for p in points)


def test_summary_counts_match_results() -> None:
    provider = FakeCounselProvider(drafts=[draft("정답률은 62%였습니다.")])
    _graph, out = _run(["st_1", "st_2"], provider)
    assert out["summary"] == "2명 중 2명 생성·0명 데이터 부족·0명 실패"


def test_summarize_is_pure() -> None:
    results = [
        StudentResult(student_ref="a", status=DraftStatus.GENERATED),
        StudentResult(student_ref="b", status=DraftStatus.REJECTED_INSUFFICIENT),
        StudentResult(student_ref="c", status=DraftStatus.FAILED),
    ]
    assert summarize(results) == "3명 중 1명 생성·1명 데이터 부족·1명 실패"


# ── 불변식 ③ 학생 1명 실패가 루프를 멈추지 않는다 ──────────────────


def test_llm_failure_records_and_continues() -> None:
    provider = FakeCounselProvider(
        drafts=[LlmTimeout(draft("느림")), draft("정답률은 62%였습니다.")]
    )
    _graph, out = _run(["st_1", "st_2"], provider)
    assert out["cursor"] == 2  # 멈추지 않았다
    assert out["results"][0].status is DraftStatus.FAILED
    assert out["results"][0].fail_reason == "llm_failed:LlmTimeout"
    assert out["results"][1].status is DraftStatus.GENERATED


def test_redaction_blocked_is_fail_closed_and_continues() -> None:
    """마스킹 불확실 → 미전송·실패 기록. 루프는 계속(불변식 3·③)."""
    provider = FakeCounselProvider(
        drafts=[RedactionBlockedError(draft("불확실")), draft("정답률은 62%였습니다.")]
    )
    _graph, out = _run(["st_1", "st_2"], provider)
    assert out["results"][0].fail_reason == "redaction_blocked"
    assert out["results"][1].status is DraftStatus.GENERATED


def test_missing_context_is_insufficient_not_failure() -> None:
    """컨텍스트 부재는 데이터 부족(정상 상태) — error_codes §2.1."""
    provider = FakeCounselProvider(drafts=[draft("정답률은 62%였습니다.")])
    _graph, out = _run(
        ["st_1", "st_2"], provider, contexts={"st_2": _context("st_2")}
    )
    assert out["results"][0].status is DraftStatus.REJECTED_INSUFFICIENT
    assert out["results"][0].fail_reason == "context_missing"
    assert out["results"][1].status is DraftStatus.GENERATED


# ── 게이트 재생성 상한 (불변식 6 · ERD "≤3") ──────────────────────


def test_gate_failure_regenerates_up_to_cap_then_records_failure() -> None:
    """근거에 없는 수치는 계속 실패 → **재생성 regen_max회 = 시도 regen_max+1회** 후 failed.

    🔴 종전 기대값은 `== _REGEN_MAX`(시도 3 = 재생성 2)였다. 그건 `range(regen_max)`의
    off-by-one을 그대로 굳힌 것이고, 예산을 1회 깎아 ERD `DRAFT_BLOCK.regen_count`(le=3)의
    3을 **도달 불가능한 값**으로 만들었다. `classify`의 `range(MAX_PARSE_RETRY + 1)`과
    같은 관례로 맞췄다.
    """
    provider = FakeCounselProvider(drafts=[draft("정답률이 88%까지 올랐습니다.")])
    _graph, out = _run(["st_1"], provider)
    assert len(provider.write_calls) == _REGEN_MAX + 1
    assert out["results"][0].status is DraftStatus.FAILED
    assert out["results"][0].fail_reason.startswith("gate_exhausted:ungrounded_number")


def test_regen_budget_is_n_regenerations_not_n_attempts() -> None:
    """🔴 관례 고정 — **재생성 N회 = 시도 N+1회**(`classify`의 `MAX_PARSE_RETRY + 1` 선례).

    ERD `DRAFT_BLOCK.regen_count`가 `le=3`인데 시도가 3회면 재생성은 2회라 **3은 영영
    안 나온다.** 상한 값과 원장 컬럼의 뜻이 갈리면 어느 쪽이 계약인지 알 수 없다.
    """
    for budget in (1, 2, 3):
        provider = FakeCounselProvider(drafts=[draft("정답률이 88%까지 올랐습니다.")])
        graph = build_counsel_graph(
            planner=provider,
            writer=provider,
            contexts={"st_1": _context("st_1")},
            execution_context=_execution_context(),
            checkpointer=InMemorySaver(),
            regen_max=budget,
            llm_failure_circuit=99,
            draft_store=InMemoryDraftResultStore(),
            tenant_id="t1",
            agent_run_id=UUID("00000000-0000-4000-8000-00000000000e"),
            new_draft_id=_draft_ids(),
            now=lambda: _NOW,
        )
        asyncio.run(
            graph.ainvoke(_state(["st_1"]), config={"configurable": {"thread_id": f"b{budget}"}})
        )
        assert len(provider.write_calls) == budget + 1, f"regen_max={budget}"


def test_zero_regen_budget_is_refused_at_build_time() -> None:
    """🔴 `regen_max=0`이면 시도가 0회다 — LLM을 한 번도 안 부르고 `gate_exhausted`로 나간다.

    게이트가 막은 것처럼 보이지만 아무것도 생성하지 않은 것이라 사후 진단이 거짓이 된다.
    `gateway.py`가 `transport_retry`에 "0..1 밖이면 기동 실패"를 건 것과 같은 자리다(불변식 6).
    """
    provider = FakeCounselProvider(drafts=[draft("정답률은 62%였습니다.")])
    with pytest.raises(ValueError, match="regen_max"):
        build_counsel_graph(
            planner=provider,
            writer=provider,
            contexts={"st_1": _context("st_1")},
            execution_context=_execution_context(),
            checkpointer=InMemorySaver(),
            regen_max=0,
            llm_failure_circuit=99,
            draft_store=InMemoryDraftResultStore(),
            tenant_id="t1",
            agent_run_id=UUID("00000000-0000-4000-8000-00000000000e"),
            new_draft_id=_draft_ids(),
            now=lambda: _NOW,
        )
    assert provider.write_calls == [], "거부 전에 LLM을 불렀다"


def test_gate_pass_on_second_attempt_stops_regenerating() -> None:
    provider = FakeCounselProvider(
        drafts=[draft("정답률이 88%까지 올랐습니다."), draft("정답률은 62%였습니다.")]
    )
    _graph, out = _run(["st_1"], provider)
    assert len(provider.write_calls) == 2  # 통과 즉시 중단
    assert out["results"][0].status is DraftStatus.GENERATED


# ── §1.3 체크포인트·재개 (학생 경계) ──────────────────────────────


def test_resume_from_student_boundary_does_not_regenerate() -> None:
    """학생 경계에서 중단→재개. 이미 만든 draft를 다시 만들지 않는다(멱등)."""
    provider = FakeCounselProvider(drafts=[draft("정답률은 62%였습니다.")])
    graph = build_counsel_graph(
        planner=provider,
        writer=provider,
        contexts={r: _context(r) for r in ["st_1", "st_2"]},
        execution_context=_execution_context(),
        checkpointer=InMemorySaver(),
        regen_max=_REGEN_MAX,
        llm_failure_circuit=99,
        draft_store=InMemoryDraftResultStore(),
        tenant_id="t1",
        agent_run_id=UUID("00000000-0000-4000-8000-00000000000e"),
        new_draft_id=_draft_ids(),
        now=lambda: _NOW,
        interrupt_before=("summarize",),
    )
    config = {"configurable": {"thread_id": "resume"}}
    first = asyncio.run(graph.ainvoke(_state(["st_1", "st_2"]), config=config))
    assert first.get("summary") is None  # summarize 앞에서 중단
    assert first["cursor"] == 2
    writes_before = len(provider.write_calls)

    resumed = asyncio.run(graph.ainvoke(None, config=config))
    assert resumed["summary"] == "2명 중 2명 생성·0명 데이터 부족·0명 실패"
    assert len(provider.write_calls) == writes_before  # 재생성 없음


def test_checkpoint_is_per_student() -> None:
    """체크포인트 단위 = 학생 1명 완료(§1.3) — 중간 상태가 남지 않는다."""
    provider = FakeCounselProvider(drafts=[draft("정답률은 62%였습니다.")])
    graph = build_counsel_graph(
        planner=provider,
        writer=provider,
        contexts={r: _context(r) for r in ["st_1", "st_2", "st_3"]},
        execution_context=_execution_context(),
        checkpointer=InMemorySaver(),
        regen_max=_REGEN_MAX,
        llm_failure_circuit=99,
        draft_store=InMemoryDraftResultStore(),
        tenant_id="t1",
        agent_run_id=UUID("00000000-0000-4000-8000-00000000000e"),
        new_draft_id=_draft_ids(),
        now=lambda: _NOW,
        interrupt_before=("student",),
    )
    config = {"configurable": {"thread_id": "cp"}}
    asyncio.run(graph.ainvoke(_state(["st_1", "st_2", "st_3"]), config=config))
    snapshot = graph.get_state(config)
    # 중단 시점의 cursor는 항상 results 길이와 같다(불변식 ②).
    assert snapshot.values["cursor"] == len(snapshot.values["results"])


def test_graph_is_deterministic() -> None:
    def run() -> str:
        provider = FakeCounselProvider(drafts=[draft("정답률은 62%였습니다.")])
        _graph, out = _run(["st_1", "st_2"], provider, thread="det")
        return str(out["summary"])

    assert run() == run()
