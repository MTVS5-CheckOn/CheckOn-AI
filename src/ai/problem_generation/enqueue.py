"""문제 세트 생성 요청 저장 후 슈퍼바이저 잡 enqueue."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

from ai.agents.supervisor import Supervisor, system_utc_now
from ai.contracts.agents import (
    OperationKind,
    PriorityClass,
    WorkerJob,
    WorkerKind,
    default_priority_for_operation,
)
from ai.contracts.problem_generation import ProblemRequest
from ai.problem_generation.domain.identity import request_hash


class ProblemRequestStore(Protocol):
    """잡 payload_ref로 문제 생성 요청을 역참조하는 저장 경계."""

    async def put(self, request: ProblemRequest) -> str: ...

    async def get(
        self, request_ref: str, *, tenant_id: str
    ) -> ProblemRequest | None: ...


class ProblemGenerationEnqueuer:
    """문제 생성 요청을 저장하고 ``problem_set.generate`` 잡을 넣는다."""

    def __init__(
        self,
        *,
        supervisor: Supervisor,
        request_store: ProblemRequestStore,
        new_id: Callable[[], UUID] = uuid4,
        now: Callable[[], datetime] = system_utc_now,
    ) -> None:
        self._supervisor = supervisor
        self._requests = request_store
        self._new_id = new_id
        self._now = now

    async def enqueue(self, request: ProblemRequest) -> WorkerJob:
        payload_hash = request_hash(request)
        request_ref = await self._requests.put(request)
        operation = OperationKind.PROBLEM_SET_GENERATE
        job = WorkerJob(
            job_id=self._new_id(),
            execution_id=self._new_id(),
            tenant_id=request.tenant_id,
            worker_kind=WorkerKind.PROBLEM_GENERATION,
            operation=operation,
            payload_ref=request_ref,
            payload_hash=payload_hash,
            priority_class=default_priority_for_operation(operation),
            queued_at=self._now(),
        )
        return await self._supervisor.enqueue(job)


def problem_generation_priority() -> PriorityClass:
    """문제 세트 생성의 공용 계약 기본 우선순위."""

    return default_priority_for_operation(OperationKind.PROBLEM_SET_GENERATE)


__all__ = [
    "ProblemGenerationEnqueuer",
    "ProblemRequestStore",
    "problem_generation_priority",
]
