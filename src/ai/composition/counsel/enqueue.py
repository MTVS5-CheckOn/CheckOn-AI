"""counsel_pack WorkerJob enqueue — 컨텍스트 묶음 저장 후 잡을 큐에 넣는다.

`probe/enqueue.py`와 같은 골격이다. `payload_ref`는 불투명 URI(`context://…`),
`payload_hash`는 묶음 내용의 sha256 — **본문을 복제하지 않고 참조·해시만** 경계를 넘는다(§5).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import datetime
from uuid import UUID, uuid4

from ai.agents.supervisor import Supervisor, system_utc_now
from ai.composition.counsel.stores import ContextBundleRecord, ContextStore
from ai.contracts.agents import (
    OperationKind,
    PriorityClass,
    WorkerJob,
    WorkerKind,
    default_priority_for_operation,
)
from ai.contracts.composition import DraftContext


def content_hash(contexts: Mapping[str, DraftContext]) -> str:
    """컨텍스트 묶음의 결정론 해시 — state의 `context_hash`와 대조된다(불변식 ④)."""
    canonical = json.dumps(
        {ref: ctx.model_dump(mode="json") for ref, ctx in sorted(contexts.items())},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CounselPackEnqueuer:
    """컨텍스트 묶음 저장 → WorkerJob enqueue. 실행은 러너가 별도로 lease한다."""

    def __init__(
        self,
        *,
        supervisor: Supervisor,
        context_store: ContextStore,
        new_id: Callable[[], UUID] = uuid4,
        now: Callable[[], datetime] = system_utc_now,
    ) -> None:
        self._sv = supervisor
        self._contexts = context_store
        self._new_id = new_id
        self._now = now

    async def enqueue(
        self,
        *,
        tenant_id: str,
        class_ref: str,
        contexts: Mapping[str, DraftContext],
    ) -> WorkerJob:
        digest = content_hash(contexts)
        context_ref = await self._contexts.put(
            ContextBundleRecord(
                id=self._new_id(),
                tenant_id=tenant_id,
                class_ref=class_ref,
                contexts=dict(contexts),
                content_hash=digest,
                created_at=self._now(),
            )
        )
        operation = OperationKind.COUNSEL_PACK_GENERATE
        job = WorkerJob(
            job_id=self._new_id(),
            execution_id=self._new_id(),
            tenant_id=tenant_id,
            worker_kind=WorkerKind.COUNSEL_PACK,
            operation=operation,
            payload_ref=context_ref,
            payload_hash=digest,
            priority_class=default_priority_for_operation(operation),
            queued_at=self._now(),
        )
        return await self._sv.enqueue(job)


def counsel_pack_priority() -> PriorityClass:
    """counsel_pack.generate 기본 우선순위 — 라우팅표 정합 확인용."""
    return default_priority_for_operation(OperationKind.COUNSEL_PACK_GENERATE)


__all__ = ["CounselPackEnqueuer", "content_hash", "counsel_pack_priority"]
