"""슈퍼바이저 작업 저장소 경계와 테스트·개발용 인메모리 구현.

프로덕션 구현은 PostgreSQL 트랜잭션 안에서 대기열 선택과 lease 갱신을 하나의
원자 연산으로 수행해야 한다. 특히 ``lease_generation``을 fencing token으로 비교해
만료된 이전 워커가 결과를 덮어쓰지 못하게 해야 한다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable
from uuid import UUID

from ai.contracts.agents import (
    ACTIVE_LEASE_PHASES,
    WORKER_RECOVERY_EXHAUSTED,
    JobPhase,
    PriorityClass,
    WorkerJob,
    WorkerKind,
    assert_expired_recovery,
    assert_job_transition,
    assert_paused_resume,
)


class JobStoreError(RuntimeError):
    """슈퍼바이저 작업 저장소 오류."""


class JobAlreadyExistsError(JobStoreError):
    """같은 job_id가 이미 존재한다."""


class JobNotFoundError(JobStoreError):
    """tenant 범위에서 작업을 찾을 수 없다."""


class StaleLeaseError(JobStoreError):
    """lease 소유권·세대·유효기간이 현재 저장값과 일치하지 않는다."""


@runtime_checkable
class JobStore(Protocol):
    """영속 작업 저장소 인터페이스.

    구현체는 각 메서드 전체를 단일 트랜잭션/CAS로 보장해야 한다. PostgreSQL 구현의
    ``lease_next``는 tenant·worker 필터와 결정론 정렬 뒤 ``FOR UPDATE SKIP LOCKED``로
    한 행을 선택하고, 완료 계열은 owner·generation·만료시각을 WHERE 절에서 비교한다.
    이 인터페이스는 Job 원장까지만 다룬다. Kafka outbox를 붙이는 배포에서는 terminal
    전이와 outbox 기록을 같은 PostgreSQL 트랜잭션으로 확장해야 한다.
    """

    async def add(self, job: WorkerJob) -> WorkerJob: ...

    async def get(self, *, tenant_id: str, job_id: UUID) -> WorkerJob | None: ...

    async def recover_expired(
        self,
        *,
        tenant_id: str,
        worker_kind: WorkerKind,
        recovered_at: datetime,
    ) -> tuple[WorkerJob, ...]: ...

    async def lease_next(
        self,
        *,
        tenant_id: str,
        worker_kind: WorkerKind,
        lease_owner: str,
        acquired_at: datetime,
        expires_at: datetime,
        priority_aging_interval: timedelta,
    ) -> WorkerJob | None: ...

    async def resume(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        resumed_at: datetime,
    ) -> WorkerJob: ...

    async def mark_running(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        lease_owner: str,
        lease_generation: int,
        checkpoint_ref: str,
        started_at: datetime,
    ) -> WorkerJob: ...

    async def renew_lease(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        lease_owner: str,
        lease_generation: int,
        renewed_at: datetime,
        expires_at: datetime,
    ) -> WorkerJob: ...

    async def pause(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        lease_owner: str,
        lease_generation: int,
        checkpoint_ref: str,
        paused_at: datetime,
    ) -> WorkerJob: ...

    async def succeed(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        lease_owner: str,
        lease_generation: int,
        result_ref: str,
        finished_at: datetime,
    ) -> WorkerJob: ...

    async def fail(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        lease_owner: str,
        lease_generation: int,
        error_code: str,
        result_ref: str | None,
        finished_at: datetime,
    ) -> WorkerJob: ...

    async def cancel_waiting(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        error_code: str | None,
        finished_at: datetime,
    ) -> WorkerJob: ...

    async def cancel_owned(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        lease_owner: str,
        lease_generation: int,
        checkpoint_ref: str | None,
        error_code: str | None,
        finished_at: datetime,
    ) -> WorkerJob: ...


_PRIORITY_LEVEL = {
    PriorityClass.BATCH: 0,
    PriorityClass.STANDARD: 1,
    PriorityClass.INTERACTIVE: 2,
}
_MAX_PRIORITY_LEVEL = max(_PRIORITY_LEVEL.values())


def job_order_key(
    job: WorkerJob,
    *,
    selected_at: datetime,
    priority_aging_interval: timedelta,
) -> tuple[int, datetime, str]:
    """aging 적용 우선순위 → 최초 queued_at → job_id의 결정론 정렬 키."""

    effective_level = effective_priority_level(
        job,
        selected_at=selected_at,
        priority_aging_interval=priority_aging_interval,
    )
    return (-effective_level, job.queued_at, str(job.job_id))


def effective_priority_level(
    job: WorkerJob,
    *,
    selected_at: datetime,
    priority_aging_interval: timedelta,
) -> int:
    """대기 구간마다 한 단계씩 올리되 interactive보다 높이지 않는다."""

    _require_aware_datetime("selected_at", selected_at)
    if priority_aging_interval <= timedelta(0):
        raise ValueError("priority_aging_interval은 0보다 커야 한다")
    waited = max(selected_at - job.queued_at, timedelta(0))
    promotion_steps = waited // priority_aging_interval
    return min(_MAX_PRIORITY_LEVEL, _PRIORITY_LEVEL[job.priority_class] + promotion_steps)


def _replace_job(job: WorkerJob, **changes: object) -> WorkerJob:
    values: dict[str, object] = {
        name: getattr(job, name) for name in WorkerJob.model_fields
    }
    values.update(changes)
    return WorkerJob.model_validate(values)


def _require_aware_datetime(field_name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}은 timezone-aware datetime이어야 한다")


class InMemoryJobStore:
    """테스트·개발 전용 저장소.

    프로세스 재시작 시 소실되고 여러 프로세스 사이에 공유되지 않으므로 프로덕션에
    사용하면 안 된다. 단일 이벤트 루프 안에서는 ``asyncio.Lock``으로 선택·lease·완료를
    원자화해 영속 구현이 지켜야 할 경합 규약을 결정론적으로 검증한다.
    """

    def __init__(self) -> None:
        self._jobs: dict[UUID, WorkerJob] = {}
        self._lock = asyncio.Lock()

    async def add(self, job: WorkerJob) -> WorkerJob:
        if (
            job.phase is not JobPhase.QUEUED
            or job.dispatch_attempt != 0
            or job.lease_generation != 0
            or job.recovery_count != 0
        ):
            raise ValueError("새 작업은 최초 queued 상태여야 한다")
        async with self._lock:
            if job.job_id in self._jobs:
                raise JobAlreadyExistsError(f"이미 존재하는 job_id: {job.job_id}")
            self._jobs[job.job_id] = job
            return job

    async def get(self, *, tenant_id: str, job_id: UUID) -> WorkerJob | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.tenant_id != tenant_id:
                return None
            return job

    async def recover_expired(
        self,
        *,
        tenant_id: str,
        worker_kind: WorkerKind,
        recovered_at: datetime,
    ) -> tuple[WorkerJob, ...]:
        _require_aware_datetime("recovered_at", recovered_at)
        async with self._lock:
            expired = sorted(
                (
                    job
                    for job in self._jobs.values()
                    if job.tenant_id == tenant_id
                    and job.worker_kind is worker_kind
                    and job.phase in ACTIVE_LEASE_PHASES
                    and job.lease_expires_at is not None
                    and job.lease_expires_at <= recovered_at
                ),
                key=lambda job: (job.queued_at, str(job.job_id)),
            )
            recovered = tuple(self._recover_one(job, recovered_at) for job in expired)
            for job in recovered:
                self._jobs[job.job_id] = job
            return recovered

    async def lease_next(
        self,
        *,
        tenant_id: str,
        worker_kind: WorkerKind,
        lease_owner: str,
        acquired_at: datetime,
        expires_at: datetime,
        priority_aging_interval: timedelta,
    ) -> WorkerJob | None:
        _require_aware_datetime("acquired_at", acquired_at)
        _require_aware_datetime("expires_at", expires_at)
        if not lease_owner:
            raise ValueError("lease_owner는 비어 있을 수 없다")
        if expires_at <= acquired_at:
            raise ValueError("expires_at은 acquired_at보다 뒤여야 한다")
        if priority_aging_interval <= timedelta(0):
            raise ValueError("priority_aging_interval은 0보다 커야 한다")
        async with self._lock:
            candidates = sorted(
                (
                    job
                    for job in self._jobs.values()
                    if job.tenant_id == tenant_id
                    and job.worker_kind is worker_kind
                    and job.phase is JobPhase.QUEUED
                ),
                key=lambda job: job_order_key(
                    job,
                    selected_at=acquired_at,
                    priority_aging_interval=priority_aging_interval,
                ),
            )
            if not candidates:
                return None
            previous = candidates[0]
            leased = _replace_job(
                previous,
                phase=JobPhase.LEASED,
                dispatch_attempt=previous.dispatch_attempt + 1,
                lease_generation=previous.lease_generation + 1,
                lease_owner=lease_owner,
                lease_acquired_at=acquired_at,
                lease_expires_at=expires_at,
            )
            assert_job_transition(previous, leased)
            self._jobs[leased.job_id] = leased
            return leased

    async def resume(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        resumed_at: datetime,
    ) -> WorkerJob:
        _require_aware_datetime("resumed_at", resumed_at)
        async with self._lock:
            previous = self._get_scoped(tenant_id, job_id)
            if previous.phase is not JobPhase.PAUSED:
                raise InvalidPhaseError("paused 작업만 resume할 수 있다")
            queued = _replace_job(previous, phase=JobPhase.QUEUED)
            assert_paused_resume(previous, queued)
            self._jobs[job_id] = queued
            return queued

    async def mark_running(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        lease_owner: str,
        lease_generation: int,
        checkpoint_ref: str,
        started_at: datetime,
    ) -> WorkerJob:
        _require_aware_datetime("started_at", started_at)
        async with self._lock:
            previous = self._require_owned_lease(
                tenant_id=tenant_id,
                job_id=job_id,
                lease_owner=lease_owner,
                lease_generation=lease_generation,
                observed_at=started_at,
                expected_phase=JobPhase.LEASED,
            )
            running = _replace_job(
                previous,
                phase=JobPhase.RUNNING,
                checkpoint_ref=checkpoint_ref,
                started_at=previous.started_at or started_at,
            )
            assert_job_transition(previous, running)
            self._jobs[job_id] = running
            return running

    async def renew_lease(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        lease_owner: str,
        lease_generation: int,
        renewed_at: datetime,
        expires_at: datetime,
    ) -> WorkerJob:
        _require_aware_datetime("renewed_at", renewed_at)
        _require_aware_datetime("expires_at", expires_at)
        async with self._lock:
            previous = self._require_owned_lease(
                tenant_id=tenant_id,
                job_id=job_id,
                lease_owner=lease_owner,
                lease_generation=lease_generation,
                observed_at=renewed_at,
            )
            if previous.lease_expires_at is None or expires_at <= previous.lease_expires_at:
                raise ValueError("갱신 lease 만료시각은 기존 만료시각보다 뒤여야 한다")
            renewed = _replace_job(previous, lease_expires_at=expires_at)
            self._jobs[job_id] = renewed
            return renewed

    async def pause(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        lease_owner: str,
        lease_generation: int,
        checkpoint_ref: str,
        paused_at: datetime,
    ) -> WorkerJob:
        _require_aware_datetime("paused_at", paused_at)
        async with self._lock:
            previous = self._require_owned_lease(
                tenant_id=tenant_id,
                job_id=job_id,
                lease_owner=lease_owner,
                lease_generation=lease_generation,
                observed_at=paused_at,
                expected_phase=JobPhase.RUNNING,
            )
            paused = _replace_job(
                previous,
                phase=JobPhase.PAUSED,
                checkpoint_ref=checkpoint_ref,
                lease_owner=None,
                lease_acquired_at=None,
                lease_expires_at=None,
            )
            assert_job_transition(previous, paused)
            self._jobs[job_id] = paused
            return paused

    async def succeed(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        lease_owner: str,
        lease_generation: int,
        result_ref: str,
        finished_at: datetime,
    ) -> WorkerJob:
        _require_aware_datetime("finished_at", finished_at)
        async with self._lock:
            previous = self._require_owned_lease(
                tenant_id=tenant_id,
                job_id=job_id,
                lease_owner=lease_owner,
                lease_generation=lease_generation,
                observed_at=finished_at,
                expected_phase=JobPhase.RUNNING,
            )
            succeeded = self._finish(
                previous,
                phase=JobPhase.SUCCEEDED,
                result_ref=result_ref,
                error_code=None,
                finished_at=finished_at,
            )
            self._jobs[job_id] = succeeded
            return succeeded

    async def fail(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        lease_owner: str,
        lease_generation: int,
        error_code: str,
        result_ref: str | None,
        finished_at: datetime,
    ) -> WorkerJob:
        _require_aware_datetime("finished_at", finished_at)
        async with self._lock:
            previous = self._require_owned_lease(
                tenant_id=tenant_id,
                job_id=job_id,
                lease_owner=lease_owner,
                lease_generation=lease_generation,
                observed_at=finished_at,
            )
            failed = self._finish(
                previous,
                phase=JobPhase.FAILED,
                result_ref=result_ref,
                error_code=error_code,
                finished_at=finished_at,
            )
            self._jobs[job_id] = failed
            return failed

    async def cancel_waiting(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        error_code: str | None,
        finished_at: datetime,
    ) -> WorkerJob:
        _require_aware_datetime("finished_at", finished_at)
        async with self._lock:
            previous = self._get_scoped(tenant_id, job_id)
            if previous.phase not in {JobPhase.QUEUED, JobPhase.PAUSED}:
                raise InvalidPhaseError("외부 취소는 queued/paused 작업만 허용한다")
            cancelled = self._finish(
                previous,
                phase=JobPhase.CANCELLED,
                result_ref=None,
                error_code=error_code,
                finished_at=finished_at,
            )
            self._jobs[job_id] = cancelled
            return cancelled

    async def cancel_owned(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        lease_owner: str,
        lease_generation: int,
        checkpoint_ref: str | None,
        error_code: str | None,
        finished_at: datetime,
    ) -> WorkerJob:
        _require_aware_datetime("finished_at", finished_at)
        async with self._lock:
            previous = self._require_owned_lease(
                tenant_id=tenant_id,
                job_id=job_id,
                lease_owner=lease_owner,
                lease_generation=lease_generation,
                observed_at=finished_at,
            )
            if previous.phase is JobPhase.RUNNING and not checkpoint_ref:
                raise ValueError("running 작업 취소에는 안전 경계의 checkpoint_ref가 필요하다")
            if checkpoint_ref is not None:
                previous = _replace_job(previous, checkpoint_ref=checkpoint_ref)
            cancelled = self._finish(
                previous,
                phase=JobPhase.CANCELLED,
                result_ref=None,
                error_code=error_code,
                finished_at=finished_at,
            )
            self._jobs[job_id] = cancelled
            return cancelled

    async def clear(self) -> None:
        """테스트 간 격리를 위해 모든 인메모리 작업을 제거한다."""

        async with self._lock:
            self._jobs.clear()

    def _recover_one(self, previous: WorkerJob, recovered_at: datetime) -> WorkerJob:
        recovery_count = previous.recovery_count + 1
        if recovery_count >= previous.max_recovery_attempts:
            recovered = self._recovery_exhausted(
                previous,
                recovered_at,
                recovery_count=recovery_count,
            )
        else:
            recovered = _replace_job(
                previous,
                phase=JobPhase.QUEUED,
                recovery_count=recovery_count,
                lease_owner=None,
                lease_acquired_at=None,
                lease_expires_at=None,
            )
        assert_expired_recovery(previous, recovered, recovered_at=recovered_at)
        return recovered

    def _recovery_exhausted(
        self,
        previous: WorkerJob,
        finished_at: datetime,
        *,
        recovery_count: int,
    ) -> WorkerJob:
        return _replace_job(
            previous,
            phase=JobPhase.FAILED,
            recovery_count=recovery_count,
            error_code=WORKER_RECOVERY_EXHAUSTED,
            lease_owner=None,
            lease_acquired_at=None,
            lease_expires_at=None,
            finished_at=finished_at,
        )

    def _finish(
        self,
        previous: WorkerJob,
        *,
        phase: JobPhase,
        result_ref: str | None,
        error_code: str | None,
        finished_at: datetime,
    ) -> WorkerJob:
        if previous.phase in {
            JobPhase.SUCCEEDED,
            JobPhase.FAILED,
            JobPhase.CANCELLED,
        }:
            raise InvalidPhaseError("종단 작업은 다시 종료할 수 없다")
        finished = _replace_job(
            previous,
            phase=phase,
            result_ref=result_ref,
            error_code=error_code,
            lease_owner=None,
            lease_acquired_at=None,
            lease_expires_at=None,
            finished_at=finished_at,
        )
        assert_job_transition(previous, finished)
        return finished

    def _get_scoped(self, tenant_id: str, job_id: UUID) -> WorkerJob:
        job = self._jobs.get(job_id)
        if job is None or job.tenant_id != tenant_id:
            raise JobNotFoundError("tenant 범위에서 작업을 찾을 수 없다")
        return job

    def _require_owned_lease(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        lease_owner: str,
        lease_generation: int,
        observed_at: datetime,
        expected_phase: JobPhase | None = None,
    ) -> WorkerJob:
        job = self._get_scoped(tenant_id, job_id)
        if expected_phase is not None and job.phase is not expected_phase:
            raise StaleLeaseError("현재 작업 phase가 요청한 lease 작업과 일치하지 않는다")
        if job.phase not in ACTIVE_LEASE_PHASES:
            raise StaleLeaseError("현재 작업에는 활성 lease가 없다")
        if job.lease_owner != lease_owner or job.lease_generation != lease_generation:
            raise StaleLeaseError("lease owner 또는 generation이 현재 값과 일치하지 않는다")
        if job.lease_expires_at is None or job.lease_expires_at <= observed_at:
            raise StaleLeaseError("lease가 만료되어 작업 결과를 반영할 수 없다")
        return job


class InvalidPhaseError(JobStoreError):
    """요청한 저장소 연산과 현재 phase가 일치하지 않는다."""
