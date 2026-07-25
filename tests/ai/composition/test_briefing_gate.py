"""브리핑 왜곡 게이트 단위 — 숫자 EXACT·금칙어·⟪⟫토큰·길이 (masking_redaction·05 §4).

게이트는 결정론(LLM 금지) — 통과분만 [표시]로 나간다.
"""

from __future__ import annotations

from ai.composition.briefing_gate import MAX_BRIEF_LENGTH, check_brief_gate


def test_number_grounded_passes() -> None:
    assert check_brief_gate("3주째 늘고 있어요", frozenset({"3"})).passed


def test_number_not_grounded_fails() -> None:
    """입력(초안)에 없는 숫자는 실패 — fabrication 차단(report numbers_used 선례, 변환 불허)."""
    result = check_brief_gate("21일째 늘고 있어요", frozenset({"3"}))
    assert not result.passed
    assert result.reason == "number_not_grounded:21"


def test_no_number_always_passes_number_check() -> None:
    assert check_brief_gate("정답률이 떨어지고 있어요", frozenset()).passed


def test_forbidden_word_fails() -> None:
    # 05 §4 A군은 어간 기반이라 substring이 정확히 걸리는 형태로 검증(문제아·꼴찌 등 명사형).
    result = check_brief_gate("이 학생은 문제아 같아요", frozenset())
    assert not result.passed
    assert result.reason.startswith("forbidden:")


def test_forbidden_stem_base_form_fails() -> None:
    """어간이 문자 그대로 나타나는 형(뒤처졌다)은 걸린다 — 활용 변형은 §6 한계."""
    assert not check_brief_gate("성적이 뒤처졌어요", frozenset()).passed


def test_comparison_frame_forbidden() -> None:
    assert not check_brief_gate("다른 아이들은 다 하는데요", frozenset()).passed


def test_token_leak_fails() -> None:
    """brief는 [표시] 직행 — ⟪⟫ 토큰 노출 금지(분기표 #4)."""
    result = check_brief_gate("⟪이름1⟫ 학생이 힘들어해요", frozenset())
    assert not result.passed
    assert result.reason == "token_leak"


def test_too_long_fails() -> None:
    text = "가" * (MAX_BRIEF_LENGTH + 1)
    result = check_brief_gate(text, frozenset())
    assert not result.passed
    assert result.reason == "too_long"


def test_length_boundary_passes() -> None:
    assert check_brief_gate("가" * MAX_BRIEF_LENGTH, frozenset()).passed
