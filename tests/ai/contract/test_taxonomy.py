"""contracts/taxonomy.py 스모크 — enum 값 고정 + 왕복 직렬화.

값 고정 테스트의 목적은 **몰래 변경 방지**다. 이 파일이 깨지면 어휘집
(docs/policies/taxonomy.md)과 양자 승인(docs/02_ownership.md §4)을 거쳐야 한다.
테스트를 기대값에 맞추는 방향으로 고치면 안 된다.
"""

import pytest

from ai.contracts.taxonomy import (
    COMMON_AREAS,
    RESERVED_TYPE_TAGS,
    SUPPORTED_ITEM_FORMATS,
    V1_TYPE_TAGS,
    AreaTag,
    ItemFormat,
    ItemTags,
    SubjectTrack,
    TypeTag,
    derive_subject_track,
)


def test_area_tag_values_frozen() -> None:
    """수능 **출제 5영역** — 어휘집 §1(2026-08-09 B 확정). 소비처 4곳이 같은 어휘를 쓴다.

    🔴 **종전 6영역에서 `speech`·`writing`이 `speech_writing` 하나가 됐다.** 축이 과목이
    아니라 **출제 단위**이고, 그 둘은 같은 출제 규격으로 만들어진다. 반대로 `language`·
    `media`는 같은 선택과목이지만 **문항 형태가 달라 그대로 둘**이다.
    """
    assert {tag.value for tag in AreaTag} == {
        "language",
        "media",
        "literature",
        "reading",
        "speech_writing",
    }


def test_area_tag_declaration_order_is_the_display_order() -> None:
    """표시 순서가 곧 선언 순서다 — 강사 화면·요청 파라미터의 기본 나열."""
    assert [tag.value for tag in AreaTag] == [
        "language",
        "media",
        "literature",
        "reading",
        "speech_writing",
    ]


def test_subject_track_values_frozen() -> None:
    assert {track.value for track in SubjectTrack} == {"common", "elective"}


def test_type_tag_values_frozen() -> None:
    """인지 유형 — 어휘집 §3. **평가원 5축** · `apply`는 예약값(99 ㊣).

    ⚠ `item_format`의 `short`·`essay`와 같은 예약이지만 처방이 다르다 — `apply`는
    **입력으로 받고 표시까지 하되 산출·출제 요청만 막는다**(문마다 답이 다르다).
    """
    assert {tag.value for tag in TypeTag} == {
        "fact",
        "infer",
        "critic",
        "concept",
        "apply",
    }
    assert {tag.value for tag in V1_TYPE_TAGS} == {"fact", "infer", "critic", "concept"}
    assert RESERVED_TYPE_TAGS == frozenset({TypeTag.APPLY})


def test_item_format_values_frozen() -> None:
    """문항 형식 — 어휘집 §4. short·essay는 예약값."""
    assert {fmt.value for fmt in ItemFormat} == {"mcq", "short", "essay"}


def test_v1_supports_mcq_only() -> None:
    """7/15 확정: v1은 mcq만 사용 (CLAUDE.md §3).

    이 단언이 깨졌다면 예약값 처리 분기가 들어왔다는 뜻이다 — 어휘집 §4 재론 없이는
    통과시키지 않는다.
    """
    assert SUPPORTED_ITEM_FORMATS == frozenset({ItemFormat.MCQ})
    assert ItemFormat.SHORT not in SUPPORTED_ITEM_FORMATS
    assert ItemFormat.ESSAY not in SUPPORTED_ITEM_FORMATS


def test_common_areas_frozen() -> None:
    """공통 과목 = 독서·문학 (어휘집 §1)."""
    assert COMMON_AREAS == frozenset({AreaTag.READING, AreaTag.LITERATURE})


@pytest.mark.parametrize(
    ("area", "expected"),
    [
        (AreaTag.READING, SubjectTrack.COMMON),
        (AreaTag.LITERATURE, SubjectTrack.COMMON),
        (AreaTag.SPEECH_WRITING, SubjectTrack.ELECTIVE),
        (AreaTag.LANGUAGE, SubjectTrack.ELECTIVE),
        (AreaTag.MEDIA, SubjectTrack.ELECTIVE),
    ],
)
def test_derive_subject_track_covers_all_areas(area: AreaTag, expected: SubjectTrack) -> None:
    assert derive_subject_track(area) == expected


def test_item_tags_roundtrip() -> None:
    tags = ItemTags(area=AreaTag.LITERATURE, type=TypeTag.INFER)
    assert ItemTags.model_validate(tags.model_dump(mode="json")) == tags


def test_item_tags_defaults_to_mcq() -> None:
    assert ItemTags(area=AreaTag.READING, type=TypeTag.FACT).item_format is ItemFormat.MCQ


def test_item_tags_rejects_unknown_field() -> None:
    """extra='forbid' — 계약 밖 필드를 조용히 흘려보내지 않는다."""
    with pytest.raises(ValueError, match="difficulty"):
        ItemTags.model_validate(
            {"area": "reading", "type": "fact", "difficulty": "hard"},
        )


def test_item_tags_rejects_unknown_area() -> None:
    with pytest.raises(ValueError, match="area"):
        ItemTags.model_validate({"area": "grammar", "type": "fact"})
