"""mapping_probe WorkerJob 어댑터 — lease→start→succeed·agent_step·부분미해결·fencing (§5).

Supervisor(InMemoryJobStore)+InMemorySaver+인메모리 저장소로 결정론화. 실 PG·LLM 없음.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from ai.agents.job_store import InMemoryJobStore, StaleLeaseError
from ai.agents.supervisor import Supervisor
from ai.contracts.agents import (
    JobPhase,
    OperationKind,
    PriorityClass,
    WorkerJob,
    WorkerKind,
)
from ai.import_mapping.probe.stores import (
    InMemoryAgentStepSink,
    InMemoryProfileStore,
    InMemorySpecResultStore,
    ProfileRecord,
    serialize_profile,
)
from ai.import_mapping.probe.worker import MappingProbeRunner
from ai.import_mapping.profiling import ColumnProfile, SheetProfile, SourceProfile

_NOW = datetime(2026, 7, 27, tzinfo=UTC)
_HASH = "sha256:" + "0" * 64


def _run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


def _counter() -> Callable[[], UUID]:
    state = {"n": 0}

    def _next() -> UUID:
        state["n"] += 1
        return UUID(int=state["n"])

    return _next


def _profile(headers: list[str]) -> SourceProfile:
    cols = tuple(
        ColumnProfile(
            name=h, n_total=1, n_null=0, n_unique=1, dtype_guess="string",
            suspect_pii=(h == "원생명"),
        )
        for h in headers
    )
    return SourceProfile(filename="r.xlsx", sheets=(SheetProfile("s", 1, cols, ()),))


_Harness = tuple[
    Supervisor,
    MappingProbeRunner,
    InMemoryProfileStore,
    InMemorySpecResultStore,
    InMemoryAgentStepSink,
]


def _harness(loop_max: int = 6) -> _Harness:
    store = InMemoryJobStore()
    supervisor = Supervisor(
        store=store,
        lease_duration=timedelta(minutes=5),
        priority_aging_interval=timedelta(minutes=1),
        clock=lambda: _NOW,
    )
    profiles, specs, sink = (
        InMemoryProfileStore(),
        InMemorySpecResultStore(),
        InMemoryAgentStepSink(),
    )
    runner = MappingProbeRunner(
        supervisor=supervisor,
        profile_store=profiles,
        spec_store=specs,
        step_sink=sink,
        checkpointer=InMemorySaver(),
        loop_max=loop_max,
        lease_owner="worker-1",
        new_id=_counter(),
    )
    return supervisor, runner, profiles, specs, sink


def _put_profile(profiles: InMemoryProfileStore, headers: list[str]) -> str:
    return profiles.put(
        ProfileRecord(
            id=UUID(int=1000), tenant_id="t1", file_hash="h", filename="r.xlsx",
            sheets=serialize_profile(_profile(headers)), created_at=_NOW,
        )
    )


async def _enqueue(supervisor: Supervisor, payload_ref: str) -> WorkerJob:
    job = WorkerJob(
        job_id=UUID(int=5), execution_id=UUID(int=6), tenant_id="t1",
        worker_kind=WorkerKind.MAPPING_PROBE, operation=OperationKind.MAPPING_PROBE_RESOLVE,
        payload_ref=payload_ref, payload_hash=_HASH, priority_class=PriorityClass.STANDARD,
        queued_at=_NOW,
    )
    return await supervisor.enqueue(job)


_ROSTER = ["원생명", "반", "등원일", "상태", "동의"]


def test_run_next_succeeds_with_result_ref() -> None:
    supervisor, runner, profiles, specs, _sink = _harness()
    ref = _put_profile(profiles, _ROSTER)

    async def scenario() -> WorkerJob | None:
        await _enqueue(supervisor, ref)
        return await runner.run_next(tenant_id="t1")

    done = _run(scenario())
    assert done is not None and done.phase is JobPhase.SUCCEEDED
    assert done.result_ref is not None and done.result_ref.startswith("spec://")
    spec = specs.get(done.result_ref)
    assert spec is not None and spec.status == "succeeded"
    assert spec.probe_agent_run == UUID(int=6)  # execution_id 링크
    assert len(spec.spec["resolved"]) == 5 and spec.spec["unresolved"] == []


def test_agent_step_persisted_with_orm_fields() -> None:
    supervisor, runner, profiles, _specs, sink = _harness()
    ref = _put_profile(profiles, _ROSTER)

    async def scenario() -> None:
        await _enqueue(supervisor, ref)
        await runner.run_next(tenant_id="t1")

    _run(scenario())
    steps = sink.steps(UUID(int=6))
    assert len(steps) == 5  # 컬럼 5개 조사
    first = steps[0]
    assert first.node_name == "tool_call" and first.tool_called == "get_unique_values"
    assert "column" in first.tool_args_masked and first.outcome == "ok"
    # 마스킹 통과분만 — 원문 실명 없음(도구 인자는 컬럼명뿐)
    assert "김" not in str([s.tool_args_masked for s in steps])


def test_partial_unresolved_still_succeeds() -> None:
    """상한으로 일부 미해결이어도 결과 계약 저장 → Job succeeded(error_codes §2.5)."""
    supervisor, runner, profiles, specs, _sink = _harness(loop_max=2)
    ref = _put_profile(profiles, _ROSTER)

    async def scenario() -> WorkerJob | None:
        await _enqueue(supervisor, ref)
        return await runner.run_next(tenant_id="t1")

    done = _run(scenario())
    assert done is not None and done.phase is JobPhase.SUCCEEDED
    spec = specs.get(done.result_ref or "")
    assert spec is not None and len(spec.spec["unresolved"]) == 3


def test_stale_lease_generation_is_fenced() -> None:
    """만료·경합 실행자의 후속 저장은 fencing으로 거부(§5.1)."""
    supervisor, _runner, profiles, _specs, _sink = _harness()
    ref = _put_profile(profiles, _ROSTER)

    async def scenario() -> None:
        await _enqueue(supervisor, ref)
        leased = await supervisor.lease_next(
            tenant_id="t1", worker_kind=WorkerKind.MAPPING_PROBE, lease_owner="worker-1"
        )
        assert leased is not None and leased.lease_generation == 1
        with pytest.raises(StaleLeaseError):
            await supervisor.succeed(
                tenant_id="t1", job_id=leased.job_id, lease_owner="worker-1",
                lease_generation=999, result_ref="spec://x",  # 잘못된 세대 → fencing
            )

    _run(scenario())


def test_run_next_returns_none_when_queue_empty() -> None:
    _supervisor, runner, _profiles, _specs, _sink = _harness()
    assert _run(runner.run_next(tenant_id="t1")) is None
