"""LLM 없는 결정론 슈퍼바이저.

슈퍼바이저는 작업 본문이나 워커 산출물을 읽지 않는다. 고정 operation 라우팅,
tenant 범위의 lease, checkpoint 재개 신호, 불투명한 result 참조만 관리한다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from ai.agents.job_store import JobStore
from ai.contracts.agents import JobPhase, WorkerJob, WorkerKind, worker_for_operation

Clock = Callable[[], datetime]


def system_utc_now() -> datetime:
    """프로덕션 기본 시계. 테스트는 고정 시계를 주입한다."""

    return datetime.now(UTC)


class Supervisor:
    """영속 ``JobStore`` 위에서 실행 전이를 조정하는 결정론 서비스."""

    def __init__(
        self,
        *,
        store: JobStore,
        lease_duration: timedelta,
        priority_aging_interval: timedelta,
        clock: Clock = system_utc_now,
    ) -> None:
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration은 0보다 커야 한다")
        if priority_aging_interval <= timedelta(0):
            raise ValueError("priority_aging_interval은 0보다 커야 한다")
        self._store = store
        self._lease_duration = lease_duration
        self._priority_aging_interval = priority_aging_interval
        self._clock = clock

    async def enqueue(self, job: WorkerJob) -> WorkerJob:
        """최초 queued 작업을 저장한다."""

        if (
            job.phase is not JobPhase.QUEUED
            or job.dispatch_attempt != 0
            or job.lease_generation != 0
            or job.recovery_count != 0
        ):
            raise ValueError("최초 queued 작업만 enqueue할 수 있다")
        return await self._store.add(job)

    async def get(self, *, tenant_id: str, job_id: UUID) -> WorkerJob | None:
        """tenant 범위에서 작업 메타만 조회한다."""

        _require_nonempty("tenant_id", tenant_id)
        return await self._store.get(tenant_id=tenant_id, job_id=job_id)

    async def lease_next(
        self,
        *,
        tenant_id: str,
        worker_kind: WorkerKind,
        lease_owner: str,
    ) -> WorkerJob | None:
        """만료 작업을 먼저 회수한 뒤 다음 queued 작업 하나를 원자적으로 lease한다."""

        _require_nonempty("tenant_id", tenant_id)
        _require_nonempty("lease_owner", lease_owner)
        now = self._now()
        await self._store.recover_expired(
            tenant_id=tenant_id,
            worker_kind=worker_kind,
            recovered_at=now,
        )
        return await self._store.lease_next(
            tenant_id=tenant_id,
            worker_kind=worker_kind,
            lease_owner=lease_owner,
            acquired_at=now,
            expires_at=now + self._lease_duration,
            priority_aging_interval=self._priority_aging_interval,
        )

    async def recover_expired(
        self,
        *,
        tenant_id: str,
        worker_kind: WorkerKind,
    ) -> tuple[WorkerJob, ...]:
        """해당 tenant·worker의 만료 lease를 queued 또는 failed로 수렴시킨다."""

        _require_nonempty("tenant_id", tenant_id)
        return await self._store.recover_expired(
            tenant_id=tenant_id,
            worker_kind=worker_kind,
            recovered_at=self._now(),
        )

    async def resume(self, *, tenant_id: str, job_id: UUID) -> WorkerJob:
        """paused 작업에 명시적 resume 신호를 보내 queued로 되돌린다."""

        _require_nonempty("tenant_id", tenant_id)
        return await self._store.resume(
            tenant_id=tenant_id,
            job_id=job_id,
            resumed_at=self._now(),
        )

    async def start(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        lease_owner: str,
        lease_generation: int,
        checkpoint_ref: str,
    ) -> WorkerJob:
        """초기 또는 회수된 checkpoint를 연결하고 leased 작업을 실행 중으로 바꾼다."""

        _require_nonempty("checkpoint_ref", checkpoint_ref)
        return await self._store.mark_running(
            tenant_id=tenant_id,
            job_id=job_id,
            lease_owner=lease_owner,
            lease_generation=lease_generation,
            checkpoint_ref=checkpoint_ref,
            started_at=self._now(),
        )

    async def heartbeat(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        lease_owner: str,
        lease_generation: int,
    ) -> WorkerJob:
        """현재 fencing token이 유효한 워커의 lease를 연장한다."""

        now = self._now()
        return await self._store.renew_lease(
            tenant_id=tenant_id,
            job_id=job_id,
            lease_owner=lease_owner,
            lease_generation=lease_generation,
            renewed_at=now,
            expires_at=now + self._lease_duration,
        )

    async def pause(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        lease_owner: str,
        lease_generation: int,
        checkpoint_ref: str,
    ) -> WorkerJob:
        """안전한 워커 경계의 checkpoint를 보존하고 협력적으로 pause한다."""

        _require_nonempty("checkpoint_ref", checkpoint_ref)
        return await self._store.pause(
            tenant_id=tenant_id,
            job_id=job_id,
            lease_owner=lease_owner,
            lease_generation=lease_generation,
            checkpoint_ref=checkpoint_ref,
            paused_at=self._now(),
        )

    async def succeed(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        lease_owner: str,
        lease_generation: int,
        result_ref: str,
    ) -> WorkerJob:
        """워커 결과를 해석하지 않고 result_ref만 보존해 성공으로 수렴한다."""

        _require_nonempty("result_ref", result_ref)
        return await self._store.succeed(
            tenant_id=tenant_id,
            job_id=job_id,
            lease_owner=lease_owner,
            lease_generation=lease_generation,
            result_ref=result_ref,
            finished_at=self._now(),
        )

    async def fail(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        lease_owner: str,
        lease_generation: int,
        error_code: str,
        result_ref: str | None = None,
    ) -> WorkerJob:
        """복구 불가능한 실행 실패를 기록한다. 보존된 부분 결과 참조는 선택 사항이다."""

        _require_nonempty("error_code", error_code)
        return await self._store.fail(
            tenant_id=tenant_id,
            job_id=job_id,
            lease_owner=lease_owner,
            lease_generation=lease_generation,
            error_code=error_code,
            result_ref=result_ref,
            finished_at=self._now(),
        )

    async def cancel_waiting(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        error_code: str | None = None,
    ) -> WorkerJob:
        """외부 요청으로 queued/paused 작업만 취소한다."""

        return await self._store.cancel_waiting(
            tenant_id=tenant_id,
            job_id=job_id,
            error_code=error_code,
            finished_at=self._now(),
        )

    async def cancel_owned(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        lease_owner: str,
        lease_generation: int,
        checkpoint_ref: str | None,
        error_code: str | None = None,
    ) -> WorkerJob:
        """현재 워커가 안전한 checkpoint 경계에서 leased/running 작업을 취소한다."""

        return await self._store.cancel_owned(
            tenant_id=tenant_id,
            job_id=job_id,
            lease_owner=lease_owner,
            lease_generation=lease_generation,
            checkpoint_ref=checkpoint_ref,
            error_code=error_code,
            finished_at=self._now(),
        )

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock은 timezone-aware datetime을 반환해야 한다")
        return now


def route_worker(job: WorkerJob) -> WorkerKind:
    """job operation을 고정 라우팅 표로 재확인한다."""

    return worker_for_operation(job.operation)


def _require_nonempty(field_name: str, value: str) -> None:
    if not value:
        raise ValueError(f"{field_name}은 비어 있을 수 없다")
