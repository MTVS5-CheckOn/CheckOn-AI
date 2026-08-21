"""문제 생성 lease heartbeat의 fencing·정리 계약."""

from __future__ import annotations

import asyncio

import pytest

from ai.problem_generation.application.lease_heartbeat import (
    run_with_lease_heartbeat,
)


def test_renews_until_operation_completes_and_leaves_no_task() -> None:
    renewals = 0
    renewed_twice = asyncio.Event()

    async def scenario() -> str:
        nonlocal renewals

        async def renew() -> None:
            nonlocal renewals
            renewals += 1
            if renewals == 2:
                renewed_twice.set()

        async def operation() -> str:
            await renewed_twice.wait()
            return "result-ref"

        result = await run_with_lease_heartbeat(
            operation(),
            renew=renew,
            interval_seconds=0.01,
            max_duration_seconds=1.0,
        )
        assert not any(
            task.get_name() == "problem-generation-lease-heartbeat" and not task.done()
            for task in asyncio.all_tasks()
        )
        return result

    assert asyncio.run(scenario()) == "result-ref"
    assert renewals >= 2


def test_heartbeat_failure_cancels_operation_and_propagates() -> None:
    operation_cancelled = asyncio.Event()

    async def scenario() -> None:
        async def renew() -> None:
            raise RuntimeError("stale lease")

        async def operation() -> None:
            try:
                await asyncio.Event().wait()
            finally:
                operation_cancelled.set()

        with pytest.raises(RuntimeError, match="stale lease"):
            await run_with_lease_heartbeat(
                operation(),
                renew=renew,
                interval_seconds=0.001,
                max_duration_seconds=1.0,
            )
        assert operation_cancelled.is_set()

    asyncio.run(scenario())


def test_rejects_non_positive_interval_without_leaking_coroutine() -> None:
    async def scenario() -> None:
        async def operation() -> None:
            return None

        coroutine = operation()
        try:
            with pytest.raises(ValueError, match="0보다 커야"):
                await run_with_lease_heartbeat(
                    coroutine,
                    renew=operation,
                    interval_seconds=0,
                    max_duration_seconds=1.0,
                )
        finally:
            coroutine.close()

    asyncio.run(scenario())


def test_total_duration_limit_cancels_the_operation() -> None:
    operation_cancelled = asyncio.Event()

    async def scenario() -> None:
        async def renew() -> None:
            return None

        async def operation() -> None:
            try:
                await asyncio.Event().wait()
            finally:
                operation_cancelled.set()

        with pytest.raises(TimeoutError):
            await run_with_lease_heartbeat(
                operation(),
                renew=renew,
                interval_seconds=0.001,
                max_duration_seconds=0.01,
            )
        assert operation_cancelled.is_set()

    asyncio.run(scenario())
