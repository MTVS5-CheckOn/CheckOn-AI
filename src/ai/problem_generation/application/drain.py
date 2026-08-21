"""문제 생성 큐를 유한 사이클로 비우는 인프로세스 드레인."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable, Sequence

from ai.contracts.agents import WorkerJob

logger = logging.getLogger(__name__)

type RunNext = Callable[[str], Awaitable[WorkerJob | None]]
type DiscoverTenants = Callable[[], Awaitable[Sequence[str]]]


class ProblemDrainLoop:
    """알림받은 테넌트의 큐를 상한 있는 사이클로 처리한다."""

    def __init__(
        self,
        *,
        run_next: RunNext,
        discover_tenants: DiscoverTenants | None = None,
        max_jobs_per_cycle: int,
        cycle_interval_seconds: float,
        idle_interval_seconds: float,
        failure_backoff_initial_seconds: float,
        failure_backoff_max_seconds: float,
        shutdown_grace_seconds: float,
    ) -> None:
        if max_jobs_per_cycle < 1:
            raise ValueError("max_jobs_per_cycle은 1 이상이어야 한다")
        intervals = {
            "cycle_interval_seconds": cycle_interval_seconds,
            "idle_interval_seconds": idle_interval_seconds,
            "failure_backoff_initial_seconds": failure_backoff_initial_seconds,
            "failure_backoff_max_seconds": failure_backoff_max_seconds,
            "shutdown_grace_seconds": shutdown_grace_seconds,
        }
        invalid = [name for name, value in intervals.items() if value <= 0]
        if invalid:
            raise ValueError(f"드레인 시간 설정은 0보다 커야 한다: {invalid}")
        if failure_backoff_max_seconds < failure_backoff_initial_seconds:
            raise ValueError("failure_backoff_max_seconds는 initial 이상이어야 한다")

        self._run_next = run_next
        self._discover_tenants = discover_tenants
        self._max_jobs_per_cycle = max_jobs_per_cycle
        self._cycle_interval_seconds = cycle_interval_seconds
        self._idle_interval_seconds = idle_interval_seconds
        self._failure_backoff_initial_seconds = failure_backoff_initial_seconds
        self._failure_backoff_max_seconds = failure_backoff_max_seconds
        self._shutdown_grace_seconds = shutdown_grace_seconds
        self._tenant_queue: asyncio.Queue[str] | None = None
        self._scheduled_tenants: set[str] = set()
        self._stop_event: asyncio.Event | None = None
        self._cycle_done: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        """배경 태스크가 살아 있는지 반환한다."""

        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        """현재 이벤트 루프에 배경 태스크를 한 번만 시작한다."""

        if self.is_running:
            return
        self._tenant_queue = asyncio.Queue()
        self._scheduled_tenants.clear()
        self._stop_event = asyncio.Event()
        self._cycle_done = asyncio.Event()
        self._cycle_done.set()
        self._task = asyncio.create_task(
            self._run(), name="problem-generation-drain"
        )

    def notify_tenant(self, tenant_id: str) -> None:
        """새 잡이 생긴 테넌트를 중복 없이 다음 사이클에 예약한다."""

        if not tenant_id:
            raise ValueError("tenant_id는 비어 있을 수 없다")
        if not self.is_running or self._tenant_queue is None:
            return
        if tenant_id in self._scheduled_tenants:
            return
        self._scheduled_tenants.add(tenant_id)
        self._tenant_queue.put_nowait(tenant_id)

    async def drain_cycle(self, tenant_id: str) -> int:
        """한 테넌트에서 설정 상한까지만 기존 run_next 경로를 실행한다."""

        processed = 0
        for _ in range(self._max_jobs_per_cycle):
            job = await self._run_next(tenant_id)
            if job is None:
                break
            processed += 1
        return processed

    async def stop(self) -> None:
        """진행 중 사이클을 유예시간 안에 정리한 뒤 태스크를 cancel·await한다."""

        task = self._task
        if task is None:
            return
        if self._stop_event is not None:
            self._stop_event.set()
        if self._cycle_done is not None and not self._cycle_done.is_set():
            try:
                await asyncio.wait_for(
                    self._cycle_done.wait(), timeout=self._shutdown_grace_seconds
                )
            except TimeoutError:
                logger.error(
                    "PG 배경 드레인 종료 유예시간 초과 seconds=%s",
                    self._shutdown_grace_seconds,
                )
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            logger.debug("PG 배경 드레인 태스크 취소 완료")
        finally:
            self._task = None
            self._tenant_queue = None
            self._scheduled_tenants.clear()

    async def _run(self) -> None:
        consecutive_failures = 0
        while self._stop_event is not None and not self._stop_event.is_set():
            tenant_id = await self._next_tenant()
            if tenant_id is None:
                continue
            assert self._cycle_done is not None
            self._cycle_done.clear()
            try:
                processed = await self.drain_cycle(tenant_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                consecutive_failures += 1
                logger.exception(
                    "PG 배경 드레인 사이클 실패 tenant=%s consecutive_failures=%d",
                    tenant_id,
                    consecutive_failures,
                )
                self.notify_tenant(tenant_id)
                await self._wait_or_stop(self._failure_backoff(consecutive_failures))
            else:
                consecutive_failures = 0
                if processed == self._max_jobs_per_cycle:
                    self.notify_tenant(tenant_id)
                delay = (
                    self._idle_interval_seconds
                    if processed == 0
                    else self._cycle_interval_seconds
                )
                await self._wait_or_stop(delay)
            finally:
                self._cycle_done.set()

    async def _next_tenant(self) -> str | None:
        assert self._tenant_queue is not None
        try:
            return self._take_scheduled_tenant()
        except asyncio.QueueEmpty:
            pass

        if self._discover_tenants is not None:
            try:
                for tenant_id in await self._discover_tenants():
                    self.notify_tenant(tenant_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("PG 배경 드레인 pending tenant 재발견 실패")
            try:
                return self._take_scheduled_tenant()
            except asyncio.QueueEmpty:
                pass

        try:
            tenant_id = await asyncio.wait_for(
                self._tenant_queue.get(), timeout=self._idle_interval_seconds
            )
        except TimeoutError:
            return None
        self._scheduled_tenants.discard(tenant_id)
        return tenant_id

    def _take_scheduled_tenant(self) -> str:
        assert self._tenant_queue is not None
        tenant_id = self._tenant_queue.get_nowait()
        self._scheduled_tenants.discard(tenant_id)
        return tenant_id

    def _failure_backoff(self, consecutive_failures: int) -> float:
        max_doublings = max(
            0,
            math.ceil(
                math.log2(
                    self._failure_backoff_max_seconds
                    / self._failure_backoff_initial_seconds
                )
            ),
        )
        multiplier = 2.0 ** min(consecutive_failures - 1, max_doublings)
        return min(
            self._failure_backoff_initial_seconds * multiplier,
            self._failure_backoff_max_seconds,
        )

    async def _wait_or_stop(self, delay_seconds: float) -> None:
        assert self._stop_event is not None
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay_seconds)
        except TimeoutError:
            return


__all__ = ["DiscoverTenants", "ProblemDrainLoop", "RunNext"]
