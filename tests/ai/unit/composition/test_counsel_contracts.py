"""상담팩 계약 — ERD 정합 · tone_map 축 어휘 대조 · 실명 필드 부재.

정본: `docs/06_erd.md` DRAFT·DRAFT_BLOCK · `error_codes` §2.1 · 05 §1.
기대값은 문서에서 손으로 옮겼다.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai.composition.tone import combination_key, load_tone_map
from ai.contracts.composition import (
    BlockType,
    CommStyle,
    DraftBlock,
    DraftContext,
    DraftKind,
    DraftStatus,
    EvidenceFact,
    Frequency,
    Interest,
    LabelSnapshot,
    Sensitivity,
    StudentResult,
)
from ai.db.models import Draft
from ai.db.models import DraftBlock as DraftBlockRow


def _label() -> LabelSnapshot:
    return LabelSnapshot(
        comm=CommStyle.DATA,
        sensitivity=Sensitivity.ANXIOUS,
        interest=Interest.GRADE,
        frequency=Frequency.FREQUENT,
    )


def _context() -> DraftContext:
    return DraftContext(
        student_ref="st_8f2a",
        guardian_ref="gd_1c33",
        label_snapshot=_label(),
        facts=(EvidenceFact(label="이번 주 정답률", value="62%"),),
        evidence_summaries=("지난주 과제 2건 미제출",),
        period_label="2026년 7월",
        fallback_text="이번 주 학습 상황을 정리해 보내드립니다.",
    )


# ── ERD 값 집합 대조 ──────────────────────────────────────────────


def test_draft_kind_matches_erd() -> None:
    """ERD DRAFT.kind "reply|report|counsel_pack"."""
    assert {k.value for k in DraftKind} == {"reply", "report", "counsel_pack"}


def test_draft_status_matches_erd_and_error_codes() -> None:
    """ERD DRAFT.status = error_codes §2.1의 종단 4종(queued·generating은 phase 쪽)."""
    assert {s.value for s in DraftStatus} == {
        "generated",
        "template_only",
        "rejected_insufficient",
        "failed",
    }


def test_block_type_matches_erd() -> None:
    """ERD DRAFT_BLOCK.block_type "greeting|fact|suggestion|closing"."""
    assert {b.value for b in BlockType} == {"greeting", "fact", "suggestion", "closing"}


def test_draft_block_regen_cap_is_three() -> None:
    """ERD 주석 "≤3" — 불변식 6(모든 루프에 상한)."""
    DraftBlock(seq=0, block_type=BlockType.FACT, content="본문", regen_count=3)
    with pytest.raises(ValidationError):
        DraftBlock(seq=0, block_type=BlockType.FACT, content="본문", regen_count=4)


def test_orm_tables_exist_unchanged() -> None:
    """DRAFT·DRAFT_BLOCK ORM은 이미 있다 — 이 PR은 스키마 무변경이다."""
    assert "label_snapshot" in Draft.__table__.columns
    assert set(DraftBlockRow.__table__.columns.keys()) >= {
        "draft_id",
        "seq",
        "block_type",
        "content",
        "regen_count",
    }


# ── tone_map 축 어휘 대조 (조회 시 변환이 끼지 않게) ─────────────────


def test_label_axes_match_tone_map_vocabulary() -> None:
    """4축 enum 값이 tone_map.yaml의 axis_rules 어휘와 정확히 일치."""
    axis_rules = load_tone_map().axis_rules
    assert {c.value for c in CommStyle} == set(axis_rules["comm"])
    assert {s.value for s in Sensitivity} == set(axis_rules["sensitivity"])
    assert {i.value for i in Interest} == set(axis_rules["interest"])
    assert {f.value for f in Frequency} == set(axis_rules["frequency"])


def test_as_axes_field_names_match_tone_map_axes() -> None:
    """as_axes()의 키가 axis_rules 축 이름과 같다 — 변환 코드 불필요."""
    assert set(_label().as_axes()) == set(load_tone_map().axis_rules)


def test_label_snapshot_resolves_a_tone_rule() -> None:
    """조합 키로 바로 조회된다 — combination_key(**as_axes())."""
    tone_map = load_tone_map()
    key = combination_key(**_label().as_axes())
    assert key == "data.anxious.grade.frequent"
    assert tone_map.combinations[key].buffer_level == 2  # 05 §2 1행


def test_every_label_combination_has_a_rule() -> None:
    """4축 enum의 데카르트곱 24개가 전부 tone_map에 있다."""
    tone_map = load_tone_map()
    for comm in CommStyle:
        for sens in Sensitivity:
            for interest in Interest:
                for freq in Frequency:
                    label = LabelSnapshot(
                        comm=comm, sensitivity=sens, interest=interest, frequency=freq
                    )
                    assert combination_key(**label.as_axes()) in tone_map.combinations


# ── 불변식 3 · allowed_numbers ────────────────────────────────────


def test_no_realname_fields() -> None:
    """실명·연락처 필드 금지 — alias만(불변식 3)."""
    forbidden = {"name", "student_name", "guardian_name", "phone", "guardian_phone", "email"}
    assert not forbidden & set(DraftContext.model_fields)


def test_allowed_numbers_is_exact_set_from_context() -> None:
    """게이트 EXACT 허용집합 — facts·evidence_summaries의 숫자만."""
    assert _context().allowed_numbers() == frozenset({"62", "2"})


def test_allowed_numbers_empty_when_no_facts() -> None:
    context = _context().model_copy(update={"facts": (), "evidence_summaries": ()})
    assert context.allowed_numbers() == frozenset()


def test_context_is_frozen_and_forbids_extra() -> None:
    with pytest.raises(ValidationError):
        DraftContext(**{**_context().model_dump(), "unexpected": 1})


def test_student_result_holds_pointer_not_body() -> None:
    """state에 실리는 결과는 draft_id 포인터뿐 — 본문 없음(§1.2 ⑨)."""
    fields = set(StudentResult.model_fields)
    assert fields == {"student_ref", "draft_id", "status", "fail_reason"}
    assert not {"content", "blocks", "text"} & fields
