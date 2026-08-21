"""문제 생성 중간 후보의 PostgreSQL 불변 저장소."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai.agents.supervisor import system_utc_now
from ai.db.models import ItemCandidate
from ai.problem_generation.application.ports import ImmutableStoreConflict
from ai.problem_generation.domain.models import CandidateSnapshot

_SCHEME = "item-candidate"


def _candidate_ref(candidate: CandidateSnapshot) -> str:
    return f"{_SCHEME}:{candidate.set_id}:{candidate.slot_index}:{candidate.attempt_no}"


def _parse_ref(candidate_ref: str) -> tuple[UUID, int, int]:
    parts = candidate_ref.split(":")
    if len(parts) != 4 or parts[0] != _SCHEME:
        raise ValueError(f"잘못된 문제 후보 참조: {candidate_ref}")
    try:
        set_id = UUID(parts[1])
        slot_index = int(parts[2])
        attempt_no = int(parts[3])
    except (ValueError, TypeError) as exc:
        raise ValueError(f"잘못된 문제 후보 참조: {candidate_ref}") from exc
    if slot_index < 0 or attempt_no not in {1, 2, 3}:
        raise ValueError(f"잘못된 문제 후보 참조: {candidate_ref}")
    return set_id, slot_index, attempt_no


class PgCandidateStore:
    """테넌트 스코프 `ITEM_CANDIDATE` 저장소."""

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        tenant_id: str,
        now: Callable[[], datetime] = system_utc_now,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._tenant_id = tenant_id
        self._now = now

    async def put(self, candidate: CandidateSnapshot) -> str:
        snapshot = candidate.model_dump(mode="json")
        candidate_id = uuid5(
            candidate.set_id,
            f"item-candidate:{candidate.slot_index}:{candidate.attempt_no}",
        )
        values = {
            "id": candidate_id,
            "set_id": candidate.set_id,
            "tenant_id": self._tenant_id,
            "slot_index": candidate.slot_index,
            "attempt_no": candidate.attempt_no,
            "snapshot": snapshot,
            "gate_summary": candidate.solve_result.model_dump(mode="json"),
            "difficulty_est": candidate.difficulty_est,
            "created_at": self._now(),
        }
        async with self._sessionmaker() as session, session.begin():
            inserted = await session.execute(
                pg_insert(ItemCandidate)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=["tenant_id", "set_id", "slot_index", "attempt_no"]
                )
                .returning(ItemCandidate.id)
            )
            if inserted.scalar_one_or_none() is None:
                stored = await session.scalar(
                    select(ItemCandidate).where(
                        ItemCandidate.tenant_id == self._tenant_id,
                        ItemCandidate.set_id == candidate.set_id,
                        ItemCandidate.slot_index == candidate.slot_index,
                        ItemCandidate.attempt_no == candidate.attempt_no,
                    )
                )
                if stored is None or stored.snapshot != snapshot:
                    raise ImmutableStoreConflict(
                        "문제 후보 멱등 충돌: "
                        f"set={candidate.set_id}, slot={candidate.slot_index}, "
                        f"attempt={candidate.attempt_no}"
                    )
        return _candidate_ref(candidate)

    async def get(self, candidate_ref: str) -> CandidateSnapshot:
        set_id, slot_index, attempt_no = _parse_ref(candidate_ref)
        async with self._sessionmaker() as session:
            snapshot = await session.scalar(
                select(ItemCandidate.snapshot).where(
                    ItemCandidate.tenant_id == self._tenant_id,
                    ItemCandidate.set_id == set_id,
                    ItemCandidate.slot_index == slot_index,
                    ItemCandidate.attempt_no == attempt_no,
                )
            )
        if snapshot is None:
            raise LookupError(f"저장되지 않은 문제 후보 참조: {candidate_ref}")
        return CandidateSnapshot.model_validate(snapshot)


__all__ = ["PgCandidateStore"]
