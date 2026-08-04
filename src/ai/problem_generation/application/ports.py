"""저장 포트 — 인프라가 구현한다. 애플리케이션이 소유하는 인터페이스."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from ai.contracts.problem_generation import GeneratedItem, ItemResult
from ai.problem_generation.domain.models import CandidateSnapshot, StoredProblemItem


class ImmutableStoreConflict(RuntimeError):
    """같은 멱등 키에 서로 다른 불변 스냅숏을 쓰려 함."""


class CandidateStore(Protocol):
    """ITEM_CANDIDATE 승인 전 사용하는 교체 가능한 저장 경계."""

    async def put(self, candidate: CandidateSnapshot) -> str:
        """후보를 멱등·불변 저장하고 참조를 반환한다."""
        ...

    async def get(self, candidate_ref: str) -> CandidateSnapshot:
        """참조로 저장 후보를 읽는다."""
        ...


class ProblemItemStore(Protocol):
    """최종 문제 문항의 교체 가능한 저장 경계."""

    async def save(
        self,
        *,
        set_id: UUID,
        slot_index: int,
        result: ItemResult,
        candidate_ref: str | None,
        item: GeneratedItem | None,
    ) -> StoredProblemItem:
        """슬롯 최종본을 멱등 저장한다."""
        ...

    async def get(self, set_id: UUID, slot_index: int) -> StoredProblemItem:
        """세트와 슬롯으로 최종본을 읽는다."""
        ...
