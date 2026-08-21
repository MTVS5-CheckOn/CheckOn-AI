"""문제 생성 실행 중 lease를 유지하는 비동기 경계."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any


class LeaseHeartbeatFailed(RuntimeError):
    """실행 중 lease 갱신 실패로 작업을 계속할 수 없음."""


async def run_with_lease_heartbeat[T](
    operation: Coroutine[Any, Any, T],
    *,
    renew: Callable[[], Awaitable[object]],
    interval_seconds: float,
    max_duration_seconds: float,
) -> T:
    """작업이 끝날 때까지 lease를 갱신하고, 갱신 실패 시 작업을 중단한다.

    heartbeat 실패는 fencing 소유권을 더는 증명할 수 없다는 뜻이다. 이때 작업을 계속하면
    회수된 새 워커와 같은 잡을 동시에 실행할 수 있으므로 fail-closed로 취소한다.

    🔴 「주기 < lease 만료」 관계의 정본은 여기가 아니다. `api/routers/problem.py`의
    `ProblemRouterSettings.validate_lease_heartbeat`가 기동에서 fail-closed로 검증한다
    (합의 ② · 2026-08-21 A·B). 이 함수가 재는 관계는 「총 상한 > 주기」뿐이며,
    함수와 이 함수의 검사만 읽으면 앞 관계가 없다고 판정될 수 있다(2026-08-22 사례).
    """

    if interval_seconds <= 0:
        raise ValueError("lease heartbeat 주기는 0보다 커야 한다")
    if max_duration_seconds <= interval_seconds:
        raise ValueError("lease heartbeat 총 상한은 갱신 주기보다 커야 한다")

    async def heartbeat_loop() -> None:
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                await renew()
            except Exception as exc:
                raise LeaseHeartbeatFailed from exc

    operation_task: asyncio.Task[T] = asyncio.create_task(
        operation, name="problem-generation-operation"
    )
    heartbeat_task: asyncio.Task[None] = asyncio.create_task(
        heartbeat_loop(), name="problem-generation-lease-heartbeat"
    )
    try:
        async with asyncio.timeout(max_duration_seconds):
            done, _ = await asyncio.wait(
                {operation_task, heartbeat_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        if heartbeat_task in done:
            heartbeat_task.result()
            raise LeaseHeartbeatFailed
        return operation_task.result()
    finally:
        for task in (operation_task, heartbeat_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(operation_task, heartbeat_task, return_exceptions=True)


__all__ = ["LeaseHeartbeatFailed", "run_with_lease_heartbeat"]
