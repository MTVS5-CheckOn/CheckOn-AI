"""인메모리 저장 어댑터 — 테스트·데모 전용."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

from ai.contracts.execution import ExecutionContext
from ai.contracts.problem_generation import (
    GeneratedItem,
    ItemResult,
    ItemRevision,
    ProblemRequest,
    ProblemSetResult,
)
from ai.problem_generation.application.ports import (
    ImmutableStoreConflict,
    ProblemItemStore,
    ProblemRevisionSession,
    RevisionConflict,
)
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
        self._revision_nos: dict[tuple[UUID, int], int] = {}
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
            self._revision_nos.setdefault(key, 0)
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
            return self._revision_nos[(set_id, slot_index)]

    async def advance_revision(
        self, set_id: UUID, slot_index: int, *, expected: int, new: int
    ) -> None:
        """리비전 저장소가 같은 잠금 안에서 현재 번호를 전진시킨다."""

        key = (set_id, slot_index)
        async with self._lock:
            current = self._revision_nos.get(key)
            if current is None:
                raise LookupError(
                    f"저장되지 않은 문항: set={set_id}, slot={slot_index}"
                )
            if current != expected or new != expected + 1:
                raise RevisionConflict(
                    reason="stale_base_revision",
                    current_revision_no=current,
                )
            self._revision_nos[key] = new

    async def list_all(self) -> tuple[StoredProblemItem, ...]:
        async with self._lock:
            return tuple(
                self._records[key]
                for key in sorted(self._records, key=lambda value: (str(value[0]), value[1]))
            )


class InMemoryProblemSetStore:
    """memory backend에서 부모 수명주기를 보존하는 결정론 저장소."""

    def __init__(self) -> None:
        self._records: dict[UUID, ProblemSetResult | None] = {}
        self._identities: dict[UUID, tuple[ProblemRequest, ExecutionContext, bool]] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        *,
        set_id: UUID,
        request: ProblemRequest,
        execution_context: ExecutionContext,
        diagnostic_purpose: bool,
    ) -> None:
        identity = (request, execution_context, diagnostic_purpose)
        async with self._lock:
            existing = self._identities.get(set_id)
            if existing is not None and existing != identity:
                raise ImmutableStoreConflict(f"문제 세트 부모 멱등 충돌: {set_id}")
            self._identities[set_id] = identity
            self._records.setdefault(set_id, None)

    async def finalize(self, result: ProblemSetResult) -> None:
        async with self._lock:
            if result.set_id not in self._records:
                raise LookupError(f"생성되지 않은 문제 세트 부모: {result.set_id}")
            existing = self._records[result.set_id]
            if existing is not None and existing != result:
                raise ImmutableStoreConflict(f"문제 세트 결과 멱등 충돌: {result.set_id}")
            self._records[result.set_id] = result


@dataclass(slots=True)
class _MemoryRevisionSession:
    _store: InMemoryProblemRevisionStore
    _key: tuple[UUID, int]
    current_item: GeneratedItem
    current_revision_no: int
    _appended: bool = False

    async def append(self, revision: ItemRevision) -> None:
        if self._appended:
            raise RuntimeError("수정 턴에는 리비전을 한 번만 저장할 수 있다")
        if revision.revision_no != self.current_revision_no + 1:
            raise ValueError("리비전 번호는 현재 번호보다 정확히 1 커야 한다")
        if not isinstance(self._store._items, InMemoryProblemItemStore):
            raise TypeError("인메모리 리비전 저장소에는 인메모리 문항 저장소가 필요하다")
        await self._store._items.advance_revision(
            self._key[0],
            self._key[1],
            expected=self.current_revision_no,
            new=revision.revision_no,
        )
        self._store._revisions.setdefault(self._key, []).append(revision)
        self._appended = True


class InMemoryProblemRevisionStore:
    """문항 단위 non-blocking 예약과 전체 턴 이력을 제공한다."""

    def __init__(self, item_store: ProblemItemStore) -> None:
        self._items = item_store
        self._revisions: dict[tuple[UUID, int], list[ItemRevision]] = {}
        self._locks: dict[tuple[UUID, int], asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    @asynccontextmanager
    async def reserve(
        self,
        *,
        set_id: UUID,
        slot_index: int,
        base_revision_no: int,
    ) -> AsyncIterator[ProblemRevisionSession]:
        key = (set_id, slot_index)
        async with self._locks_guard:
            lock = self._locks.setdefault(key, asyncio.Lock())
            if lock.locked():
                current = len(self._revisions.get(key, ()))
                raise RevisionConflict(
                    reason="revision_in_progress",
                    current_revision_no=current,
                )
            await lock.acquire()
        try:
            stored = await self._items.get(set_id, slot_index)
            if stored.item is None:
                raise LookupError(
                    f"본문이 없는 문항: set={set_id}, slot={slot_index}"
                )
            revisions = self._revisions.get(key, ())
            current_revision_no = await self._items.current_revision_no(
                set_id, slot_index
            )
            if base_revision_no != current_revision_no:
                raise RevisionConflict(
                    reason="stale_base_revision",
                    current_revision_no=current_revision_no,
                )
            current_item = next(
                (
                    revision.result_snapshot
                    for revision in reversed(revisions)
                    if revision.verifications_passed
                    and revision.result_snapshot is not None
                ),
                stored.item,
            )
            yield _MemoryRevisionSession(
                _store=self,
                _key=key,
                current_item=current_item,
                current_revision_no=current_revision_no,
            )
        finally:
            lock.release()

    async def list_revisions(
        self, set_id: UUID, slot_index: int
    ) -> tuple[ItemRevision, ...]:
        await self._items.get(set_id, slot_index)
        return tuple(self._revisions.get((set_id, slot_index), ()))
