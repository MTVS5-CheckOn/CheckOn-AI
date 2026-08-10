"""§1의 「부합/미달」 판정이 **실패 행에 흔들리는가** (99 ⓥ ⓔ).

🔴 **묻는 것은 「중앙값 계산이 맞나」가 아니다** — 그건 동어반복이다.
**「판정이 실패 행에 흔들리나」**를 묻는다.

등재문의 지적 그대로다: *"§1의 「실측 중앙값 0.2초 (부합)」 — 그 0.2초는 **400이 즉시
떨어진 시간**인데 러너가 `outcome`을 안 보고 지연만 비교한다."*

⚠ **실패 행은 지연이 매우 작다**(문 앞에서 떨어진다) — 표본에 섞이면 중앙값을 **아래로
끌어내려** 성공 경로가 느린데도 **「부합」**을 만든다.

🔴 **규칙은 하나다: 「부합/미달」 판정과 그 근거 수치는 같은 분모에서 나온다.**
⚠ 임계 `1200`은 이 파일의 축이 아니다(기획서 검증치 대조 축 · 별건).
"""

from __future__ import annotations

from typing import Any, Final

from ai.evaluation.counsel_llm_smoke import _s1_summary

#: 🔴 **성공만 보면 미달, 섞으면 부합**이 되는 표본 — 그게 이 검사의 축이다.
#: 성공 셋이 전부 임계(1200ms) 위인데 **실패 둘이 섞이면 중앙값이 임계 아래로 내려간다.**
_SUCCESS_MS: Final = (1500, 1600, 1700)
_FAILED_MS: Final = (20, 25, 30, 35)
_THRESHOLD_MS: Final = 1200


def _row(elapsed_ms: int, *, ok: bool) -> dict[str, Any]:
    return {
        "elapsed_ms": elapsed_ms,
        "outcome": "ok" if ok else "invalid_schema",
        "gate_passed": ok,
        "attempts": 1,
        "fallback_used": False,
        "mask_residue": False,
    }


def test_the_sample_actually_flips_the_verdict() -> None:
    """🔴 **절단 가드 — 표본이 판정을 안 뒤집으면 이 파일은 아무것도 안 본다.**

    ⚠ **표본을 두 번 고쳤다.** 지시서의 예시(성공 3 + 실패 2)는 **안 뒤집히고**(5개 중
    3번째가 1500), 실패 셋도 **안 뒤집힌다**(6개 중 `len//2`=3번째가 **1500**이다 — 실측).
    🔴 **실패가 성공보다 많아야** `len//2`가 실패 구간에 떨어진다 ⇒ 성공 3 + 실패 **4**.
    **예시가 안 뒤집히면 예시가 틀린 것이고, 그것을 이 가드가 잡았다.**
    """
    mixed = sorted(_SUCCESS_MS + _FAILED_MS)
    success_median = sorted(_SUCCESS_MS)[len(_SUCCESS_MS) // 2]
    mixed_median = mixed[len(mixed) // 2]
    assert success_median > _THRESHOLD_MS, "성공 표본이 임계 위여야 한다"
    assert mixed_median <= _THRESHOLD_MS, (
        f"섞은 중앙값이 임계 아래로 안 내려간다({mixed_median}) — 표본이 판정을 안 뒤집는다"
    )


def test_the_latency_stat_is_not_dragged_down_by_failed_rows() -> None:
    """🔴 **판정 수치가 실패 행에 흔들리면 안 된다.**

    고치기 전: `median_ms`가 **섞인 분모**에서 나와 `30`(부합)이다 —
    성공 경로는 전부 1500ms 위인데 **「부합」이 찍힌다.**
    """
    rows = [_row(ms, ok=True) for ms in _SUCCESS_MS]
    rows += [_row(ms, ok=False) for ms in _FAILED_MS]
    summary = _s1_summary(rows)

    assert summary["median_ms"] > _THRESHOLD_MS, (
        f"실패 행이 지연 중앙값을 끌어내렸다({summary['median_ms']}ms) — "
        "성공 경로는 전부 임계 위인데 판정이 「부합」이 된다(99 ⓥ ⓔ)"
    )


def test_the_denominator_is_reported_next_to_the_number() -> None:
    """🔴 **분모가 수치 옆에 있어야 한다** — 「N건 중 M건 기준」.

    ⚠ 좁히기만 하고 분모를 안 적으면 **표본이 몇이었는지**를 다음 사람이 못 안다 —
    성공 1건의 중앙값과 성공 20건의 중앙값은 같은 무게가 아니다.
    """
    rows = [_row(ms, ok=True) for ms in _SUCCESS_MS]
    rows += [_row(ms, ok=False) for ms in _FAILED_MS]
    summary = _s1_summary(rows)

    assert summary.get("latency_sample") == len(_SUCCESS_MS), (
        f"지연 통계의 표본 수가 없거나 틀렸다: {summary.get('latency_sample')!r}"
    )
    assert summary["total"] == len(rows), "전체 건수는 그대로여야 한다(다른 축이다)"
