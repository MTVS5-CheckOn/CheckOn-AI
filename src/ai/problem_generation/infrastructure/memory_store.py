"""인메모리 저장 어댑터 — 테스트·데모용. 실 PG 어댑터는 A-1 승인 후."""

from __future__ import annotations

import asyncio
from uuid import UUID

from ai.contracts.problem_generation import GeneratedItem, ItemResult
from ai.problem_generation.application.ports import ImmutableStoreConflict
from ai.problem_generation.domain.identity import problem_item_id
from ai.problem_generation.domain.models import CandidateSnapshot, StoredProblemItem


class InMemoryCandidateStore:
    """테스트·M2 수직 슬라이스용 불변 후보 저장소."""

    def __init__(self) -> None:
        self._records: dict[str, CandidateSnapshot] = {}
        self._lock = asyncio.Lock()

    async def put(self, candidate: CandidateSnapshot) -> str:
        candidate_ref = (
            f"item-candidate:{candidate.set_id}:"
            f"{candidate.slot_index}:{candidate.attempt_no}"
        )
        async with self._lock:
            existing = self._records.get(candidate_ref)
            if existing is not None and existing != candidate:
                raise ImmutableStoreConflict(
                    f"후보 불변 스냅숏 충돌: {candidate_ref}"
                )
            self._records[candidate_ref] = candidate
        return candidate_ref

    async def get(self, candidate_ref: str) -> CandidateSnapshot:
        async with self._lock:
            try:
                return self._records[candidate_ref]
            except KeyError as error:
                raise LookupError(f"저장되지 않은 후보 참조: {candidate_ref}") from error

    async def list_all(self) -> tuple[CandidateSnapshot, ...]:
        async with self._lock:
            return tuple(self._records[key] for key in sorted(self._records))


class InMemoryProblemItemStore:
    """테스트·M2 수직 슬라이스용 최종본 저장소."""

    def __init__(self) -> None:
        self._records: dict[tuple[UUID, int], StoredProblemItem] = {}
        self._lock = asyncio.Lock()

    async def save(
        self,
        *,
        set_id: UUID,
        slot_index: int,
        result: ItemResult,
        candidate_ref: str | None,
        item: GeneratedItem | None,
    ) -> StoredProblemItem:
        key = (set_id, slot_index)
        record = StoredProblemItem(
            item_id=problem_item_id(set_id, slot_index),
            set_id=set_id,
            slot_index=slot_index,
            result=result,
            candidate_ref=candidate_ref,
            item=item,
        )
        async with self._lock:
            existing = self._records.get(key)
            if existing is not None and existing != record:
                raise ImmutableStoreConflict(
                    f"문항 최종본 멱등 충돌: set={set_id}, slot={slot_index}"
                )
            self._records[key] = record
        return record

    async def get(self, set_id: UUID, slot_index: int) -> StoredProblemItem:
        async with self._lock:
            try:
                return self._records[(set_id, slot_index)]
            except KeyError as error:
                raise LookupError(
                    f"저장되지 않은 문항: set={set_id}, slot={slot_index}"
                ) from error

    async def current_revision_no(self, set_id: UUID, slot_index: int) -> int:
        """최초 생성 문항의 리비전 번호 0을 반환한다."""

        async with self._lock:
            if (set_id, slot_index) not in self._records:
                raise LookupError(
                    f"저장되지 않은 문항: set={set_id}, slot={slot_index}"
                )
            return 0

    async def list_all(self) -> tuple[StoredProblemItem, ...]:
        async with self._lock:
            return tuple(
                self._records[key]
                for key in sorted(self._records, key=lambda value: (str(value[0]), value[1]))
            )
