"""파생 투영이 스냅숏과 갈리지 않는다 — A 판정 승인 조건 ⓑ (09 §2-20 · 99 #04).

🔴 **이 파일이 승인 조건이다.** 조건 ⓐ(한 함수가 유도)만으로는 다음 사람이 그 함수를
우회해 컬럼을 직접 채울 수 있다. 경계를 말했으면 그 자리에 red를 남긴다.

검사는 셋이다:

1. **유도값 == 컬럼** — `problem_item_projection()`이 낸 값이 실제 행에 그대로 앉는다.
2. **덮이지 않은 컬럼이 없다** — ORM 컬럼 전수에서 「파생이 아니라고 선언한 것」을 빼면
   투영 키 집합과 정확히 같다. 🔴 **새 컬럼을 추가하고 유도를 안 붙이면 여기서 red다** —
   이게 없으면 컬럼이 조용히 늘어나고 그게 §2-20을 낳은 형태다.
3. **본문 없는 슬롯** — `item=None`이면 본문 투영 여덟이 전부 `None`이다(결정 ③ ⓒ).

⚠ 실 PG 왕복은 `tests/ai/integration/test_problem_store_pg_roundtrip.py`가 본다. 여기는
DB 없이 도는 순수 함수 검사다.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from ai.contracts.problem_generation import (
    Answer,
    Choice,
    DifficultyBand,
    EvidenceAnchor,
    EvidenceKind,
    GeneratedItem,
    ItemResult,
    ProblemItemStatus,
    ReviewReason,
)
from ai.contracts.taxonomy import AreaTag, ItemFormat, TypeTag
from ai.db.models import ProblemItem as ProblemItemRow
from ai.db.repositories.problem_store import (
    NON_PROJECTED_COLUMNS,
    problem_item_projection,
)
from ai.problem_generation.domain.identity import problem_item_id
from ai.problem_generation.domain.models import StoredProblemItem

_SET_ID = UUID("11111111-1111-5111-8111-111111111111")

#: 본문(`GeneratedItem`)에서만 나오는 투영 — 슬롯에 본문이 없으면 전부 None이어야 한다.
_BODY_PROJECTIONS = (
    "area_tag",
    "type_tag",
    "item_format",
    "skill_node_id",
    "stem",
    "choices",
    "answer",
    "rationale",
)


def _item() -> GeneratedItem:
    return GeneratedItem(
        area_tag=AreaTag.LANGUAGE,
        type_tag=TypeTag.CONCEPT,
        item_format=ItemFormat.MCQ,
        skill_node_id="grammar.node-1",
        stem="다음 중 옳은 것을 고르시오.",
        choices=tuple(
            Choice(
                no=no,
                text=f"선지 {no}",
                why_wrong=None if no == 1 else f"오답 근거 {no}",
                misconception_tag=None if no == 1 else "application_target_substitution",
            )
            for no in range(1, 6)
        ),
        answer=Answer(correct_no=1),
        rationale="비공개 해설 원문",
        evidence=(
            EvidenceAnchor(kind=EvidenceKind.GRAMMAR_RULE, ref="grammar:rule-1"),
        ),
    )


def _record(
    *,
    slot_index: int = 0,
    with_body: bool = True,
    review_reason: ReviewReason | None = None,
) -> StoredProblemItem:
    item_id = problem_item_id(_SET_ID, slot_index)
    status = (
        ProblemItemStatus.NEEDS_REVIEW
        if review_reason is not None
        else ProblemItemStatus.VERIFIED
    )
    if not with_body:
        status = ProblemItemStatus.VERIFICATION_UNAVAILABLE
    return StoredProblemItem(
        item_id=item_id,
        set_id=_SET_ID,
        slot_index=slot_index,
        result=ItemResult(
            item_id=item_id,
            status=status,
            attempt_no=1,
            difficulty_est=0.62 if with_body else None,
            difficulty_band=DifficultyBand.MEDIUM if with_body else None,
            review_reason=review_reason,
        ),
        candidate_ref=f"item-candidate:{_SET_ID}:{slot_index}:1" if with_body else None,
        item=_item() if with_body else None,
    )


def test_projection_covers_every_derived_column() -> None:
    """🔴 ORM 컬럼 전수 − 비파생 선언 == 투영 키. 새 컬럼에 유도를 안 붙이면 red다."""
    orm_columns = {column.name for column in ProblemItemRow.__table__.columns}
    derived = orm_columns - NON_PROJECTED_COLUMNS
    assert derived == set(problem_item_projection(_record())), (
        "파생 컬럼과 투영 키가 다르다 — 컬럼을 늘렸으면 "
        "`problem_item_projection()`에 유도를 붙이거나 `NON_PROJECTED_COLUMNS`에 "
        "**이유와 함께** 등재해야 한다(A 판정 조건 ⓐ)"
    )


def test_non_projected_columns_are_real_columns() -> None:
    """비파생 선언이 낡으면 위 검사가 조용히 헐거워진다 — 실재 컬럼만 허용."""
    orm_columns = {column.name for column in ProblemItemRow.__table__.columns}
    assert NON_PROJECTED_COLUMNS <= orm_columns, (
        f"없는 컬럼이 비파생으로 선언돼 있다: {sorted(NON_PROJECTED_COLUMNS - orm_columns)}"
    )


def test_projection_derives_body_and_result_axes() -> None:
    """유도값이 스냅숏의 값과 같다 — 손으로 옮긴 값이 아니다."""
    record = _record(review_reason=ReviewReason.LOW_CONFIDENCE)
    projection = problem_item_projection(record)
    body = record.item
    assert body is not None

    assert projection["slot_index"] == record.slot_index
    assert projection["area_tag"] == body.area_tag.value
    assert projection["type_tag"] == body.type_tag.value
    assert projection["stem"] == body.stem
    assert projection["rationale"] == body.rationale
    assert projection["answer"] == {"correct_no": 1}
    assert [choice["no"] for choice in projection["choices"]] == [1, 2, 3, 4, 5]
    assert projection["status"] == ProblemItemStatus.NEEDS_REVIEW.value
    # 🔴 사유가 있으면 배지가 선다 — 배지 하나로 사유를 되살릴 수는 없다(§2-20 #10).
    assert projection["review_badge"] is True
    assert projection["difficulty_est"] == Decimal("0.62")
    # 계약이 None으로 못 박은 자리 · 저장 시점에 값이 없는 자리 — 지어내지 않는다.
    assert projection["difficulty_fit"] is None
    assert projection["difficulty_calib_ver"] is None


def test_review_badge_is_false_without_reason() -> None:
    """사유가 없으면 배지도 없다 — boolean이 사유의 존재만 비춘다."""
    assert problem_item_projection(_record())["review_badge"] is False


def test_body_projections_are_null_for_a_slot_without_item() -> None:
    """본문 없는 슬롯 — 투영은 원본이 없을 때 NULL이다(결정 ③ ⓒ)."""
    projection = problem_item_projection(_record(with_body=False))
    assert all(projection[name] is None for name in _BODY_PROJECTIONS)
    # 슬롯은 여전히 존재한다 — 조회 키와 상태는 남는다.
    assert projection["slot_index"] == 0
    assert projection["status"] == ProblemItemStatus.VERIFICATION_UNAVAILABLE.value


@pytest.mark.parametrize("slot_index", [0, 1, 7])
def test_snapshot_roundtrips_without_loss(slot_index: int) -> None:
    """🔴 정본 왕복 — `model_dump(mode="json")` → `model_validate`가 동치다.

    §2-20이 센 결손 12건은 **컬럼으로 되살릴 수 없는 값들**이었다. 스냅숏이 정본이면
    그 12건이 전부 여기로 돌아온다 — 그것이 결정 ② B안의 유일한 근거다.
    """
    record = _record(slot_index=slot_index)
    restored = StoredProblemItem.model_validate(record.model_dump(mode="json"))
    assert restored == record
    assert restored.result.difficulty_band is DifficultyBand.MEDIUM
    assert restored.item is not None
    assert restored.item.evidence == record.item.evidence if record.item else False


def test_uuid5_slot_identity_is_deterministic() -> None:
    """멱등이 PK로 서려면 같은 슬롯이 같은 행이어야 한다."""
    assert problem_item_id(_SET_ID, 3) == problem_item_id(_SET_ID, 3)
    assert problem_item_id(_SET_ID, 3) != problem_item_id(_SET_ID, 4)
    assert problem_item_id(uuid4(), 3) != problem_item_id(_SET_ID, 3)
