"""기대치 입력 층 — 지문 × 유형 (04 §1 "기대치 입력 층" · 13 §4-3-4).

"어려운 지문이 걸린 주라 떨어진 것"과 "진짜 무너진 것"을 구분한다.

🔴 **핵심 안전장치: 콜드 스타트 회귀 위험 0.** 통계가 비면 전량 폴백(전체 평균)이라
**잔차 ≈ 기존 하락폭**이 되어 판정이 종전과 같아진다. 이게 설계 의도다.

전 경로 결정론이다 — LLM이 끼면 설계가 잘못된 것이다.
"""

from __future__ import annotations

from ai.contracts.detection import LearningEvent
from ai.detection.expectation import (
    ExpectationStats,
    PassageTypeKey,
    expected_accuracy,
)

_MIN_N = 30


def _stat(responses: int, corrects: int) -> tuple[int, int]:
    return responses, corrects


_Event = tuple[str | None, str | None, bool]


def _events(*specs: _Event) -> list[_Event]:
    return list(specs)


# ── 계약 — passage_ref 수신 ──────────────────────────────────────


def test_learning_event_accepts_passage_ref() -> None:
    event = LearningEvent.model_validate(
        {
            "record_id": "le_1",
            "student_ref": "st_1",
            "type": "solve",
            "occurred_at": "2026-07-08T19:20:00+09:00",
            "correct": True,
            "passage_ref": "ps_4471",
            "type_tag": "infer",
            "source": "trackB",
        }
    )
    assert event.passage_ref == "ps_4471"


def test_passage_ref_is_optional() -> None:
    """지문 없는 문항(문법 단문·어휘)·묶음 개념 없는 학원 — 비워도 정상이다."""
    event = LearningEvent.model_validate(
        {
            "record_id": "le_2",
            "student_ref": "st_1",
            "type": "solve",
            "occurred_at": "2026-07-08T19:20:00+09:00",
            "correct": False,
            "source": "trackA",
        }
    )
    assert event.passage_ref is None


# ── 🔴 콜드 스타트 — 회귀 위험 0 ────────────────────────────────


def test_cold_start_falls_back_to_overall_mean() -> None:
    """🔴 통계가 비면 기대 = 전체 평균 — 보정 없음과 **동치**다."""
    stats = ExpectationStats(combos={}, overall_responses=0, overall_corrects=0)
    result = expected_accuracy(
        _events(("ps_1", "infer", True), ("ps_1", "infer", False)),
        stats=stats,
        min_n=_MIN_N,
        session_overall=0.5,
    )
    assert result.expected == 0.5
    assert result.fallback_events == 2
    assert result.combo_events == 0


def test_residual_equals_raw_drop_on_cold_start() -> None:
    """🔴 **핵심 안전장치** — 전량 폴백이면 잔차 = 실제 − 전체평균이라 판정이 종전과 같다."""
    stats = ExpectationStats(combos={}, overall_responses=0, overall_corrects=0)
    events = _events(("ps_1", "infer", True), ("ps_1", "infer", False))
    result = expected_accuracy(events, stats=stats, min_n=_MIN_N, session_overall=0.72)
    assert result.expected is not None
    actual = 0.5
    assert actual - result.expected == actual - 0.72


# ── 조합 통계가 쌓이면 ───────────────────────────────────────────


def test_uses_combo_mean_when_sample_is_enough() -> None:
    """표본 ≥ min_n이면 그 조합의 실측 평균을 기대치로 쓴다."""
    stats = ExpectationStats(
        combos={PassageTypeKey("ps_hard", "infer"): _stat(40, 12)},  # 30%
        overall_responses=1000,
        overall_corrects=700,
    )
    result = expected_accuracy(
        _events(("ps_hard", "infer", False)),
        stats=stats,
        min_n=_MIN_N,
        session_overall=0.7,
    )
    assert result.expected == 0.3
    assert result.combo_events == 1
    assert result.fallback_events == 0


def test_below_min_n_falls_back() -> None:
    """표본 미달이면 전체 평균 — 추정 오차가 잔차에 실리는 것을 막는다(04 §1)."""
    stats = ExpectationStats(
        combos={PassageTypeKey("ps_thin", "infer"): _stat(_MIN_N - 1, 3)},
        overall_responses=1000,
        overall_corrects=700,
    )
    result = expected_accuracy(
        _events(("ps_thin", "infer", False)),
        stats=stats,
        min_n=_MIN_N,
        session_overall=0.7,
    )
    assert result.expected == 0.7
    assert result.fallback_events == 1


def test_null_passage_ref_falls_back() -> None:
    """`passage_ref`가 없으면 조합이 성립하지 않는다 — 폴백."""
    stats = ExpectationStats(
        combos={PassageTypeKey("ps_1", "infer"): _stat(50, 10)},
        overall_responses=1000,
        overall_corrects=700,
    )
    result = expected_accuracy(
        _events((None, "infer", False)), stats=stats, min_n=_MIN_N, session_overall=0.7
    )
    assert result.expected == 0.7


def test_mixed_events_are_weighted_by_count() -> None:
    """조합분과 폴백분이 섞이면 **문항 수 가중 평균**이다."""
    stats = ExpectationStats(
        combos={PassageTypeKey("ps_hard", "infer"): _stat(40, 12)},  # 30%
        overall_responses=1000,
        overall_corrects=700,
    )
    result = expected_accuracy(
        _events(("ps_hard", "infer", False), (None, "infer", True)),
        stats=stats,
        min_n=_MIN_N,
        session_overall=0.7,
    )
    assert result.expected is not None
    assert abs(result.expected - 0.5) < 1e-9  # (0.3 + 0.7) / 2


# ── 🔴 부모 없는 태그 — 양쪽 대칭 제외 ──────────────────────────


def test_untagged_events_excluded_from_both_sides() -> None:
    """🔴 `type_tag`가 없는 이벤트는 **기대·실제 양쪽**에서 빠진다(13 §5-5 ③ 대칭).

    한쪽만 빼면 baseline이 오염돼 전체가 계통적으로 밀린다(B 지적·A 수용).
    """
    stats = ExpectationStats(
        combos={PassageTypeKey("ps_hard", "infer"): _stat(40, 12)},
        overall_responses=1000,
        overall_corrects=700,
    )
    result = expected_accuracy(
        _events(("ps_hard", "infer", False), ("ps_x", None, True)),
        stats=stats,
        min_n=_MIN_N,
        session_overall=0.7,
    )
    assert result.counted_events == 1, "태그 없는 이벤트가 모수에 들었다"
    assert result.expected == 0.3
    assert result.excluded_untagged == 1


def test_all_untagged_yields_no_expectation() -> None:
    """전부 태그가 없으면 판정 자체가 성립하지 않는다 — None."""
    stats = ExpectationStats(combos={}, overall_responses=10, overall_corrects=7)
    result = expected_accuracy(
        _events(("ps_1", None, True)), stats=stats, min_n=_MIN_N, session_overall=0.7
    )
    assert result.expected is None
    assert result.counted_events == 0


# ── 결정론 ───────────────────────────────────────────────────────


def test_deterministic() -> None:
    stats = ExpectationStats(
        combos={PassageTypeKey("ps_1", "infer"): _stat(50, 20)},
        overall_responses=1000,
        overall_corrects=700,
    )
    events = _events(("ps_1", "infer", True), (None, "fact", False))
    first = expected_accuracy(events, stats=stats, min_n=_MIN_N, session_overall=0.7)
    second = expected_accuracy(events, stats=stats, min_n=_MIN_N, session_overall=0.7)
    assert first == second
