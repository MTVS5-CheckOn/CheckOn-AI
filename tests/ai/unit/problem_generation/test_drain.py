"""문제 생성 배경 드레인의 상한·복구·종료 계약."""

from __future__ import annotations

import asyncio
from typing import cast

from ai.contracts.agents import WorkerJob
from ai.problem_generation.application.drain import ProblemDrainLoop, RunNext


def _loop(
    run_next: RunNext,
    *,
    max_jobs_per_cycle: int = 2,
) -> ProblemDrainLoop:
    return ProblemDrainLoop(
        run_next=run_next,
        max_jobs_per_cycle=max_jobs_per_cycle,
        cycle_interval_seconds=0.001,
        idle_interval_seconds=0.001,
        failure_backoff_initial_seconds=0.001,
        failure_backoff_max_seconds=0.002,
        shutdown_grace_seconds=1.0,
    )


def test_drain_cycle_never_exceeds_job_limit() -> None:
    calls = 0

    async def run_next(_tenant_id: str) -> WorkerJob | None:
        nonlocal calls
        calls += 1
        return cast(WorkerJob, object())

    drain = _loop(run_next, max_jobs_per_cycle=3)

    processed = asyncio.run(drain.drain_cycle("tenant-limit"))

    assert processed == 3
    assert calls == 3


def test_drain_loop_survives_a_cycle_failure() -> None:
    calls = 0
    recovered = asyncio.Event()

    async def run_next(_tenant_id: str) -> WorkerJob | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("일시적 실패")
        recovered.set()
        return None

    async def scenario() -> None:
        drain = _loop(run_next)
        await drain.start()
        drain.notify_tenant("tenant-retry")
        await asyncio.wait_for(recovered.wait(), timeout=1.0)
        assert drain.is_running
        await drain.stop()
        assert not drain.is_running

    asyncio.run(scenario())
    assert calls >= 2


def test_stop_waits_for_active_cycle_then_cleans_up_task() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def run_next(_tenant_id: str) -> WorkerJob | None:
        entered.set()
        await release.wait()
        return None

    async def scenario() -> None:
        drain = _loop(run_next)
        await drain.start()
        drain.notify_tenant("tenant-shutdown")
        await asyncio.wait_for(entered.wait(), timeout=1.0)

        stopping = asyncio.create_task(drain.stop())
        await asyncio.sleep(0)
        assert not stopping.done(), "진행 중 사이클을 정리하기 전에 종료됐다"
        release.set()
        await asyncio.wait_for(stopping, timeout=1.0)

        assert not drain.is_running
        assert not any(
            task.get_name() == "problem-generation-drain" and not task.done()
            for task in asyncio.all_tasks()
        )

    asyncio.run(scenario())
