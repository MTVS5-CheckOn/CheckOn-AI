"""문제 생성 lease heartbeat의 fencing·정리 계약."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest

from ai.agents.supervisor import Supervisor
from ai.contracts.agents import (
    JobPhase,
    OperationKind,
    PriorityClass,
    WorkerJob,
    WorkerKind,
)
from ai.problem_generation.application.lease_heartbeat import (
    LeaseHeartbeatFailed,
    run_with_lease_heartbeat,
)
from ai.problem_generation.assembly import ProblemGenerationRunner


class _RecordingSupervisor:
    def __init__(self) -> None:
        self.failed_error_codes: list[str] = []

    async def fail(self, **kwargs: object) -> None:
        self.failed_error_codes.append(cast(str, kwargs["error_code"]))


class _HeartbeatFailingRunner(ProblemGenerationRunner):
    async def _execute(self, job: WorkerJob) -> WorkerJob:
        async def renew() -> None:
            raise RuntimeError("로그에 남으면 안 되는 원문")

        async def operation() -> WorkerJob:
            await asyncio.Event().wait()
            return job

        return await run_with_lease_heartbeat(
            operation(),
            renew=renew,
            interval_seconds=0.001,
            max_duration_seconds=1.0,
        )


def _leased_job() -> WorkerJob:
    now = datetime(2026, 8, 22, tzinfo=UTC)
    return WorkerJob(
        job_id=uuid4(),
        execution_id=uuid4(),
        tenant_id="tenant-heartbeat-log",
        worker_kind=WorkerKind.PROBLEM_GENERATION,
        operation=OperationKind.PROBLEM_SET_GENERATE,
        payload_ref="problem-request:heartbeat-log",
        payload_hash=f"sha256:{'0' * 64}",
        phase=JobPhase.LEASED,
        priority_class=PriorityClass.STANDARD,
        lease_generation=1,
        lease_owner="worker-heartbeat-log",
        lease_acquired_at=now,
        lease_expires_at=now + timedelta(minutes=5),
        queued_at=now,
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

        with pytest.raises(LeaseHeartbeatFailed) as caught:
            await run_with_lease_heartbeat(
                operation(),
                renew=renew,
                interval_seconds=0.001,
                max_duration_seconds=1.0,
            )
        assert isinstance(caught.value.__cause__, RuntimeError)
        assert str(caught.value.__cause__) == "stale lease"
        assert operation_cancelled.is_set()

    asyncio.run(scenario())


def test_heartbeat_failure_is_logged_without_changing_ledger_error_code(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """heartbeat가 먼저 죽으면 비민감 경고를 남기고 기존 원장 코드를 유지한다."""

    supervisor = _RecordingSupervisor()
    runner = _HeartbeatFailingRunner(
        supervisor=cast(Supervisor, supervisor),
        request_store=cast(Any, object()),
        result_store=cast(Any, object()),
        workflow=object(),
        run_store=cast(Any, object()),
        call_log=cast(Any, object()),
        verify_config_version="verify-config.v1",
        prompt_version="pg.items.v1",
        lease_owner="worker-heartbeat-log",
        lease_heartbeat_seconds=0.001,
        lease_heartbeat_max_seconds=1.0,
    )
    job = _leased_job()

    with caplog.at_level(logging.WARNING, logger="ai.problem_generation.assembly"):
        with pytest.raises(LeaseHeartbeatFailed):
            asyncio.run(runner._run_guarded(job))

    assert supervisor.failed_error_codes == ["problem_worker_internal"]
    assert f"heartbeat 실패로 취소됨 job={job.job_id}" in caplog.text
    assert "로그에 남으면 안 되는 원문" not in caplog.text


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
