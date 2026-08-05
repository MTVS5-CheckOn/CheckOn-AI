"""저장 포트 — 인프라가 구현한다. 애플리케이션이 소유하는 인터페이스."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from ai.contracts.problem_generation import GeneratedItem, ItemResult
from ai.problem_generation.domain.lexicon import LexiconEntry
from ai.problem_generation.domain.models import CandidateSnapshot, StoredProblemItem


class ImmutableStoreConflict(RuntimeError):
    """같은 멱등 키에 서로 다른 불변 스냅숏을 쓰려 함."""


class LexiconUnavailable(RuntimeError):
    """어휘 사전을 조회할 수 없음 — 대조 불가.

    "없는 표제어"와 구분해야 한다. 조회가 실패했는데 빈 결과로 취급하면
    R-1이 대조 없이 통과한다(불변식 2). 호출부는 이 예외를
    `verification_unavailable`로 올리고 문항을 발행하지 않는다.
    """


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


class LexiconLookup(Protocol):
    """어휘 사전 조회 경계 — R-1 어휘 대조가 쓴다.

    사전이 바뀌어도(표준국어대사전 → 우리말샘) 규칙 코드는 그대로여야 하므로
    포트로 끊는다.
    """

    async def lookup(self, word: str) -> tuple[LexiconEntry, ...]:
        """표제어를 정확히 일치로 조회한다.

        사전에 없는 말이면 **빈 튜플**을 반환한다 — 오류가 아니다. R-1이
        "표제어 없음"을 판정하는 정상 경로다. 조회 자체가 실패하면
        `LexiconUnavailable`을 올린다.
        """
        ...
