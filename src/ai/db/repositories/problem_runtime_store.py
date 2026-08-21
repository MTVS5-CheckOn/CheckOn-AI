"""문제 생성 요청·결과의 PostgreSQL 영속 저장소."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai.contracts.problem_generation import ProblemGenerationOutcome, ProblemRequest
from ai.db.models import ProblemGenerationRequest as RequestRow
from ai.db.models import ProblemGenerationResult as ResultRow

_OUTCOME_ADAPTER: TypeAdapter[ProblemGenerationOutcome] = TypeAdapter(ProblemGenerationOutcome)


class ProblemRuntimeConflict(RuntimeError):
    """같은 참조에 다른 요청 또는 결과가 저장됐다."""


def _request_snapshot(request: ProblemRequest) -> dict[str, Any]:
    return request.model_dump(mode="json")


def _result_snapshot(result: ProblemGenerationOutcome) -> dict[str, Any]:
    return result.model_dump(mode="json")


class PgProblemRequestStore:
    def __init__(self, *, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def put(self, request: ProblemRequest) -> str:
        request_ref = f"problem-request:{request.tenant_id}:{request.request_id}"
        snapshot = _request_snapshot(request)
        async with self._sessionmaker() as session, session.begin():
            inserted = await session.execute(
                pg_insert(RequestRow)
                .values(
                    ref=request_ref,
                    tenant_id=request.tenant_id,
                    snapshot=snapshot,
                )
                .on_conflict_do_nothing(index_elements=["ref"])
                .returning(RequestRow.ref)
            )
            if inserted.scalar_one_or_none() is None:
                stored = await session.scalar(
                    select(RequestRow).where(RequestRow.ref == request_ref)
                )
                if (
                    stored is None
                    or stored.tenant_id != request.tenant_id
                    or stored.snapshot != snapshot
                ):
                    raise ProblemRuntimeConflict(f"문제 생성 요청 멱등 충돌: ref={request_ref}")
        return request_ref

    async def get(self, request_ref: str, *, tenant_id: str) -> ProblemRequest | None:
        async with self._sessionmaker() as session:
            snapshot = await session.scalar(
                select(RequestRow.snapshot).where(
                    RequestRow.ref == request_ref,
                    RequestRow.tenant_id == tenant_id,
                )
            )
        return None if snapshot is None else ProblemRequest.model_validate(snapshot)


class PgProblemResultStore:
    def __init__(self, *, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def put(
        self,
        *,
        tenant_id: str,
        job_id: UUID,
        result: ProblemGenerationOutcome,
    ) -> str:
        result_ref = f"problem-result:{job_id}"
        snapshot = _result_snapshot(result)
        async with self._sessionmaker() as session, session.begin():
            inserted = await session.execute(
                pg_insert(ResultRow)
                .values(
                    ref=result_ref,
                    tenant_id=tenant_id,
                    job_id=job_id,
                    snapshot=snapshot,
                )
                .on_conflict_do_nothing(index_elements=["ref"])
                .returning(ResultRow.ref)
            )
            if inserted.scalar_one_or_none() is None:
                stored = await session.scalar(select(ResultRow).where(ResultRow.ref == result_ref))
                if (
                    stored is None
                    or stored.tenant_id != tenant_id
                    or stored.job_id != job_id
                    or stored.snapshot != snapshot
                ):
                    raise ProblemRuntimeConflict(f"문제 생성 결과 멱등 충돌: ref={result_ref}")
        return result_ref

    async def get(self, result_ref: str, *, tenant_id: str) -> ProblemGenerationOutcome | None:
        async with self._sessionmaker() as session:
            snapshot = await session.scalar(
                select(ResultRow.snapshot).where(
                    ResultRow.ref == result_ref,
                    ResultRow.tenant_id == tenant_id,
                )
            )
        return None if snapshot is None else _OUTCOME_ADAPTER.validate_python(snapshot)


__all__ = [
    "PgProblemRequestStore",
    "PgProblemResultStore",
    "ProblemRuntimeConflict",
]
