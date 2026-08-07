"""counsel_pack WorkerJob 어댑터·저장소·조립부 — §5 fencing · 부분 미해결 succeeded.

Supervisor(InMemoryJobStore) + InMemorySaver + 인메모리 저장소로 결정론화. 실 LLM·PG 없음.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from counsel_text import draft
from langgraph.checkpoint.memory import InMemorySaver

from ai.agents.job_store import InMemoryJobStore, StaleLeaseError
from ai.agents.supervisor import Supervisor
from ai.composition.counsel.assembly import (
    COUNSELOR_TRANSPORT_RETRY,
    DEFAULT_REGEN_MAX,
    build_counsel_gateway,
)
from ai.composition.counsel.enqueue import CounselPackEnqueuer, content_hash
from ai.composition.counsel.provider import FakeCounselLlmProvider, FakeCounselProvider
from ai.composition.counsel.stores import (
    AgentStepRecord,
    InMemoryAgentStepSink,
    InMemoryContextStore,
    InMemoryDraftResultStore,
    InMemoryPackResultStore,
    make_ref,
    parse_ref,
)
from ai.composition.counsel.worker import CounselPackRunner
from ai.contracts.agents import (
    JobPhase,
    OperationKind,
    PriorityClass,
    WorkerJob,
    WorkerKind,
)
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
from ai.contracts.llm import LLMRequest, ModelRole
from ai.db.models import AgentStep

_NOW = datetime(2026, 7, 30, tzinfo=UTC)


def _run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


def _counter() -> Callable[[], UUID]:
    state = {"n": 0}

    def _next() -> UUID:
        state["n"] += 1
        return UUID(int=state["n"])

    return _next


def _context(student_ref: str, *, with_facts: bool = True) -> DraftContext:
    return DraftContext(
        student_ref=student_ref,
        guardian_ref=f"gd_{student_ref}",
        label_snapshot=LabelSnapshot(
            comm=CommStyle.DATA,
            sensitivity=Sensitivity.ANXIOUS,
            interest=Interest.GRADE,
            frequency=Frequency.FREQUENT,
        ),
        facts=(EvidenceFact(label="이번 주 정답률", value="62%"),) if with_facts else (),
        evidence_summaries=(),
        period_label="2026년 7월",
        fallback_text="이번 주 학습 상황을 정리해 보내드립니다.",
    )


def _supervisor() -> Supervisor:
    return Supervisor(
        store=InMemoryJobStore(),
        lease_duration=timedelta(minutes=5),
        priority_aging_interval=timedelta(minutes=1),
        clock=lambda: _NOW,
    )


# ── 저장소: ref 형식·ORM 1:1 ──────────────────────────────────────


def test_ref_scheme_follows_probe_precedent() -> None:
    """probe의 profile://·spec:// 선례 — context://·draft://."""
    key = UUID(int=7)
    assert make_ref("context", key) == f"context://{key}"
    assert parse_ref(f"context://{key}", "context") == key
    with pytest.raises(ValueError, match="context 참조가 아님"):
        parse_ref(f"draft://{key}", "context")


def test_agent_step_record_is_1to1_with_orm() -> None:
    """probe와 두 곳에 정의된 타입이 갈리지 않게 ORM 컬럼과 대조(99 D 후속 안건)."""
    assert set(AgentStepRecord.model_fields) == set(AgentStep.__table__.columns.keys())


# ── 조립부: provider 등록(머지 조건 ②)·전송 재시도 ────────────────


def test_gateway_registers_counselor_role() -> None:
    """등록이 빠지면 첫 호출에서 LookupError — B가 지적한 머지 조건 ②."""
    gateway = build_counsel_gateway(FakeCounselLlmProvider())
    request = LLMRequest(
        role=ModelRole.COUNSELOR, prompt="p", prompt_id="composition/counsel_pack",
        prompt_version="0.1",
    )
    assert gateway._providers.get(request.role) is not None  # noqa: SLF001


def test_transport_retry_is_zero_for_counselor() -> None:
    """결정론 폴백이 있으므로 무재시도 — 조립부가 주입(브리핑 narrator와 동일)."""
    assert COUNSELOR_TRANSPORT_RETRY == 0
    gateway = build_counsel_gateway(FakeCounselLlmProvider())
    assert gateway._attempts_for(ModelRole.COUNSELOR) == 1  # noqa: SLF001


def test_regen_max_default_matches_erd_cap() -> None:
    assert DEFAULT_REGEN_MAX == 3


# ── enqueue → run_next ────────────────────────────────────────────


def _harness(
    refs: list[str], *, provider: FakeCounselProvider | None = None
) -> tuple[Supervisor, CounselPackRunner, InMemoryContextStore, InMemoryAgentStepSink]:
    supervisor = _supervisor()
    contexts = InMemoryContextStore()
    sink = InMemoryAgentStepSink()
    fake = provider or FakeCounselProvider(drafts=[draft("정답률은 62%였습니다.")])
    runner = CounselPackRunner(
        supervisor=supervisor,
        context_store=contexts,
        draft_store=InMemoryDraftResultStore(),
        pack_store=InMemoryPackResultStore(),
        step_sink=sink,
        planner=fake,
        writer=fake,
        checkpointer=InMemorySaver(),
        regen_max=DEFAULT_REGEN_MAX,
        lease_owner="worker-1",
        new_id=_counter(),
    )
    return supervisor, runner, contexts, sink


