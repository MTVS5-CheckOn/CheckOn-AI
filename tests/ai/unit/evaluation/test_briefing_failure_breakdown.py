"""🔴 측정기가 **갈래를 남기는지** — 99 #255 · №110.

⚠ 🔴 종전 측정기는 사유별 `Counter` 만 남겨 «21건이 전부 `LlmUnavailable`» 까지만
말했다. 🔴 «어느 신호에 몰렸나」·«시간에 몰렸나」를 못 답했고, 그 둘은 **처방이 갈린다.**
⇒ 여기서 **재는 도구 자체**를 잰다(실 LLM 0회 — 합성 원장으로).
"""

from __future__ import annotations

from ai.evaluation.briefing_preview import _breakdown, _Failure, _Ledger, _scrub


def _ledger(*failures: _Failure) -> _Ledger:
    return _Ledger(started=0.0, max_calls=66, ordinal=66, failures=list(failures))


def _fail(ordinal: int, signal: str, *, kind: str = "llm:LlmUnavailable") -> _Failure:
    return _Failure(
        ordinal=ordinal,
        at_s=float(ordinal),
        signal=signal,
        variant="v1" if ordinal % 2 else "v3",
        kind=kind,
        detail="LLM 연결 실패",
    )


def test_no_failures_says_so() -> None:
    assert "가를 것이 없다" in "\n".join(_breakdown(_ledger(), 11, 66))


def test_the_signal_axis_is_kept() -> None:
    """🔴 ① 어느 신호가 몇 번 죽었나."""
    report = "\n".join(
        _breakdown(_ledger(_fail(1, "a"), _fail(2, "a"), _fail(3, "b")), 11, 66)
    )
    assert "2/11 신호에서 발생" in report
    assert "| a | 2 |" in report
    assert "| b | 1 |" in report


def test_the_time_axis_separates_front_from_back() -> None:
    """🔴 ② «신호 탓」과 «율속 탓」을 가르는 축 — 앞으로 몰린 경우."""
    front = "\n".join(_breakdown(_ledger(*(_fail(i, f"s{i}") for i in range(1, 6))), 11, 66))
    assert "앞 1/3 5건 · 중 1/3 0건 · 뒤 1/3 0건" in front
    back = "\n".join(_breakdown(_ledger(*(_fail(i, f"s{i}") for i in range(62, 67))), 11, 66))
    assert "앞 1/3 0건 · 중 1/3 0건 · 뒤 1/3 5건" in back


def test_the_cause_text_is_counted() -> None:
    """🔴 ③ 원인 문면 — «연결 실패」와 «429」는 처방이 다르다."""
    report = "\n".join(
        _breakdown(
            _ledger(
                _fail(1, "a"),
                _Failure(2, 2.0, "b", "v3", "llm:LlmUnavailable", "LLM 일시 실패 429"),
            ),
            11,
            66,
        )
    )
    assert "`LLM 연결 실패` — **1건**" in report
    assert "`LLM 일시 실패 429` — **1건**" in report


def test_the_cause_text_never_carries_a_key() -> None:
    """🔴 키는 어떤 형태로도 안 나간다 — 어댑터가 바뀌어도 여기가 마지막 관문."""
    scrubbed = _scrub("auth failed for sk-abcdef0123456789 at vendor")
    assert "sk-abcdef0123456789" not in scrubbed
    assert "sk-***" in scrubbed


def test_the_cause_text_is_bounded() -> None:
    """길이 상한 — 산출 본문이 갈래 표로 새지 않게(불변식 3 · 99 #80)."""
    assert len(_scrub("가" * 500)) <= 80
