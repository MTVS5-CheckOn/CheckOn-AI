"""저장 포트 — 인프라가 구현한다. 애플리케이션이 소유하는 인터페이스."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Protocol
from uuid import UUID

from ai.contracts.execution import ExecutionContext
from ai.contracts.problem_generation import (
    GeneratedItem,
    ItemResult,
    ItemRevision,
    ProblemRequest,
    ProblemSetResult,
)
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


class RevisionConflict(RuntimeError):
    """문항 리비전의 낙관적 잠금 또는 진행 중 예약 충돌."""

    def __init__(self, *, reason: str, current_revision_no: int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.current_revision_no = current_revision_no


class ProblemRevisionSession(Protocol):
    """문항 하나를 독점한 수정 턴의 저장 세션."""

    @property
    def current_item(self) -> GeneratedItem:
        """마지막으로 전체 검증을 통과한 현재 문항."""
        ...

    @property
    def current_revision_no(self) -> int:
        """예약 시점의 낙관적 잠금 번호."""
        ...

    async def append(self, revision: ItemRevision) -> None:
        """턴 결과를 한 번 저장하고 현재 리비전 번호를 올린다."""
        ...


class ProblemRevisionStore(Protocol):
    """수정 턴을 문항 단위로 직렬화하는 리비전 저장 경계."""

    def reserve(
        self,
        *,
        set_id: UUID,
        slot_index: int,
        base_revision_no: int,
    ) -> AbstractAsyncContextManager[ProblemRevisionSession]:
        """진행 중이면 즉시 충돌시키고, 성공하면 append까지 독점한다."""
        ...

    async def list_revisions(
        self, set_id: UUID, slot_index: int
    ) -> tuple[ItemRevision, ...]:
        """턴 번호 순으로 리비전 이력을 읽는다."""
        ...


class CandidateStore(Protocol):
    """게이트 통과 후보를 재개 경로에서도 읽는 교체 가능한 저장 경계."""

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

    async def current_revision_no(self, set_id: UUID, slot_index: int) -> int:
        """슬롯의 현재 낙관적 잠금 번호를 읽는다."""
        ...


class ProblemSetStore(Protocol):
    """AI_RUN과 슬롯 사이의 문제 세트 부모 저장 경계."""

    async def create(
        self,
        *,
        set_id: UUID,
        request: ProblemRequest,
        execution_context: ExecutionContext,
        diagnostic_purpose: bool,
    ) -> None:
        """문항 저장 전에 생성 중인 부모 세트를 멱등 보장한다."""
        ...

    async def finalize(self, result: ProblemSetResult) -> None:
        """종료 결과의 상태·요약·중단 사유를 부모 세트에 투영한다."""
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
