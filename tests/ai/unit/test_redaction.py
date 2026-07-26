"""redaction 패턴별 단위 검사 — masking_redaction.md §2 '검증 케이스' 열 + §1 토큰 규격.

코퍼스 게이트(golden)와 별개로, §2 표의 패턴별 통과 조건을 하나씩 고정한다.
"""

from __future__ import annotations

import pytest

from ai.runtime.redaction import redact


def _masked(text: str) -> str:
    return redact(text).masked_text


# ── §2 검증 케이스 열 ──────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    ["010-1234-5678", "01012345678", "02 123 4567", "031-123-4567"],
)
def test_p2_phone_detected(text: str) -> None:
    assert "⟪연락처1⟫" in _masked(text)


def test_p2_date_not_detected_as_phone() -> None:
    """'2026-07-14'(날짜)는 전화번호로 미검출(§2 P2)."""
    assert _masked("시험은 2026-07-14 입니다") == "시험은 2026-07-14 입니다"


def test_p3_email_detected() -> None:
    assert "⟪이메일1⟫" in _masked("parkgom93@gmail.com 으로 보내요")


def test_p4_full_address_detected() -> None:
    assert "대치동" not in _masked("서울시 강남구 대치동 123-4")


def test_p4_dong_only_masked() -> None:
    """'대치동 학원가 분위기'는 동 단위까지만 마스킹(§2 P4)."""
    masked = _masked("대치동 학원가 분위기가 좋아요")
    assert "대치동" not in masked
    assert "학원가" in masked


def test_p5_school_detected() -> None:
    assert "한빛중학교" not in _masked("한빛중학교 2학년이에요")


def test_p5_grade_kept() -> None:
    """'고3'(학년)은 유지(§2 P5)."""
    assert _masked("고3 올라가요") == "고3 올라가요"


def test_p5_abbr_not_over_masking_verb_ending() -> None:
    """'그만두고'처럼 -고로 끝나는 어미는 학교로 오탐하지 않는다(문맥 게이팅)."""
    assert _masked("이제 그만두고 쉴래요") == "이제 그만두고 쉴래요"


def test_p6_rrn_detected() -> None:
    assert "⟪생년월일1⟫" in _masked("주민번호 010101-3234567 입니다")


def test_p6_birthdate_with_context_detected() -> None:
    """'2010년 3월 5일생'은 검출(문맥 단어 생, §2 P6)."""
    assert "2010" not in _masked("2010년 3월 5일생이에요")


def test_p6_examdate_without_context_kept() -> None:
    """시험 날짜(생·출생 문맥 없음)는 유지 — 생년월일 오탐 방지."""
    assert _masked("시험 2026년 3월 5일 봐요") == "시험 2026년 3월 5일 봐요"


def test_p7_student_id_with_label_detected() -> None:
    assert "20241234" not in _masked("학번 20241234 입니다")


def test_p8_korean_digit_decoded() -> None:
    """'공일공…' 한글숫자 디코딩 후 P2(§2 P8 · [A 확정 7/23])."""
    assert "⟪연락처1⟫" in _masked("공일공일이삼사오육칠팔")


def test_p1b_honorific_name_confident() -> None:
    assert _masked("김서연 어머니께서") == "⟪이름1⟫ 어머니께서"


# ── §1 토큰 규격 ──────────────────────────────────────────


def test_same_value_same_number() -> None:
    """같은 값은 같은 번호(문맥 일관성, §1)."""
    masked = _masked("김서연 학생과 김서연 학생이 다퉜어요")
    assert masked.count("⟪이름1⟫") == 2
    assert "⟪이름2⟫" not in masked


def test_distinct_values_distinct_numbers() -> None:
    masked = _masked("010-1111-2222 그리고 010-3333-4444")
    assert "⟪연락처1⟫" in masked
    assert "⟪연락처2⟫" in masked


def test_findings_do_not_leak_original_values() -> None:
    """단방향(§1): findings에 원문↔토큰 매핑을 남기지 않는다."""
    result = redact("김서연 어머니 010-1234-5678")
    for finding in result.findings:
        assert "김서연" not in finding.type and "김서연" not in finding.token
        assert "1234" not in finding.token
    # 반환값 어디에도 원문 조각이 없어야(masked_text 제외)
    joined = " ".join(f"{f.type}{f.token}" for f in result.findings)
    assert "김서연" not in joined and "5678" not in joined


def test_uncertain_marks_flag() -> None:
    result = redact("서연이가 왔어요")
    assert "⟪확인필요⟫" in result.masked_text
    assert result.uncertain is True


def test_clean_text_unchanged_and_certain() -> None:
    result = redact("오늘 수업은 비문학 독해였어요")
    assert result.masked_text == "오늘 수업은 비문학 독해였어요"
    assert result.uncertain is False
    assert result.findings == ()
