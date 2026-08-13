"""문항 리비전 저장의 낙관적 잠금·진행 중 충돌 계약."""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from ai.contracts.gates import BlockedReason
from ai.contracts.problem_generation import (
    Answer,
    Choice,
    EvidenceAnchor,
    EvidenceKind,
    GeneratedItem,
    ItemResult,
    ItemRevision,
    ProblemItemStatus,
    RevisionKind,
)
from ai.contracts.taxonomy import AreaTag, ItemFormat, TypeTag
from ai.problem_generation.application.ports import RevisionConflict
from ai.problem_generation.domain.identity import problem_item_id
from ai.problem_generation.infrastructure.memory_store import (
    InMemoryProblemItemStore,
    InMemoryProblemRevisionStore,
)

_SET_ID = UUID("11111111-1111-4111-8111-111111111111")


def _item(stem: str) -> GeneratedItem:
    return GeneratedItem(
        area_tag=AreaTag.LANGUAGE,
        type_tag=TypeTag.INFER,
        item_format=ItemFormat.MCQ,
        skill_node_id="language.grammar.phonological_change",
        stem=stem,
        choices=tuple(
            Choice(
                no=no,
                text=f"선지 {no}",
                why_wrong=None if no == 1 else "승인 근거와 다르다.",
            )
            for no in range(1, 6)
        ),
        answer=Answer(correct_no=1),
        rationale="승인된 규정에 따르면 1번이 옳다.",
        evidence=(
            EvidenceAnchor(
                kind=EvidenceKind.GRAMMAR_RULE,
                ref="kornorms:표준발음법:1",
            ),
        ),
    )


async def _stores() -> tuple[InMemoryProblemItemStore, InMemoryProblemRevisionStore]:
    items = InMemoryProblemItemStore()
    await items.save(
        set_id=_SET_ID,
        slot_index=0,
        result=ItemResult(
            item_id=problem_item_id(_SET_ID, 0),
            status=ProblemItemStatus.VERIFIED,
            attempt_no=1,
        ),
        candidate_ref="item-candidate:test",
        item=_item("최초 문항"),
    )
    return items, InMemoryProblemRevisionStore(items)


def _revision(
    number: int,
    *,
    item: GeneratedItem | None,
    blocked_reason: BlockedReason | None = None,
) -> ItemRevision:
    return ItemRevision(
        revision_no=number,
        revision_kind=RevisionKind.AI_REFINE,
        instruction="발문을 명확하게 바꿔 주세요.",
        result_snapshot=item,
        verifications_passed=item is not None,
        blocked_reason=blocked_reason,
    )


def test_blocked_turn_increments_revision_but_keeps_last_verified_item() -> None:
    async def scenario() -> None:
        _items, revisions = await _stores()
        revised = _item("검증을 통과한 수정 문항")
        async with revisions.reserve(
            set_id=_SET_ID, slot_index=0, base_revision_no=0
        ) as session:
            assert session.current_item.stem == "최초 문항"
            await session.append(_revision(1, item=revised))
        async with revisions.reserve(
            set_id=_SET_ID, slot_index=0, base_revision_no=1
        ) as session:
            assert session.current_item == revised
            await session.append(
                _revision(
                    2,
                    item=None,
                    blocked_reason=BlockedReason.ANSWER_INTEGRITY,
                )
            )
        async with revisions.reserve(
            set_id=_SET_ID, slot_index=0, base_revision_no=2
        ) as session:
            assert session.current_revision_no == 2
            assert session.current_item == revised
        assert [row.revision_no for row in await revisions.list_revisions(_SET_ID, 0)] == [
            1,
            2,
        ]

    asyncio.run(scenario())


def test_stale_base_is_rejected_before_session_body_runs() -> None:
    async def scenario() -> None:
        _items, revisions = await _stores()
        async with revisions.reserve(
            set_id=_SET_ID, slot_index=0, base_revision_no=0
        ) as session:
            await session.append(_revision(1, item=_item("수정 문항")))
        with pytest.raises(RevisionConflict) as caught:
            async with revisions.reserve(
                set_id=_SET_ID, slot_index=0, base_revision_no=0
            ):
                raise AssertionError("stale 요청 본문은 실행되면 안 된다")
        assert caught.value.reason == "stale_base_revision"
        assert caught.value.current_revision_no == 1

    asyncio.run(scenario())


def test_concurrent_new_key_is_rejected_as_revision_in_progress() -> None:
    async def scenario() -> None:
        _items, revisions = await _stores()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def first_turn() -> None:
            async with revisions.reserve(
                set_id=_SET_ID, slot_index=0, base_revision_no=0
            ):
                entered.set()
                await release.wait()

        first = asyncio.create_task(first_turn())
        await entered.wait()
        try:
            with pytest.raises(RevisionConflict) as caught:
                async with revisions.reserve(
                    set_id=_SET_ID, slot_index=0, base_revision_no=0
                ):
                    raise AssertionError("동시 요청 본문은 실행되면 안 된다")
            assert caught.value.reason == "revision_in_progress"
            assert caught.value.current_revision_no == 0
        finally:
            release.set()
            await first

    asyncio.run(scenario())
