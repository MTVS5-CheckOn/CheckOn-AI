"""슈퍼바이저 공통 계약의 라우팅·상태·불변 필드 검사."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from uuid import UUID

import pytest
from pydantic import ValidationError

from ai.contracts.agents import (
    DEFAULT_MAX_RECOVERY_ATTEMPTS,
    DEFAULT_OPERATION_PRIORITY,
    NORMAL_TRANSITIONS,
    OPERATION_WORKER,
    WORKER_RECOVERY_EXHAUSTED,
    InvalidJobTransition,
    JobPhase,
    OperationKind,
    PriorityClass,
    WorkerJob,
    WorkerKind,
    assert_expired_recovery,
    assert_job_transition,
    assert_paused_resume,
    default_priority_for_operation,
    worker_for_operation,
)

_T0 = datetime(2026, 7, 27, 9, 0, tzinfo=UTC)
_JOB_ID = UUID("00000000-0000-4000-8000-000000000001")
_EXECUTION_ID = UUID("00000000-0000-4000-8000-000000000002")
_HASH = "sha256:" + "a" * 64


def _job(**changes: object) -> WorkerJob:
    values: dict[str, object] = {
        "job_id": _JOB_ID,
        "execution_id": _EXECUTION_ID,
        "tenant_id": "tenant-a",
        "worker_kind": WorkerKind.PROBLEM_GENERATION,
        "operation": OperationKind.PROBLEM_SET_GENERATE,
        "payload_ref": "command://problem-set/1",
        "payload_hash": _HASH,
        "priority_class": PriorityClass.STANDARD,
        "queued_at": _T0,
    }
    values.update(changes)
    return WorkerJob.model_validate(values)


def _leased(job: WorkerJob, *, expires_at: datetime | None = None) -> WorkerJob:
    values: dict[str, object] = {
        name: getattr(job, name) for name in WorkerJob.model_fields
    }
    values.update(
        {
            "phase": JobPhase.LEASED,
            "dispatch_attempt": job.dispatch_attempt + 1,
            "lease_generation": job.lease_generation + 1,
            "lease_owner": "worker-1",
            "lease_acquired_at": _T0 + timedelta(seconds=1),
            "lease_expires_at": expires_at or _T0 + timedelta(minutes=1),
        }
    )
    return WorkerJob.model_validate(values)


def _running(job: WorkerJob) -> WorkerJob:
    values: dict[str, object] = {
        name: getattr(job, name) for name in WorkerJob.model_fields
    }
    values.update(
        {
            "phase": JobPhase.RUNNING,
            "checkpoint_ref": "checkpoint://thread/1/initial",
            "started_at": _T0 + timedelta(seconds=2),
        }
    )
    return WorkerJob.model_validate(values)


def test_operation_routes_to_exactly_one_worker() -> None:
    expected = {
        OperationKind.COUNSEL_PACK_GENERATE: WorkerKind.COUNSEL_PACK,
        OperationKind.MAPPING_PROBE_RESOLVE: WorkerKind.MAPPING_PROBE,
        OperationKind.PROBLEM_SET_GENERATE: WorkerKind.PROBLEM_GENERATION,
        OperationKind.PROBLEM_ITEM_REFINE: WorkerKind.PROBLEM_GENERATION,
        OperationKind.PROBLEM_ITEM_REVERIFY: WorkerKind.PROBLEM_GENERATION,
    }
    assert dict(OPERATION_WORKER) == expected
    assert set(OPERATION_WORKER) == set(OperationKind)
    for operation, worker_kind in expected.items():
        assert worker_for_operation(operation) is worker_kind


def test_routing_and_default_priority_tables_are_immutable() -> None:
    assert isinstance(OPERATION_WORKER, MappingProxyType)
    assert isinstance(DEFAULT_OPERATION_PRIORITY, MappingProxyType)
    with pytest.raises(TypeError):
        OPERATION_WORKER[OperationKind.PROBLEM_SET_GENERATE] = (  # type: ignore[index]
            WorkerKind.COUNSEL_PACK
        )


def test_interactive_operations_have_interactive_default_priority() -> None:
    assert (
        default_priority_for_operation(OperationKind.PROBLEM_ITEM_REFINE)
        is PriorityClass.INTERACTIVE
    )
    assert (
        default_priority_for_operation(OperationKind.PROBLEM_ITEM_REVERIFY)
        is PriorityClass.INTERACTIVE
    )
    assert (
        default_priority_for_operation(OperationKind.COUNSEL_PACK_GENERATE)
        is PriorityClass.BATCH
    )


def test_worker_job_rejects_operation_worker_mismatch() -> None:
    with pytest.raises(ValidationError, match="고정 라우팅"):
        _job(worker_kind=WorkerKind.COUNSEL_PACK)


def test_worker_job_requires_canonical_sha256_hash() -> None:
    with pytest.raises(ValidationError, match="payload_hash"):
        _job(payload_hash="not-a-hash")


def test_worker_job_is_frozen_and_forbids_extra_fields() -> None:
    job = _job()
    with pytest.raises(ValidationError, match="frozen"):
        job.tenant_id = "tenant-b"  # type: ignore[misc]
    with pytest.raises(ValidationError, match="extra"):
        WorkerJob.model_validate({**job.model_dump(), "payload": {"secret": True}})


def test_default_recovery_attempt_cap_is_three() -> None:
    job = _job()
    assert job.max_recovery_attempts == DEFAULT_MAX_RECOVERY_ATTEMPTS == 3
    assert job.recovery_count == 0
    assert job.dispatch_attempt == job.lease_generation == 0


def test_dispatch_attempt_and_fencing_generation_are_independent_fields() -> None:
    job = _job(dispatch_attempt=2, lease_generation=1)
    assert job.dispatch_attempt == 2
    assert job.lease_generation == 1


def test_job_rejects_recovery_count_overflow() -> None:
    with pytest.raises(ValidationError, match="max_recovery_attempts"):
        _job(recovery_count=2, max_recovery_attempts=1)


def test_job_rejects_naive_timestamps() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        _job(queued_at=datetime(2026, 7, 27, 9, 0))


def test_running_job_requires_initial_checkpoint() -> None:
    leased = _leased(_job())
    values = leased.model_dump()
    values.update(
        phase=JobPhase.RUNNING,
        started_at=_T0 + timedelta(seconds=2),
    )
    with pytest.raises(ValidationError, match="checkpoint_ref"):
        WorkerJob.model_validate(values)


def test_normal_transition_table_excludes_implicit_recovery_and_resume() -> None:
    assert JobPhase.QUEUED not in NORMAL_TRANSITIONS[JobPhase.LEASED]
    assert JobPhase.QUEUED not in NORMAL_TRANSITIONS[JobPhase.RUNNING]
    assert JobPhase.QUEUED not in NORMAL_TRANSITIONS[JobPhase.PAUSED]


def test_queued_to_leased_to_running_transitions_are_valid() -> None:
    queued = _job()
    leased = _leased(queued)
    running = _running(leased)
    assert_job_transition(queued, leased)
    assert_job_transition(leased, running)


def test_direct_queued_to_running_transition_is_rejected() -> None:
    queued = _job()
    running = _running(_leased(queued))
    with pytest.raises(InvalidJobTransition, match="전이 불가"):
        assert_job_transition(queued, running)


def test_transition_cannot_mutate_command_identity() -> None:
    queued = _job()
    leased = _leased(queued)
    changed = WorkerJob.model_validate(
        {
            **leased.model_dump(),
            "payload_ref": "command://problem-set/changed",
        }
    )
    with pytest.raises(InvalidJobTransition, match="불변 작업 명령"):
        assert_job_transition(queued, changed)


def test_expired_running_recovery_preserves_checkpoint() -> None:
    queued = _job()
    running = _running(
        _leased(queued, expires_at=_T0 + timedelta(seconds=10))
    )
    recovered = WorkerJob.model_validate(
        {
            **running.model_dump(),
            "phase": JobPhase.QUEUED,
            "lease_owner": None,
            "lease_acquired_at": None,
            "lease_expires_at": None,
            "recovery_count": 1,
        }
    )
    assert_expired_recovery(
        running,
        recovered,
        recovered_at=_T0 + timedelta(seconds=11),
    )
    assert recovered.checkpoint_ref == running.checkpoint_ref


def test_unexpired_lease_cannot_be_recovered() -> None:
    leased = _leased(_job(), expires_at=_T0 + timedelta(minutes=1))
    recovered = WorkerJob.model_validate(
        {
            **leased.model_dump(),
            "phase": JobPhase.QUEUED,
            "lease_owner": None,
            "lease_acquired_at": None,
            "lease_expires_at": None,
        }
    )
    with pytest.raises(InvalidJobTransition, match="만료되지 않은"):
        assert_expired_recovery(
            leased,
            recovered,
            recovered_at=_T0 + timedelta(seconds=30),
        )


def test_recovery_cap_converges_to_failed() -> None:
    queued = _job(max_recovery_attempts=1)
    leased = _leased(queued, expires_at=_T0 + timedelta(seconds=10))
    failed = WorkerJob.model_validate(
        {
            **leased.model_dump(),
            "phase": JobPhase.FAILED,
            "lease_owner": None,
            "lease_acquired_at": None,
            "lease_expires_at": None,
            "recovery_count": 1,
            "error_code": WORKER_RECOVERY_EXHAUSTED,
            "finished_at": _T0 + timedelta(seconds=11),
        }
    )
    assert_expired_recovery(
        leased,
        failed,
        recovered_at=_T0 + timedelta(seconds=11),
    )


def test_paused_resume_is_explicit_and_preserves_checkpoint() -> None:
    running = _running(_leased(_job()))
    paused = WorkerJob.model_validate(
        {
            **running.model_dump(),
            "phase": JobPhase.PAUSED,
            "lease_owner": None,
            "lease_acquired_at": None,
            "lease_expires_at": None,
        }
    )
    assert_job_transition(running, paused)
    resumed = WorkerJob.model_validate({**paused.model_dump(), "phase": JobPhase.QUEUED})
    assert_paused_resume(paused, resumed)
    assert resumed.checkpoint_ref == paused.checkpoint_ref
