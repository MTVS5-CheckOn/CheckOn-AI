"""문제 세트 부모 영속과 재시작 조회 재조립."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import ColumnElement, select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai.contracts.execution import ExecutionContext, VersionSet
from ai.contracts.problem_generation import (
    ProblemItemStatus,
    ProblemRequest,
    ProblemSetResult,
    ProblemSetStatus,
    SetStopReason,
    TargetSource,
)
from ai.db.models import AiRun
from ai.db.models import ProblemItem as ProblemItemRow
from ai.db.models import ProblemSet as ProblemSetRow
from ai.problem_generation.application.ports import ImmutableStoreConflict
from ai.problem_generation.domain.models import StoredProblemItem


@dataclass(frozen=True, slots=True)
class RestoredProblemSet:
    """PG 스냅숏에서 무손실로 복구한 API 조회 입력."""

    result: ProblemSetResult
    request: ProblemRequest
    execution_id: UUID
    versions: VersionSet


def _versions(row: AiRun) -> VersionSet:
    return VersionSet(
        pipeline_version=row.pipeline_version,
        engine_version=row.engine_version,
        schema_version=row.schema_version,
        contract_version=row.contract_version,
        threshold_version=row.threshold_version,
        prompt_version=row.prompt_version,
        graph_version=row.graph_version,
        taxonomy_version=row.taxonomy_version,
        verify_config_version=row.verify_config_version,
        difficulty_calib_version=row.difficulty_calib_version,
    )


class PgProblemSetStore:
    """테넌트 범위 problem_set 부모와 슬롯 스냅숏을 함께 읽고 쓴다."""

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        tenant_id: str,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._tenant_id = tenant_id

    async def create(
        self,
        *,
        set_id: UUID,
        request: ProblemRequest,
        execution_context: ExecutionContext,
        diagnostic_purpose: bool,
    ) -> None:
        request_snapshot = request.model_dump(mode="json")
        values = {
            "id": set_id,
            "run_id": execution_context.execution_id,
            "tenant_id": self._tenant_id,
            "target_kind": request.target_kind.value,
            "target_ref": request.target_ref,
            "target_source": request.target_source.value,
            "weakness_map_id": request.weakness_map_id,
            "request": request_snapshot,
            "status": ProblemSetStatus.GENERATING.value,
            "summary": None,
            "stop_reason": None,
            "diagnostic_purpose": diagnostic_purpose,
            "created_at": datetime.now(UTC),
        }
        async with self._sessionmaker() as session, session.begin():
            await session.execute(
                pg_insert(ProblemSetRow)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[ProblemSetRow.id])
            )
            stored = await session.scalar(
                select(ProblemSetRow).where(ProblemSetRow.id == set_id)
            )
            if stored is None:
                raise LookupError(f"문제 세트 부모를 만들지 못했다: {set_id}")
            if (
                stored.tenant_id != self._tenant_id
                or stored.run_id != execution_context.execution_id
                or stored.request != request_snapshot
            ):
                raise ImmutableStoreConflict(f"문제 세트 부모 멱등 충돌: {set_id}")

    async def finalize(self, result: ProblemSetResult) -> None:
        async with self._sessionmaker() as session, session.begin():
            updated = await session.execute(
                sa_update(ProblemSetRow)
                .where(
                    ProblemSetRow.id == result.set_id,
                    ProblemSetRow.tenant_id == self._tenant_id,
                )
                .values(
                    status=result.status.value,
                    summary=result.summary,
                    stop_reason=(
                        result.stop_reason.value if result.stop_reason is not None else None
                    ),
                )
                .returning(ProblemSetRow.id)
            )
            if updated.scalar_one_or_none() is None:
                raise LookupError(f"종료할 문제 세트 부모가 없다: {result.set_id}")

    async def get_by_set_id(self, set_id: UUID) -> RestoredProblemSet | None:
        return await self._restore(ProblemSetRow.id == set_id)

    async def get_by_execution_id(
        self, execution_id: UUID
    ) -> RestoredProblemSet | None:
        return await self._restore(ProblemSetRow.run_id == execution_id)

    async def _restore(
        self, condition: ColumnElement[bool]
    ) -> RestoredProblemSet | None:
        async with self._sessionmaker() as session:
            joined = (
                await session.execute(
                    select(ProblemSetRow, AiRun)
                    .join(AiRun, ProblemSetRow.run_id == AiRun.execution_id)
                    .where(ProblemSetRow.tenant_id == self._tenant_id, condition)
                )
            ).one_or_none()
            if joined is None:
                return None
            set_row, run_row = joined
            item_rows = (
                (
                    await session.execute(
                        select(ProblemItemRow)
                        .where(ProblemItemRow.set_id == set_row.id)
                        .order_by(ProblemItemRow.slot_index)
                    )
                )
                .scalars()
                .all()
            )

        stored_items = tuple(
            StoredProblemItem.model_validate(row.snapshot) for row in item_rows
        )
        slot_indexes = tuple(item.slot_index for item in stored_items)
        if slot_indexes != tuple(range(len(stored_items))):
            raise ValueError(f"문제 세트 슬롯이 연속적이지 않다: {set_row.id}")
        request = ProblemRequest.model_validate(set_row.request)
        items = tuple(item.result for item in stored_items)
        result = ProblemSetResult(
            set_id=set_row.id,
            status=ProblemSetStatus(set_row.status),
            stop_reason=(
                SetStopReason(set_row.stop_reason)
                if set_row.stop_reason is not None
                else None
            ),
            target_source=request.target_source,
            personalized=request.target_source is TargetSource.WEAKNESS_AUTO,
            requested_count=request.count,
            processed_count=len(items),
            unstarted_count=request.count - len(items),
            items=items,
            summary=set_row.summary,
            dropped_reasons=tuple(
                item.failure_reason
                for item in items
                if item.status is ProblemItemStatus.DROPPED
                and item.failure_reason is not None
            ),
        )
        return RestoredProblemSet(
            result=result,
            request=request,
            execution_id=run_row.execution_id,
            versions=_versions(run_row),
        )


__all__ = ["PgProblemSetStore", "RestoredProblemSet"]
