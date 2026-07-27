"""PostgreSQL 기반 슈퍼바이저 WorkerJob 실행 원장.

큐 선택과 phase 전이는 모두 단일 DB 트랜잭션에서 행 잠금으로 직렬화한다.
owner·lease_generation·만료시각을 조건에 포함해 만료된 실행자의 후속 저장을 막는다.
Kafka outbox는 백엔드 이벤트 계약 확정 전이므로 이 저장소 범위에 포함하지 않는다.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, Never
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import Select, case, func, literal, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from ai.agents.job_store import (
    InvalidPhaseError,
    JobAlreadyExistsError,
    JobNotFoundError,
    JobStoreError,
    StaleLeaseError,
)
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
from ai.db.models import AgentRun

logger = logging.getLogger(__name__)


def _evolve(job: WorkerJob, **changes: object) -> WorkerJob:
    values: dict[str, object] = {name: getattr(job, name) for name in WorkerJob.model_fields}
    values.update(changes)
    return WorkerJob.model_validate(values)


def _ensure_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}은 timezone-aware datetime이어야 한다")


def _sqlstate(exc: IntegrityError) -> str | None:
    original = exc.orig
    return getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)


def _priority_expression(
    *,
    selected_at: datetime,
    priority_aging_interval: timedelta,
) -> ColumnElement[Any]:
    interval_seconds = priority_aging_interval.total_seconds()
    base_level = case(
        (AgentRun.priority_class == PriorityClass.INTERACTIVE.value, 2),
        (AgentRun.priority_class == PriorityClass.STANDARD.value, 1),
        else_=0,
    )
    waited_seconds = func.greatest(
        func.extract("epoch", literal(selected_at) - AgentRun.queued_at),
        0,
    )
    promotion_steps = func.floor(waited_seconds / interval_seconds)
    return func.least(2, base_level + promotion_steps)


def build_lease_next_query(
    *,
    tenant_id: str,
    worker_kind: WorkerKind,
    acquired_at: datetime,
    priority_aging_interval: timedelta,
) -> Select[tuple[AgentRun]]:
    """aging·결정론 정렬과 ``SKIP LOCKED``가 포함된 lease 후보 쿼리."""

    _ensure_aware(acquired_at, "acquired_at")
    if priority_aging_interval <= timedelta(0):
        raise ValueError("priority_aging_interval은 0보다 커야 한다")
    effective_priority = _priority_expression(
        selected_at=acquired_at,
        priority_aging_interval=priority_aging_interval,
    )
    return (
        select(AgentRun)
        .where(
            AgentRun.tenant_id == tenant_id,
            AgentRun.agent_kind == worker_kind.value,
            AgentRun.status == JobPhase.QUEUED.value,
        )
        .order_by(
            effective_priority.desc(),
            AgentRun.queued_at,
            AgentRun.id,
        )
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def build_owned_lease_query(
    *,
    tenant_id: str,
    job_id: UUID,
    lease_owner: str,
    lease_generation: int,
    observed_at: datetime,
    expected_phase: JobPhase | None = None,
) -> Select[tuple[AgentRun]]:
    """tenant scope와 fencing 조건을 모두 SQL에 넣은 소유 lease 쿼리."""

    _ensure_aware(observed_at, "observed_at")
    phases = (
        [expected_phase.value]
        if expected_phase is not None
        else sorted(phase.value for phase in ACTIVE_LEASE_PHASES)
    )
    return (
        select(AgentRun)
        .where(
            AgentRun.tenant_id == tenant_id,
            AgentRun.id == job_id,
            AgentRun.status.in_(phases),
            AgentRun.lease_owner == lease_owner,
            AgentRun.lease_generation == lease_generation,
            AgentRun.lease_expires_at > observed_at,
        )
        .with_for_update()
    )


class PgJobStore:
    """멀티 프로세스용 PostgreSQL JobStore.

    DB 오류와 손상된 원장 행은 ``JobStoreError``로 fail-closed 처리한다. 상태 전이는
    계약 검증 후 같은 행 잠금 트랜잭션 안에서 반영한다.
    """

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sessionmaker = sessionmaker

    async def add(self, job: WorkerJob) -> WorkerJob:
        if (
            job.phase is not JobPhase.QUEUED
            or job.dispatch_attempt != 0
            or job.lease_generation != 0
            or job.recovery_count != 0
        ):
            raise ValueError("새 작업은 최초 queued 상태여야 한다")

        async def operation(session: AsyncSession) -> WorkerJob:
            session.add(_row_from_job(job))
            await session.flush()
            return job

        return await self._transaction(
            "작업 등록",
            operation,
            duplicate_job_id=job.job_id,
        )

    async def get(self, *, tenant_id: str, job_id: UUID) -> WorkerJob | None:
        stmt = select(AgentRun).where(
            AgentRun.tenant_id == tenant_id,
            AgentRun.id == job_id,
        )
        try:
            async with self._sessionmaker() as session:
                row = (await session.execute(stmt)).scalar_one_or_none()
        except SQLAlchemyError as exc:
            self._raise_db_error("작업 조회", exc)
        return _job_from_row(row) if row is not None else None

    async def recover_expired(
        self,
        *,
        tenant_id: str,
        worker_kind: WorkerKind,
        recovered_at: datetime,
    ) -> tuple[WorkerJob, ...]:
        _ensure_aware(recovered_at, "recovered_at")

        async def operation(session: AsyncSession) -> tuple[WorkerJob, ...]:
            stmt = (
                select(AgentRun)
                .where(
                    AgentRun.tenant_id == tenant_id,
                    AgentRun.agent_kind == worker_kind.value,
                    AgentRun.status.in_(sorted(phase.value for phase in ACTIVE_LEASE_PHASES)),
                    AgentRun.lease_expires_at <= recovered_at,
                )
                .order_by(AgentRun.queued_at, AgentRun.id)
                .with_for_update(skip_locked=True)
            )
            rows = (await session.execute(stmt)).scalars().all()
            recovered: list[WorkerJob] = []
            for row in rows:
                previous = _job_from_row(row)
                recovery_count = previous.recovery_count + 1
                if recovery_count >= previous.max_recovery_attempts:
                    current = _evolve(
                        previous,
                        phase=JobPhase.FAILED,
                        recovery_count=recovery_count,
                        error_code=WORKER_RECOVERY_EXHAUSTED,
                        lease_owner=None,
                        lease_acquired_at=None,
                        lease_expires_at=None,
                        finished_at=recovered_at,
                    )
                else:
                    current = _evolve(
                        previous,
                        phase=JobPhase.QUEUED,
                        recovery_count=recovery_count,
                        lease_owner=None,
                        lease_acquired_at=None,
                        lease_expires_at=None,
                    )
                assert_expired_recovery(previous, current, recovered_at=recovered_at)
                _apply_job(row, current, updated_at=recovered_at)
                recovered.append(current)
            return tuple(recovered)

        return await self._transaction("만료 lease 회수", operation)

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
        if not lease_owner:
            raise ValueError("lease_owner는 비어 있을 수 없다")
        _ensure_aware(acquired_at, "acquired_at")
        _ensure_aware(expires_at, "expires_at")
        if expires_at <= acquired_at:
            raise ValueError("expires_at은 acquired_at보다 뒤여야 한다")
        stmt = build_lease_next_query(
            tenant_id=tenant_id,
            worker_kind=worker_kind,
            acquired_at=acquired_at,
            priority_aging_interval=priority_aging_interval,
        )

        async def operation(session: AsyncSession) -> WorkerJob | None:
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                return None
            previous = _job_from_row(row)
            current = _evolve(
                previous,
                phase=JobPhase.LEASED,
                dispatch_attempt=previous.dispatch_attempt + 1,
                lease_generation=previous.lease_generation + 1,
                lease_owner=lease_owner,
                lease_acquired_at=acquired_at,
                lease_expires_at=expires_at,
            )
            assert_job_transition(previous, current)
            _apply_job(row, current, updated_at=acquired_at)
            return current

        return await self._transaction("다음 작업 lease", operation)

    async def resume(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        resumed_at: datetime,
    ) -> WorkerJob:
        _ensure_aware(resumed_at, "resumed_at")

        async def operation(session: AsyncSession) -> WorkerJob:
            row = await self._locked_scoped_row(session, tenant_id, job_id)
            previous = _job_from_row(row)
            if previous.phase is not JobPhase.PAUSED:
                raise InvalidPhaseError("paused 작업만 resume할 수 있다")
            current = _evolve(previous, phase=JobPhase.QUEUED)
            assert_paused_resume(previous, current)
            _apply_job(row, current, updated_at=resumed_at)
            return current

        return await self._transaction("작업 resume", operation)

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
        _ensure_aware(started_at, "started_at")

        async def operation(session: AsyncSession) -> WorkerJob:
            row = await self._owned_row(
                session,
                tenant_id=tenant_id,
                job_id=job_id,
                lease_owner=lease_owner,
                lease_generation=lease_generation,
                observed_at=started_at,
                expected_phase=JobPhase.LEASED,
            )
            previous = _job_from_row(row)
            current = _evolve(
                previous,
                phase=JobPhase.RUNNING,
                checkpoint_ref=checkpoint_ref,
                started_at=previous.started_at or started_at,
            )
            assert_job_transition(previous, current)
            _apply_job(row, current, updated_at=started_at)
            return current

        return await self._transaction("작업 running 전이", operation)

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
        _ensure_aware(renewed_at, "renewed_at")
        _ensure_aware(expires_at, "expires_at")

        async def operation(session: AsyncSession) -> WorkerJob:
            row = await self._owned_row(
                session,
                tenant_id=tenant_id,
                job_id=job_id,
                lease_owner=lease_owner,
                lease_generation=lease_generation,
                observed_at=renewed_at,
            )
            previous = _job_from_row(row)
            if previous.lease_expires_at is None or expires_at <= previous.lease_expires_at:
                raise ValueError("갱신 lease 만료시각은 기존 만료시각보다 뒤여야 한다")
            current = _evolve(previous, lease_expires_at=expires_at)
            _apply_job(row, current, updated_at=renewed_at)
            return current

        return await self._transaction("lease 갱신", operation)

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
        _ensure_aware(paused_at, "paused_at")

        async def operation(session: AsyncSession) -> WorkerJob:
            row = await self._owned_row(
                session,
                tenant_id=tenant_id,
                job_id=job_id,
                lease_owner=lease_owner,
                lease_generation=lease_generation,
                observed_at=paused_at,
                expected_phase=JobPhase.RUNNING,
            )
            previous = _job_from_row(row)
            current = _evolve(
                previous,
                phase=JobPhase.PAUSED,
                checkpoint_ref=checkpoint_ref,
                lease_owner=None,
                lease_acquired_at=None,
                lease_expires_at=None,
            )
            assert_job_transition(previous, current)
            _apply_job(row, current, updated_at=paused_at)
            return current

        return await self._transaction("작업 pause", operation)

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
        return await self._finish_owned(
            tenant_id=tenant_id,
            job_id=job_id,
            lease_owner=lease_owner,
            lease_generation=lease_generation,
            phase=JobPhase.SUCCEEDED,
            result_ref=result_ref,
            error_code=None,
            finished_at=finished_at,
            expected_phase=JobPhase.RUNNING,
        )

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
        return await self._finish_owned(
            tenant_id=tenant_id,
            job_id=job_id,
            lease_owner=lease_owner,
            lease_generation=lease_generation,
            phase=JobPhase.FAILED,
            result_ref=result_ref,
            error_code=error_code,
            finished_at=finished_at,
            expected_phase=None,
        )

    async def cancel_waiting(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        error_code: str | None,
        finished_at: datetime,
    ) -> WorkerJob:
        _ensure_aware(finished_at, "finished_at")

        async def operation(session: AsyncSession) -> WorkerJob:
            row = await self._locked_scoped_row(session, tenant_id, job_id)
            previous = _job_from_row(row)
            if previous.phase not in {JobPhase.QUEUED, JobPhase.PAUSED}:
                raise InvalidPhaseError("외부 취소는 queued/paused 작업만 허용한다")
            current = _finish_job(
                previous,
                phase=JobPhase.CANCELLED,
                result_ref=None,
                error_code=error_code,
                finished_at=finished_at,
            )
            _apply_job(row, current, updated_at=finished_at)
            return current

        return await self._transaction("대기 작업 취소", operation)

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
        _ensure_aware(finished_at, "finished_at")

        async def operation(session: AsyncSession) -> WorkerJob:
            row = await self._owned_row(
                session,
                tenant_id=tenant_id,
                job_id=job_id,
                lease_owner=lease_owner,
                lease_generation=lease_generation,
                observed_at=finished_at,
            )
            previous = _job_from_row(row)
            if previous.phase is JobPhase.RUNNING and not checkpoint_ref:
                raise ValueError("running 작업 취소에는 안전 경계의 checkpoint_ref가 필요하다")
            if checkpoint_ref is not None:
                previous = _evolve(previous, checkpoint_ref=checkpoint_ref)
            current = _finish_job(
                previous,
                phase=JobPhase.CANCELLED,
                result_ref=None,
                error_code=error_code,
                finished_at=finished_at,
            )
            _apply_job(row, current, updated_at=finished_at)
            return current

        return await self._transaction("소유 작업 취소", operation)

    async def _finish_owned(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        lease_owner: str,
        lease_generation: int,
        phase: JobPhase,
        result_ref: str | None,
        error_code: str | None,
        finished_at: datetime,
        expected_phase: JobPhase | None,
    ) -> WorkerJob:
        _ensure_aware(finished_at, "finished_at")

        async def operation(session: AsyncSession) -> WorkerJob:
            row = await self._owned_row(
                session,
                tenant_id=tenant_id,
                job_id=job_id,
                lease_owner=lease_owner,
                lease_generation=lease_generation,
                observed_at=finished_at,
                expected_phase=expected_phase,
            )
            previous = _job_from_row(row)
            current = _finish_job(
                previous,
                phase=phase,
                result_ref=result_ref,
                error_code=error_code,
                finished_at=finished_at,
            )
            _apply_job(row, current, updated_at=finished_at)
            return current

        return await self._transaction(f"작업 {phase.value} 전이", operation)

    async def _locked_scoped_row(
        self,
        session: AsyncSession,
        tenant_id: str,
        job_id: UUID,
    ) -> AgentRun:
        stmt = (
            select(AgentRun)
            .where(
                AgentRun.tenant_id == tenant_id,
                AgentRun.id == job_id,
            )
            .with_for_update()
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise JobNotFoundError("tenant 범위에서 작업을 찾을 수 없다")
        return row

    async def _owned_row(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        job_id: UUID,
        lease_owner: str,
        lease_generation: int,
        observed_at: datetime,
        expected_phase: JobPhase | None = None,
    ) -> AgentRun:
        stmt = build_owned_lease_query(
            tenant_id=tenant_id,
            job_id=job_id,
            lease_owner=lease_owner,
            lease_generation=lease_generation,
            observed_at=observed_at,
            expected_phase=expected_phase,
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            return row
        await self._locked_scoped_row(session, tenant_id, job_id)
        raise StaleLeaseError(
            "phase·lease owner·generation·만료시각 중 하나가 현재 값과 일치하지 않는다"
        )

    async def _transaction[T](
        self,
        action: str,
        operation: Callable[[AsyncSession], Awaitable[T]],
        *,
        duplicate_job_id: UUID | None = None,
    ) -> T:
        try:
            async with self._sessionmaker() as session:
                async with session.begin():
                    return await operation(session)
        except JobStoreError:
            raise
        except IntegrityError as exc:
            if duplicate_job_id is not None and _sqlstate(exc) == "23505":
                raise JobAlreadyExistsError(f"이미 존재하는 job_id: {duplicate_job_id}") from exc
            self._raise_db_error(action, exc)
        except SQLAlchemyError as exc:
            self._raise_db_error(action, exc)

    @staticmethod
    def _raise_db_error(action: str, exc: SQLAlchemyError) -> Never:
        logger.error("슈퍼바이저 원장 DB 오류 — fail-closed action=%s", action, exc_info=True)
        raise JobStoreError(f"{action} 중 DB 오류가 발생했다") from exc


def _finish_job(
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
    current = _evolve(
        previous,
        phase=phase,
        result_ref=result_ref,
        error_code=error_code,
        lease_owner=None,
        lease_acquired_at=None,
        lease_expires_at=None,
        finished_at=finished_at,
    )
    assert_job_transition(previous, current)
    return current


def _row_from_job(job: WorkerJob) -> AgentRun:
    return AgentRun(
        id=job.job_id,
        run_id=job.execution_id,
        tenant_id=job.tenant_id,
        agent_kind=job.worker_kind.value,
        operation=job.operation.value,
        payload_ref=job.payload_ref,
        payload_hash=job.payload_hash,
        priority_class=job.priority_class.value,
        dispatch_attempt=job.dispatch_attempt,
        lease_generation=job.lease_generation,
        recovery_count=job.recovery_count,
        max_recovery_attempts=job.max_recovery_attempts,
        lease_owner=job.lease_owner,
        lease_acquired_at=job.lease_acquired_at,
        lease_expires_at=job.lease_expires_at,
        checkpoint_ref=job.checkpoint_ref,
        result_ref=job.result_ref,
        error_code=job.error_code,
        queued_at=job.queued_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        state_checkpoint={},
        progress="0",
        status=job.phase.value,
        updated_at=job.queued_at,
    )


def _job_from_row(row: AgentRun) -> WorkerJob:
    try:
        return WorkerJob(
            job_id=row.id,
            execution_id=row.run_id,
            tenant_id=row.tenant_id,
            worker_kind=WorkerKind(row.agent_kind),
            operation=row.operation,
            payload_ref=row.payload_ref,
            payload_hash=row.payload_hash,
            phase=row.status,
            priority_class=row.priority_class,
            dispatch_attempt=row.dispatch_attempt,
            lease_generation=row.lease_generation,
            recovery_count=row.recovery_count,
            max_recovery_attempts=row.max_recovery_attempts,
            lease_owner=row.lease_owner,
            lease_acquired_at=row.lease_acquired_at,
            lease_expires_at=row.lease_expires_at,
            checkpoint_ref=row.checkpoint_ref,
            result_ref=row.result_ref,
            error_code=row.error_code,
            queued_at=row.queued_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )
    except (ValidationError, ValueError) as exc:
        raise JobStoreError(f"손상된 AgentRun 원장 행: {row.id}") from exc


def _apply_job(row: AgentRun, job: WorkerJob, *, updated_at: datetime) -> None:
    row.status = job.phase.value
    row.dispatch_attempt = job.dispatch_attempt
    row.lease_generation = job.lease_generation
    row.recovery_count = job.recovery_count
    row.lease_owner = job.lease_owner
    row.lease_acquired_at = job.lease_acquired_at
    row.lease_expires_at = job.lease_expires_at
    row.checkpoint_ref = job.checkpoint_ref
    row.result_ref = job.result_ref
    row.error_code = job.error_code
    row.started_at = job.started_at
    row.finished_at = job.finished_at
    row.updated_at = updated_at
