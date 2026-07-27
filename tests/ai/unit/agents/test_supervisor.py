"""결정론 슈퍼바이저의 lease 경합·회수·tenant 격리 검사."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from ai.agents.job_store import (
    InMemoryJobStore,
    InvalidPhaseError,
    JobNotFoundError,
    JobStore,
    StaleLeaseError,
    effective_priority_level,
)
from ai.agents.supervisor import Supervisor, route_worker
from ai.contracts.agents import (
    WORKER_RECOVERY_EXHAUSTED,
    JobPhase,
    OperationKind,
    PriorityClass,
    WorkerJob,
    WorkerKind,
    default_priority_for_operation,
)

_T0 = datetime(2026, 7, 27, 9, 0, tzinfo=UTC)
_LEASE_DURATION = timedelta(minutes=1)
_AGING_INTERVAL = timedelta(minutes=10)
_HASH = "sha256:" + "b" * 64


def _run[T](awaitable: Awaitable[T]) -> T:
    return asyncio.run(awaitable)  # type: ignore[arg-type]


class _Clock:
    def __init__(self, now: datetime = _T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _job(
    *,
    job_id: int,
    tenant_id: str = "tenant-a",
    operation: OperationKind = OperationKind.PROBLEM_SET_GENERATE,
    priority_class: PriorityClass | None = None,
    queued_at: datetime = _T0,
    max_recovery_attempts: int = 3,
) -> WorkerJob:
    worker_by_operation = {
        OperationKind.COUNSEL_PACK_GENERATE: WorkerKind.COUNSEL_PACK,
        OperationKind.MAPPING_PROBE_RESOLVE: WorkerKind.MAPPING_PROBE,
        OperationKind.PROBLEM_SET_GENERATE: WorkerKind.PROBLEM_GENERATION,
        OperationKind.PROBLEM_ITEM_REFINE: WorkerKind.PROBLEM_GENERATION,
        OperationKind.PROBLEM_ITEM_REVERIFY: WorkerKind.PROBLEM_GENERATION,
    }
    return WorkerJob(
        job_id=UUID(int=job_id),
        execution_id=UUID(int=1000 + job_id),
        tenant_id=tenant_id,
        worker_kind=worker_by_operation[operation],
        operation=operation,
        payload_ref=f"command://{operation.value}/{job_id}",
        payload_hash=_HASH,
        priority_class=priority_class or default_priority_for_operation(operation),
        queued_at=queued_at,
        max_recovery_attempts=max_recovery_attempts,
    )


def _supervisor(
    store: InMemoryJobStore,
    clock: _Clock,
) -> Supervisor:
    return Supervisor(
        store=store,
        lease_duration=_LEASE_DURATION,
        priority_aging_interval=_AGING_INTERVAL,
        clock=clock,
    )


async def _lease_and_start(
    supervisor: Supervisor,
    job: WorkerJob,
    *,
    lease_owner: str = "worker-1",
    checkpoint_ref: str = "checkpoint://thread/1/initial",
) -> WorkerJob:
    leased = await supervisor.lease_next(
        tenant_id=job.tenant_id,
        worker_kind=job.worker_kind,
        lease_owner=lease_owner,
    )
    assert leased is not None
    return await supervisor.start(
        tenant_id=job.tenant_id,
        job_id=job.job_id,
        lease_owner=lease_owner,
        lease_generation=leased.lease_generation,
        checkpoint_ref=checkpoint_ref,
    )


def test_inmemory_store_satisfies_protocol() -> None:
    assert isinstance(InMemoryJobStore(), JobStore)


def test_route_worker_uses_fixed_contract_table() -> None:
    job = _job(job_id=1, operation=OperationKind.PROBLEM_ITEM_REFINE)
    assert route_worker(job) is WorkerKind.PROBLEM_GENERATION


def test_priority_then_queued_at_then_job_id_order_is_deterministic() -> None:
    store = InMemoryJobStore()
    clock = _Clock()
    supervisor = _supervisor(store, clock)
    jobs = (
        _job(
            job_id=30,
            operation=OperationKind.PROBLEM_SET_GENERATE,
            queued_at=_T0 - timedelta(minutes=2),
        ),
        _job(
            job_id=20,
            operation=OperationKind.PROBLEM_ITEM_REFINE,
            queued_at=_T0,
        ),
        _job(
            job_id=10,
            operation=OperationKind.PROBLEM_ITEM_REFINE,
            queued_at=_T0,
        ),
    )

    async def scenario() -> None:
        for job in jobs:
            await supervisor.enqueue(job)
        first = await supervisor.lease_next(
            tenant_id="tenant-a",
            worker_kind=WorkerKind.PROBLEM_GENERATION,
            lease_owner="worker-1",
        )
        assert first is not None
        assert first.job_id == UUID(int=10)

    _run(scenario())


def test_priority_aging_promotes_at_most_two_levels() -> None:
    batch = _job(
        job_id=1,
        operation=OperationKind.COUNSEL_PACK_GENERATE,
        queued_at=_T0,
    )
    assert (
        effective_priority_level(
            batch,
            selected_at=_T0 + _AGING_INTERVAL,
            priority_aging_interval=_AGING_INTERVAL,
        )
        == 1
    )
    assert (
        effective_priority_level(
            batch,
            selected_at=_T0 + _AGING_INTERVAL * 2,
            priority_aging_interval=_AGING_INTERVAL,
        )
        == 2
    )
    assert (
        effective_priority_level(
            batch,
            selected_at=_T0 + _AGING_INTERVAL * 100,
            priority_aging_interval=_AGING_INTERVAL,
        )
        == 2
    )


def test_aged_batch_precedes_new_interactive_when_effective_priority_ties() -> None:
    store = InMemoryJobStore()
    clock = _Clock(_T0 + _AGING_INTERVAL * 2)
    supervisor = _supervisor(store, clock)
    aged_batch = _job(
        job_id=20,
        operation=OperationKind.COUNSEL_PACK_GENERATE,
        queued_at=_T0,
    )
    new_interactive = _job(
        job_id=10,
        operation=OperationKind.COUNSEL_PACK_GENERATE,
        priority_class=PriorityClass.INTERACTIVE,
        queued_at=clock.now,
    )

    async def scenario() -> None:
        await supervisor.enqueue(aged_batch)
        await supervisor.enqueue(new_interactive)
        leased = await supervisor.lease_next(
            tenant_id="tenant-a",
            worker_kind=WorkerKind.COUNSEL_PACK,
            lease_owner="worker-1",
        )
        assert leased is not None
        assert leased.job_id == aged_batch.job_id

    _run(scenario())


def test_concurrent_lease_is_atomic() -> None:
    store = InMemoryJobStore()
    clock = _Clock()
    first_supervisor = _supervisor(store, clock)
    second_supervisor = _supervisor(store, clock)
    job = _job(job_id=1)

    async def scenario() -> None:
        await first_supervisor.enqueue(job)
        leases = await asyncio.gather(
            first_supervisor.lease_next(
                tenant_id=job.tenant_id,
                worker_kind=job.worker_kind,
                lease_owner="worker-1",
            ),
            second_supervisor.lease_next(
                tenant_id=job.tenant_id,
                worker_kind=job.worker_kind,
                lease_owner="worker-2",
            ),
        )
        acquired = [lease for lease in leases if lease is not None]
        assert len(acquired) == 1
        assert acquired[0].lease_generation == 1

    _run(scenario())


def test_expired_running_job_is_released_from_checkpoint() -> None:
    store = InMemoryJobStore()
    clock = _Clock()
    supervisor = _supervisor(store, clock)
    job = _job(job_id=1)

    async def scenario() -> None:
        await supervisor.enqueue(job)
        running = await _lease_and_start(
            supervisor,
            job,
            checkpoint_ref="checkpoint://thread/1/item-3",
        )
        clock.now = _T0 + _LEASE_DURATION + timedelta(seconds=1)
        released = await supervisor.lease_next(
            tenant_id=job.tenant_id,
            worker_kind=job.worker_kind,
            lease_owner="worker-2",
        )
        assert released is not None
        assert released.phase is JobPhase.LEASED
        assert released.lease_generation == running.lease_generation + 1
        assert released.dispatch_attempt == 2
        assert released.recovery_count == 1
        assert released.checkpoint_ref == "checkpoint://thread/1/item-3"
        assert released.started_at == running.started_at

    _run(scenario())


def test_stale_generation_cannot_complete_after_recovery() -> None:
    store = InMemoryJobStore()
    clock = _Clock()
    old_supervisor = _supervisor(store, clock)
    new_supervisor = _supervisor(store, clock)
    job = _job(job_id=1)

    async def scenario() -> None:
        await old_supervisor.enqueue(job)
        old_running = await _lease_and_start(old_supervisor, job, lease_owner="worker-old")
        clock.now = _T0 + _LEASE_DURATION + timedelta(seconds=1)
        new_lease = await new_supervisor.lease_next(
            tenant_id=job.tenant_id,
            worker_kind=job.worker_kind,
            lease_owner="worker-new",
        )
        assert new_lease is not None
        await new_supervisor.start(
            tenant_id=job.tenant_id,
            job_id=job.job_id,
            lease_owner="worker-new",
            lease_generation=new_lease.lease_generation,
            checkpoint_ref=old_running.checkpoint_ref or "checkpoint://unexpected",
        )
        with pytest.raises(StaleLeaseError):
            await old_supervisor.succeed(
                tenant_id=job.tenant_id,
                job_id=job.job_id,
                lease_owner="worker-old",
                lease_generation=old_running.lease_generation,
                result_ref="result://stale",
            )
        succeeded = await new_supervisor.succeed(
            tenant_id=job.tenant_id,
            job_id=job.job_id,
            lease_owner="worker-new",
            lease_generation=new_lease.lease_generation,
            result_ref="result://current",
        )
        assert succeeded.result_ref == "result://current"

    _run(scenario())


def test_recovery_attempt_cap_converges_to_failed() -> None:
    store = InMemoryJobStore()
    clock = _Clock()
    supervisor = _supervisor(store, clock)
    job = _job(job_id=1, max_recovery_attempts=1)

    async def scenario() -> None:
        await supervisor.enqueue(job)
        await _lease_and_start(supervisor, job)
        clock.now = _T0 + _LEASE_DURATION + timedelta(seconds=1)
        recovered = await supervisor.recover_expired(
            tenant_id=job.tenant_id,
            worker_kind=job.worker_kind,
        )
        assert len(recovered) == 1
        assert recovered[0].phase is JobPhase.FAILED
        assert recovered[0].error_code == WORKER_RECOVERY_EXHAUSTED
        assert (
            await supervisor.lease_next(
                tenant_id=job.tenant_id,
                worker_kind=job.worker_kind,
                lease_owner="worker-2",
            )
            is None
        )

    _run(scenario())


def test_paused_job_requires_explicit_resume() -> None:
    store = InMemoryJobStore()
    clock = _Clock()
    supervisor = _supervisor(store, clock)
    job = _job(job_id=1)

    async def scenario() -> None:
        await supervisor.enqueue(job)
        running = await _lease_and_start(supervisor, job)
        paused = await supervisor.pause(
            tenant_id=job.tenant_id,
            job_id=job.job_id,
            lease_owner="worker-1",
            lease_generation=running.lease_generation,
            checkpoint_ref="checkpoint://thread/1/item-4",
        )
        assert paused.phase is JobPhase.PAUSED
        assert (
            await supervisor.lease_next(
                tenant_id=job.tenant_id,
                worker_kind=job.worker_kind,
                lease_owner="worker-2",
            )
            is None
        )
        queued = await supervisor.resume(tenant_id=job.tenant_id, job_id=job.job_id)
        assert queued.phase is JobPhase.QUEUED
        resumed = await supervisor.lease_next(
            tenant_id=job.tenant_id,
            worker_kind=job.worker_kind,
            lease_owner="worker-2",
        )
        assert resumed is not None
        assert resumed.checkpoint_ref == "checkpoint://thread/1/item-4"

    _run(scenario())


def test_manual_pause_resume_does_not_consume_recovery_budget() -> None:
    store = InMemoryJobStore()
    clock = _Clock()
    supervisor = _supervisor(store, clock)
    job = _job(job_id=1, max_recovery_attempts=1)

    async def scenario() -> None:
        await supervisor.enqueue(job)
        paused: WorkerJob | None = None
        for turn in range(5):
            leased = await supervisor.lease_next(
                tenant_id=job.tenant_id,
                worker_kind=job.worker_kind,
                lease_owner=f"worker-{turn}",
            )
            assert leased is not None
            running = await supervisor.start(
                tenant_id=job.tenant_id,
                job_id=job.job_id,
                lease_owner=f"worker-{turn}",
                lease_generation=leased.lease_generation,
                checkpoint_ref=f"checkpoint://thread/1/item-{turn}",
            )
            paused = await supervisor.pause(
                tenant_id=job.tenant_id,
                job_id=job.job_id,
                lease_owner=f"worker-{turn}",
                lease_generation=running.lease_generation,
                checkpoint_ref=f"checkpoint://thread/1/item-{turn + 1}",
            )
            assert paused.recovery_count == 0
            if turn < 4:
                await supervisor.resume(tenant_id=job.tenant_id, job_id=job.job_id)
        assert paused is not None
        assert paused.dispatch_attempt == 5
        assert paused.lease_generation == 5
        assert paused.recovery_count == 0

    _run(scenario())


def test_external_cancel_cannot_clear_active_lease() -> None:
    store = InMemoryJobStore()
    clock = _Clock()
    supervisor = _supervisor(store, clock)
    job = _job(job_id=1)

    async def scenario() -> None:
        await supervisor.enqueue(job)
        running = await _lease_and_start(supervisor, job)
        with pytest.raises(InvalidPhaseError):
            await supervisor.cancel_waiting(
                tenant_id=job.tenant_id,
                job_id=job.job_id,
                error_code="teacher_cancelled",
            )
        current = await supervisor.get(tenant_id=job.tenant_id, job_id=job.job_id)
        assert current == running

    _run(scenario())


def test_worker_cancel_requires_current_fencing_token_and_checkpoint() -> None:
    store = InMemoryJobStore()
    clock = _Clock()
    supervisor = _supervisor(store, clock)
    job = _job(job_id=1)

    async def scenario() -> None:
        await supervisor.enqueue(job)
        running = await _lease_and_start(supervisor, job)
        with pytest.raises(StaleLeaseError):
            await supervisor.cancel_owned(
                tenant_id=job.tenant_id,
                job_id=job.job_id,
                lease_owner="worker-1",
                lease_generation=running.lease_generation + 1,
                checkpoint_ref="checkpoint://thread/1/safe-stop",
            )
        with pytest.raises(ValueError, match="checkpoint_ref"):
            await supervisor.cancel_owned(
                tenant_id=job.tenant_id,
                job_id=job.job_id,
                lease_owner="worker-1",
                lease_generation=running.lease_generation,
                checkpoint_ref=None,
            )
        cancelled = await supervisor.cancel_owned(
            tenant_id=job.tenant_id,
            job_id=job.job_id,
            lease_owner="worker-1",
            lease_generation=running.lease_generation,
            checkpoint_ref="checkpoint://thread/1/safe-stop",
            error_code="teacher_cancelled",
        )
        assert cancelled.phase is JobPhase.CANCELLED
        assert cancelled.checkpoint_ref == "checkpoint://thread/1/safe-stop"
        assert cancelled.lease_owner is None

    _run(scenario())


def test_tenant_scope_is_enforced_on_selection_lookup_and_completion() -> None:
    store = InMemoryJobStore()
    clock = _Clock()
    supervisor = _supervisor(store, clock)
    tenant_a_job = _job(job_id=1, tenant_id="tenant-a")
    tenant_b_job = _job(job_id=2, tenant_id="tenant-b")

    async def scenario() -> None:
        await supervisor.enqueue(tenant_a_job)
        await supervisor.enqueue(tenant_b_job)
        running = await _lease_and_start(supervisor, tenant_a_job)
        assert await supervisor.get(tenant_id="tenant-b", job_id=tenant_a_job.job_id) is None
        with pytest.raises(JobNotFoundError):
            await supervisor.succeed(
                tenant_id="tenant-b",
                job_id=tenant_a_job.job_id,
                lease_owner="worker-1",
                lease_generation=running.lease_generation,
                result_ref="result://not-allowed",
            )
        tenant_b_lease = await supervisor.lease_next(
            tenant_id="tenant-b",
            worker_kind=tenant_b_job.worker_kind,
            lease_owner="worker-b",
        )
        assert tenant_b_lease is not None
        assert tenant_b_lease.job_id == tenant_b_job.job_id

    _run(scenario())


def test_domain_partial_result_is_opaque_success_reference() -> None:
    store = InMemoryJobStore()
    clock = _Clock()
    supervisor = _supervisor(store, clock)
    job = _job(job_id=1)

    async def scenario() -> None:
        await supervisor.enqueue(job)
        running = await _lease_and_start(supervisor, job)
        result_ref = "problem-set://set-1?status=partial_success"
        succeeded = await supervisor.succeed(
            tenant_id=job.tenant_id,
            job_id=job.job_id,
            lease_owner="worker-1",
            lease_generation=running.lease_generation,
            result_ref=result_ref,
        )
        assert succeeded.phase is JobPhase.SUCCEEDED
        assert succeeded.result_ref == result_ref
        assert succeeded.error_code is None

    _run(scenario())


def test_failure_can_preserve_partial_result_reference() -> None:
    store = InMemoryJobStore()
    clock = _Clock()
    supervisor = _supervisor(store, clock)
    job = _job(job_id=1)

    async def scenario() -> None:
        await supervisor.enqueue(job)
        running = await _lease_and_start(supervisor, job)
        failed = await supervisor.fail(
            tenant_id=job.tenant_id,
            job_id=job.job_id,
            lease_owner="worker-1",
            lease_generation=running.lease_generation,
            error_code="provider_unavailable",
            result_ref="counsel-pack://run-1/completed-students",
        )
        assert failed.phase is JobPhase.FAILED
        assert failed.result_ref == "counsel-pack://run-1/completed-students"

    _run(scenario())


def test_leased_job_can_fail_before_worker_checkpoint_initialization() -> None:
    store = InMemoryJobStore()
    clock = _Clock()
    supervisor = _supervisor(store, clock)
    job = _job(job_id=1)

    async def scenario() -> None:
        await supervisor.enqueue(job)
        leased = await supervisor.lease_next(
            tenant_id=job.tenant_id,
            worker_kind=job.worker_kind,
            lease_owner="worker-1",
        )
        assert leased is not None
        failed = await supervisor.fail(
            tenant_id=job.tenant_id,
            job_id=job.job_id,
            lease_owner="worker-1",
            lease_generation=leased.lease_generation,
            error_code="checkpoint_initialization_failed",
        )
        assert failed.phase is JobPhase.FAILED
        assert failed.started_at is None
        assert failed.error_code == "checkpoint_initialization_failed"

    _run(scenario())