def test_enqueue_then_run_succeeds() -> None:
    refs = ["st_1", "st_2"]
    supervisor, runner, contexts, _sink = _harness(refs)
    enqueuer = CounselPackEnqueuer(
        supervisor=supervisor, context_store=contexts, new_id=_counter(), now=lambda: _NOW
    )

    async def scenario() -> WorkerJob | None:
        job = await enqueuer.enqueue(
            tenant_id="t1", class_ref="cl_a1", contexts={r: _context(r) for r in refs}
        )
        assert job.operation is OperationKind.COUNSEL_PACK_GENERATE
        assert job.worker_kind is WorkerKind.COUNSEL_PACK
        assert job.priority_class is PriorityClass.BATCH
        assert job.payload_ref.startswith("context://")
        return await runner.run_next(tenant_id="t1")

    done = _run(scenario())
    assert done is not None
    assert done.phase is JobPhase.SUCCEEDED
    assert done.result_ref is not None


def test_partial_failure_still_succeeds() -> None:
    """"N명 중 M명 생성"은 failed가 아니라 succeeded + summary(error_codes §2.5)."""
    refs = ["st_1", "st_2"]
    provider = FakeCounselProvider(drafts=[draft("정답률이 88%까지 올랐습니다.")])  # 게이트 소진
    supervisor, runner, contexts, sink = _harness(refs, provider=provider)
    enqueuer = CounselPackEnqueuer(
        supervisor=supervisor, context_store=contexts, new_id=_counter(), now=lambda: _NOW
    )

    async def scenario() -> WorkerJob | None:
        await enqueuer.enqueue(
            tenant_id="t1", class_ref="cl_a1", contexts={r: _context(r) for r in refs}
        )
        return await runner.run_next(tenant_id="t1")

    done = _run(scenario())
    assert done is not None
    assert done.phase is JobPhase.SUCCEEDED  # 도메인 실패 ≠ 실행 실패
    steps = _run(sink.steps(done.job_id))
    assert [s.outcome for s in steps] == [DraftStatus.FAILED.value] * 2


def test_agent_steps_persisted_with_job_id() -> None:
    """agent_run_id = job_id (AGENT_STEP → AGENT_RUN.id, §5 1:1 투영)."""
    refs = ["st_1", "st_2"]
    supervisor, runner, contexts, sink = _harness(refs)
    enqueuer = CounselPackEnqueuer(
        supervisor=supervisor, context_store=contexts, new_id=_counter(), now=lambda: _NOW
    )

    async def scenario() -> WorkerJob | None:
        await enqueuer.enqueue(
            tenant_id="t1", class_ref="cl_a1", contexts={r: _context(r) for r in refs}
        )
        return await runner.run_next(tenant_id="t1")

    done = _run(scenario())
    assert done is not None
    steps = _run(sink.steps(done.job_id))
    assert [s.seq for s in steps] == [0, 1]
    assert all(s.node_name == "student" for s in steps)
    # 마스킹 통과분만 — alias 외 본문 없음
    assert all(set(s.tool_args_masked) == {"student_ref"} for s in steps)


def test_empty_queue_returns_none() -> None:
    _supervisor_, runner, _contexts, _sink = _harness(["st_1"])
    assert _run(runner.run_next(tenant_id="t1")) is None


# ── §5.1 fencing ─────────────────────────────────────────────────


def test_stale_lease_generation_is_fenced() -> None:
    refs = ["st_1"]
    supervisor, _runner, contexts, _sink = _harness(refs)
    enqueuer = CounselPackEnqueuer(
        supervisor=supervisor, context_store=contexts, new_id=_counter(), now=lambda: _NOW
    )

    async def scenario() -> None:
        await enqueuer.enqueue(
            tenant_id="t1", class_ref="cl_a1", contexts={r: _context(r) for r in refs}
        )
        leased = await supervisor.lease_next(
            tenant_id="t1", worker_kind=WorkerKind.COUNSEL_PACK, lease_owner="worker-1"
        )
        assert leased is not None and leased.lease_generation == 1
        with pytest.raises(StaleLeaseError):
            await supervisor.succeed(
                tenant_id="t1", job_id=leased.job_id, lease_owner="worker-1",
                lease_generation=999, result_ref="context://x",
            )

    _run(scenario())


# ── 불변식 ④ context_hash ─────────────────────────────────────────


def test_content_hash_is_deterministic_and_order_independent() -> None:
    a = {"st_1": _context("st_1"), "st_2": _context("st_2")}
    b = {"st_2": _context("st_2"), "st_1": _context("st_1")}
    assert content_hash(a) == content_hash(b)
    assert content_hash(a).startswith("sha256:")


def test_content_hash_changes_with_context() -> None:
    a = {"st_1": _context("st_1")}
    b = {"st_1": _context("st_1", with_facts=False)}
    assert content_hash(a) != content_hash(b)
