"""기대치 층 ↔ R1 배선 — 잔차가 판정 시리즈가 된다 (04 §1 기대치 입력 층).

🔴 **핵심 안전장치 두 개**
① **콜드 스타트 동일성** — 기대치가 없으면 R1 판정이 종전(원 정답률 하락폭)과 **같다**.
② **어려운 지문 주는 잔차로 흡수** — 기대가 함께 낮으면 실제가 떨어져도 발화하지 않는다.

전 경로 결정론이다.
"""

from __future__ import annotations

from dataclasses import replace

from ai.detection.baseline import compute_baseline
from ai.detection.features import StudentFeatures, extract_features
from ai.detection.quantile import drop_series
from ai.detection.rules import evaluate_student
from ai.detection.segments import Segment
from ai.detection.thresholds import default_threshold_config
from ai.evaluation.fake_snapshot import StudentPlan, build_detect_request

WEEK_START = "2026-07-13"
_CONFIG = default_threshold_config()


def _features(accuracies: tuple[float, ...]) -> StudentFeatures:
    plan = StudentPlan(
        student_ref="st_1",
        class_ref="cl_a1",
        weeks=len(accuracies),
        solves_per_week=20,
        accuracy=accuracies,
    )
    request = build_detect_request(week_start=WEEK_START, seed=1, students=[plan])
    return extract_features(request)["st_1"]


def _with_expectation(
    features: StudentFeatures, expected: tuple[float | None, ...]
) -> StudentFeatures:
    weeks = tuple(
        replace(week, expected_accuracy=exp)
        for week, exp in zip(features.weeks, expected, strict=True)
    )
    return replace(features, weeks=weeks)


def _fires_r1(features: StudentFeatures) -> bool:
    baseline = compute_baseline(features, _CONFIG.baseline_window_weeks)
    findings, _ = evaluate_student(features, baseline, _CONFIG, Segment.NORMAL, 15.0)
    return any(f.rule_id.value == "R1" for f in findings)


# ── ① 콜드 스타트 동일성 ────────────────────────────────────────


def test_cold_start_matches_legacy_judgement() -> None:
    """🔴 기대치가 없으면 판정이 종전과 **같다** — 회귀 위험 0(설계 의도)."""
    for final in (0.60, 0.65, 0.70):
        plain = _features((0.8,) * 8 + (final, final))
        with_none = _with_expectation(plain, (None,) * 10)
        assert _fires_r1(plain) == _fires_r1(with_none), final


def test_drop_series_falls_back_to_accuracy_without_expectation() -> None:
    """분위 풀도 같은 스위치를 탄다 — 잔차가 없으면 원 정답률 시리즈."""
    features = _features((0.8,) * 8 + (0.60, 0.60))
    baseline = compute_baseline(features, _CONFIG.baseline_window_weeks)
    assert baseline.residual is None
    series = drop_series(features.weeks, baseline)
    assert series, "원 정답률 시리즈가 나와야 한다"


# ── ② 어려운 지문 주는 잔차로 흡수된다 ─────────────────────────


def test_hard_passage_week_is_absorbed_by_residual() -> None:
    """🔴 실제가 20%p 떨어져도 **기대도 같이 떨어졌으면** 발화하지 않는다.

    "어려운 지문이 걸린 주"와 "진짜 무너진 것"의 구분 — 이 층의 존재 이유다.
    """
    features = _features((0.8,) * 8 + (0.60, 0.60))
    assert _fires_r1(features), "전제: 기대치 없이는 발화한다"

    # 기준선 주는 기대 0.80(잔차 0), 판정 주는 기대 0.60(잔차 0) — 난이도 탓이다.
    absorbed = _with_expectation(features, (0.80,) * 8 + (0.60, 0.60))
    assert not _fires_r1(absorbed)


def test_real_collapse_still_fires_with_expectation() -> None:
    """역케이스 — 기대는 그대로인데 실제만 떨어지면 **잔차로도 발화**한다."""
    features = _features((0.8,) * 8 + (0.60, 0.60))
    real = _with_expectation(features, (0.80,) * 10)  # 기대는 평탄
    assert _fires_r1(real)


def test_partial_expectation_uses_accuracy_path() -> None:
    """판정 창에 기대치가 **일부만** 있으면 원 정답률 경로다 — 주마다 섞지 않는다."""
    features = _features((0.8,) * 8 + (0.60, 0.60))
    mixed = _with_expectation(features, (0.80,) * 8 + (0.60, None))
    assert _fires_r1(mixed), "혼재 시 원 정답률 판정(= 발화)이어야 한다"


# ── 결정론 ───────────────────────────────────────────────────────


def test_residual_path_is_deterministic() -> None:
    features = _with_expectation(
        _features((0.8,) * 8 + (0.60, 0.60)), (0.80,) * 8 + (0.60, 0.60)
    )
    assert _fires_r1(features) == _fires_r1(features)
