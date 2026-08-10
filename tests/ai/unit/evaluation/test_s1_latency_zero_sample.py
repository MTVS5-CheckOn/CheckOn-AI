"""성공 표본이 **0건**일 때 §1이 「0.0초 (부합)」을 찍는가 (99 ⓥ ⓔ 후속).

🔴 **앞 PR이 분모를 좁히면서 만든 구멍이다.** 지연 통계를 `outcome == "ok"` 행으로 좁혔는데,
**성공이 하나도 없으면** `latencies`가 비어 `median_ms`·`max_ms`가 **`0`으로 떨어진다.**
그리고 렌더는 `0 <= 1200`이므로 **「실측 중앙값 0.0초 (부합)」**을 찍는다.

⚠ **전 호출이 실패한 회차가 성능 기준을 통과한 것으로 보고된다** — 좁히기 전(전체 행 기준)
보다 **더 나쁘다.** 종전엔 실패 행의 작은 지연이라도 **실측값**이었는데, 지금은
**측정한 적 없는 0**이 판정에 들어간다.

🔴 **규칙: 표본이 없으면 판정을 안 찍는다.** 결손은 결손으로(`None`) 나오고,
문면은 **「측정 불가 — 성공 표본 0건」**이다. **0ms를 실측값처럼 표시하지 않는다.**
⚠ 임계 `1200`은 이 파일의 축이 아니다 — **표본 0건에서의 판정 유무**만 본다.
"""

from __future__ import annotations

from typing import Any, Final

from ai.evaluation.counsel_llm_smoke import (
    _s1_console_latency,
    _s1_latency_verdict,
    _s1_summary,
)

_SUCCESS_MS: Final = (1500, 1600, 1700)
_FAILED_MS: Final = (20, 25, 30, 35)
#: 🔴 판정을 찍었다는 표식 — 둘 중 **하나라도** 나오면 표본 0건에서 판정한 것이다.
_VERDICT_MARKS: Final = ("부합", "미달")
#: 결손을 정직하게 말하는 문면의 앵커.
_WITHHELD: Final = "측정 불가"


def _row(elapsed_ms: int, *, ok: bool) -> dict[str, Any]:
    return {
        "elapsed_ms": elapsed_ms,
        "outcome": "ok" if ok else "invalid_schema",
        "gate_passed": ok,
        "attempts": 1,
        "fallback_used": False,
        "mask_residue": False,
    }


def _all_failed() -> list[dict[str, Any]]:
    return [_row(ms, ok=False) for ms in _FAILED_MS]


def test_the_zero_sample_case_is_actually_reachable() -> None:
    """🔴 절단 가드 — 합성 표본이 실제로 **성공 0건**이어야 이 파일이 무언가를 본다."""
    summary = _s1_summary(_all_failed())
    assert summary["total"] > 0, "행이 없으면 다른 것을 재게 된다"
    assert summary["latency_sample"] == 0, (
        f"합성 표본에 성공 행이 섞였다({summary['latency_sample']}건) — 축이 틀렸다"
    )


def test_the_missing_measurement_is_not_rendered_as_zero() -> None:
    """🔴 **측정한 적 없는 값은 `0`이 아니라 결손이다.**

    `0`으로 두면 *"0ms 걸렸다"* 와 구분이 안 되고 **비교 연산자에 그대로 들어간다.**
    """
    summary = _s1_summary(_all_failed())
    assert summary["median_ms"] is None, (
        f"성공 표본이 0건인데 중앙값이 실측값처럼 나온다: {summary['median_ms']!r}"
    )
    assert summary["max_ms"] is None, (
        f"성공 표본이 0건인데 최대값이 실측값처럼 나온다: {summary['max_ms']!r}"
    )


def test_the_verdict_is_withheld_without_a_success_sample() -> None:
    """🔴 **표본이 없으면 「부합」도 「미달」도 안 찍는다** — 정직한 문면은 「측정 불가」다."""
    line = _s1_latency_verdict(_s1_summary(_all_failed()))
    struck = [mark for mark in _VERDICT_MARKS if mark in line]
    assert not struck, f"성공 표본 0건인데 판정을 찍었다({struck}): {line}"
    assert _WITHHELD in line, f"결손을 정직하게 말하지 않는다: {line}"
    assert "0.0초" not in line, f"측정한 적 없는 0을 실측값처럼 적는다: {line}"


def test_the_console_line_withholds_the_same_way() -> None:
    """🔴 **리포트만 고치면 콘솔이 거짓말을 계속한다** — 실행 끝에 찍히는 줄이 먼저 읽힌다."""
    line = _s1_console_latency(_s1_summary(_all_failed()))
    assert "0ms" not in line, f"콘솔이 측정한 적 없는 0ms를 찍는다: {line}"
    assert _WITHHELD in line, f"콘솔이 결손을 정직하게 말하지 않는다: {line}"


def test_a_single_success_still_gets_a_verdict() -> None:
    """🔴 **뒤집기 — 기존 경로는 그대로다.** 성공이 하나라도 있으면 판정을 찍는다."""
    rows = _all_failed() + [_row(_SUCCESS_MS[0], ok=True)]
    summary = _s1_summary(rows)
    assert summary["median_ms"] == _SUCCESS_MS[0], "성공 1건의 중앙값은 그 값이다"
    line = _s1_latency_verdict(summary)
    assert any(mark in line for mark in _VERDICT_MARKS), f"판정이 사라졌다: {line}"
    assert _WITHHELD not in line, f"표본이 있는데 측정 불가로 적는다: {line}"
    assert "성공 1건 기준" in line, f"분모가 사라졌다: {line}"


def test_the_existing_verdict_still_flips_on_the_threshold() -> None:
    """🔴 **판정 자체가 살아 있는지** — 성공 표본이 임계 위면 「미달」이어야 한다."""
    slow = _s1_latency_verdict(_s1_summary([_row(ms, ok=True) for ms in _SUCCESS_MS]))
    fast = _s1_latency_verdict(_s1_summary([_row(20, ok=True), _row(25, ok=True)]))
    assert "미달" in slow, f"임계 위인데 미달이 아니다: {slow}"
    assert "부합" in fast, f"임계 아래인데 부합이 아니다: {fast}"
