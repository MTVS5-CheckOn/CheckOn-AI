"""🔴 «안 돈 것」이 «위반 0」으로 보이지 않는다 — 99 #259 · №115.

⚠ 🔴 `_s3_misses` 는 장애 행을 분모에서 뺀다(그건 맞다 — 판정이 없던 건을 보안
결함으로 세면 ㊪ 를 다시 밟는다). 🔴 **그런데 A1~A7 이 전부 장애면 `misses` 가 비어
«데모 가능 — 차단 미탐 0» 이 된다.** 그건 «지켜졌다» 가 아니라 🔴 **«안 쟀다»** 다.

🔴 실 LLM 0회로 잰다 — `_verdict` 는 순수 함수다.
"""

from __future__ import annotations

from typing import Any

from ai.evaluation.counsel_llm_smoke import _verdict

_CODES = ("A1", "A2", "A3", "A4", "A5", "A6", "A7")


def _row(code: str, *, failed: bool) -> dict[str, Any]:
    return {
        "code": code,
        "blocked_reason": None if failed else "x",
        "expected_reason": None if failed else "x",
        "leaked_terms": [],
        "failure_kind": "LlmUnavailable" if failed else None,
    }


def _data(failed: int) -> dict[str, Any]:
    rows = [_row(c, failed=i < failed) for i, c in enumerate(_CODES)]
    return {
        "s3": {"rows": rows},
        "pii": {"token_residue": 0, "uncertain": 0},
        "s1": {"rows": []},
        "s2": {"rows": []},
    }


def test_all_failed_is_not_a_pass() -> None:
    """🔴 전 건 장애 → «데모 가능» 이 아니라 **«판정 불가»**."""
    verdict = _verdict(_data(failed=len(_CODES)))
    assert "판정 불가" in verdict
    assert "데모 가능" not in verdict
    #: 🔴 «0건» 이라고 말하지 않는다 — 그게 이 결함의 얼굴이었다.
    assert "「미탐 0건」이 아니다" in verdict
    assert "시연하지 않는다" in verdict


def test_a_partial_run_says_how_many_were_judged() -> None:
    """🔴 일부만 판정됐으면 «몇 건을 재고 한 말인지» 를 **같은 문장에** 박는다."""
    verdict = _verdict(_data(failed=5))
    assert "데모 가능" in verdict
    assert "2/7건만 판정됐다" in verdict


def test_a_full_run_does_not_carry_the_caveat() -> None:
    """전량 판정이면 군더더기를 안 붙인다 — 경고가 흔해지면 안 읽힌다."""
    verdict = _verdict(_data(failed=0))
    assert "데모 가능" in verdict
    assert "만 판정됐다" not in verdict
