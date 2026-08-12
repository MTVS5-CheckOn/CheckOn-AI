"""문항 리비전 PG 저장소 — 테넌트 격리·낙관적 잠금·진행 중 예약."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID, uuid5

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai.contracts.problem_generation import GeneratedItem, ItemRevision
from ai.db.models import ItemRevision as ItemRevisionRow
from ai.db.models import ProblemItem as ProblemItemRow
from ai.db.models import ProblemSet as ProblemSetRow
from ai.problem_generation.application.ports import (
    ProblemRevisionSession,
    RevisionConflict,
)
from ai.problem_generation.domain.models import StoredProblemItem


def _revision_from_row(row: ItemRevisionRow) -> ItemRevision:
    return ItemRevision(
        revision_no=row.turn_no,
        revision_kind=row.revision_kind,
        instruction=row.instruction,
        result_snapshot=row.result_snapshot,
        diff=tuple(row.diff.get("changes", ())),
        verifications_passed=row.verifications_passed,
        blocked_reason=row.blocked_reason,
        llm_call_id=row.llm_call_id,
    )


@dataclass(slots=True)
class _PgRevisionSession:
    _session: AsyncSession
    _item_row: ProblemItemRow
    current_item: GeneratedItem
    current_revision_no: int
    _appended: bool = False

    async def append(self, revision: ItemRevision) -> None:
        if self._appended:
            raise RuntimeError("수정 턴에는 리비전을 한 번만 저장할 수 있다")
        if revision.revision_no != self.current_revision_no + 1:
            raise ValueError("리비전 번호는 현재 번호보다 정확히 1 커야 한다")
        self._session.add(
            ItemRevisionRow(
                id=uuid5(self._item_row.id, f"item-revision:{revision.revision_no}"),
                item_id=self._item_row.id,
                turn_no=revision.revision_no,
                revision_kind=revision.revision_kind.value,
                instruction=revision.instruction,
                result_snapshot=(
                    revision.result_snapshot.model_dump(mode="json")
                    if revision.result_snapshot is not None
                    else None
                ),
                diff={
                    "changes": [change.model_dump(mode="json") for change in revision.diff]
                },
                verifications_passed=revision.verifications_passed,
                blocked_reason=(
                    revision.blocked_reason.value
                    if revision.blocked_reason is not None
                    else None
                ),
                llm_call_id=revision.llm_call_id,
            )
        )
        self._item_row.current_revision_no = revision.revision_no
        self._appended = True


class PgProblemRevisionStore:
    """세션 advisory lock을 LLM 턴 전체에 유지하는 테넌트 스코프 저장소."""

    _TRY_LOCK = text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))")
    _UNLOCK = text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))")

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        tenant_id: str,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._tenant_id = tenant_id

    @asynccontextmanager
    async def reserve(
        self,
        *,
        set_id: UUID,
        slot_index: int,
        base_revision_no: int,
    ) -> AsyncIterator[ProblemRevisionSession]:
        lock_key = f"item_revision\x1f{self._tenant_id}\x1f{set_id}\x1f{slot_index}"
        async with self._sessionmaker() as session:
            acquired = bool(
                (await session.execute(self._TRY_LOCK, {"key": lock_key})).scalar_one()
            )
            if not acquired:
                current = await self._current_revision_no(session, set_id, slot_index)
                raise RevisionConflict(
                    reason="revision_in_progress", current_revision_no=current
                )
            try:
                row = await self._item_row(session, set_id, slot_index)
                if row is None:
                    raise LookupError(
                        f"다른 테넌트의 문항이거나 없음: set={set_id}, slot={slot_index}"
                    )
                if row.current_revision_no != base_revision_no:
                    raise RevisionConflict(
                        reason="stale_base_revision",
                        current_revision_no=row.current_revision_no,
                    )
                current_item = await self._current_item(session, row)
                revision_session = _PgRevisionSession(
                    _session=session,
                    _item_row=row,
                    current_item=current_item,
                    current_revision_no=row.current_revision_no,
                )
                yield revision_session
                if revision_session._appended:
                    await session.commit()
                else:
                    await session.rollback()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.execute(self._UNLOCK, {"key": lock_key})
                await session.rollback()

    async def list_revisions(
        self, set_id: UUID, slot_index: int
    ) -> tuple[ItemRevision, ...]:
        async with self._sessionmaker() as session:
            row = await self._item_row(session, set_id, slot_index)
            if row is None:
                raise LookupError(
                    f"다른 테넌트의 문항이거나 없음: set={set_id}, slot={slot_index}"
                )
            statement = (
                select(ItemRevisionRow)
                .where(ItemRevisionRow.item_id == row.id)
                .order_by(ItemRevisionRow.turn_no)
            )
            rows = (await session.execute(statement)).scalars().all()
            return tuple(_revision_from_row(revision) for revision in rows)

    async def _current_revision_no(
        self, session: AsyncSession, set_id: UUID, slot_index: int
    ) -> int:
        row = await self._item_row(session, set_id, slot_index)
        if row is None:
            raise LookupError(
                f"다른 테넌트의 문항이거나 없음: set={set_id}, slot={slot_index}"
            )
        return row.current_revision_no

    async def _item_row(
        self, session: AsyncSession, set_id: UUID, slot_index: int
    ) -> ProblemItemRow | None:
        statement = (
            select(ProblemItemRow)
            .join(ProblemSetRow, ProblemItemRow.set_id == ProblemSetRow.id)
            .where(
                ProblemSetRow.tenant_id == self._tenant_id,
                ProblemItemRow.set_id == set_id,
                ProblemItemRow.slot_index == slot_index,
            )
        )
        return (await session.execute(statement)).scalar_one_or_none()

    async def _current_item(
        self, session: AsyncSession, row: ProblemItemRow
    ) -> GeneratedItem:
        statement = (
            select(ItemRevisionRow)
            .where(
                ItemRevisionRow.item_id == row.id,
                ItemRevisionRow.verifications_passed.is_(True),
                ItemRevisionRow.result_snapshot.is_not(None),
            )
            .order_by(ItemRevisionRow.turn_no.desc())
            .limit(1)
        )
        revision = (await session.execute(statement)).scalar_one_or_none()
        if revision is not None:
            return GeneratedItem.model_validate(revision.result_snapshot)
        stored = StoredProblemItem.model_validate(row.snapshot)
        if stored.item is None:
            raise LookupError(f"본문이 없는 문항: item_id={row.id}")
        return stored.item


__all__ = ["PgProblemRevisionStore"]
