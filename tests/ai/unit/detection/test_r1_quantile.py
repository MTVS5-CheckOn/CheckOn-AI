"""R1 발동률 목표 임계 — 분위 전환 (04 §1 R1 재정의 · 13 §4-1).

**근거는 적중률이 아니라 발동률 제어다.** 분위 임계는 적중률에서 고정 임계와 동등하고
(13 §4-1 정정 — 종전 37.8%는 발동 횟수 불일치로 무효), 바뀌는 것은 **알림 수가 설계값으로
고정된다**는 성질이다(09 §1-8 경보 상한 철학).

풀 = **베이스라인 창(8주) × 전 학생**의 주간 하락폭 — 13 §4-1이 검증한 학생×주와 같은 모양.
표본이 `quantile_min_pool` 미만이면 고정 `drop_pp`로 폴백한다.
"""

from __future__ import annotations

import pytest

from ai.contracts.detection import DetectResponse
from ai.detection.engine import detect
from ai.detection.quantile import ThresholdSource, resolve_drop_threshold
from ai.detection.thresholds import ThresholdConfig, default_threshold_config
from ai.evaluation.fake_snapshot import StudentPlan, build_detect_request

WEEK_START = "2026-07-13"


# ── 순수 함수 — 같은 입력 = 같은 임계 ─────────────────────────────


def test_quantile_threshold_is_deterministic() -> None:
    pool = [float(i) for i in range(200)]
    first = resolve_drop_threshold(pool, target_rate=0.05, min_pool=100, fallback_pp=15.0)
    second = resolve_drop_threshold(pool, target_rate=0.05, min_pool=100, fallback_pp=15.0)
    assert first == second


def test_quantile_picks_upper_tail_of_drops() -> None:
    """하락폭은 **클수록 나쁘다** — 상위 target_rate 지점이 임계가 된다."""
    pool = [float(i) for i in range(100)]  # 0..99
    threshold, source = resolve_drop_threshold(
        pool, target_rate=0.05, min_pool=100, fallback_pp=15.0
    )
    assert source is ThresholdSource.QUANTILE
    assert 93.0 <= threshold <= 96.0, threshold


def test_falls_back_when_pool_is_small() -> None:
    """🔴 표본 부족 시 고정 15%p 폴백 — 한 명이 임계를 좌우하지 않게."""
    threshold, source = resolve_drop_threshold(
        [1.0, 2.0, 3.0], target_rate=0.05, min_pool=100, fallback_pp=15.0
    )
    assert source is ThresholdSource.FALLBACK
    assert threshold == 15.0


def test_empty_pool_falls_back() -> None:
    threshold, source = resolve_drop_threshold(
        [], target_rate=0.05, min_pool=100, fallback_pp=15.0
    )
    assert (threshold, source) == (15.0, ThresholdSource.FALLBACK)


@pytest.mark.parametrize("rate", [0.03, 0.05, 0.10])
def test_alert_rate_matches_target(rate: float) -> None:
    """🔴 발동률이 target에 맞는다 — 이것이 분위 전환의 검증된 근거다(13 §4-1)."""
    pool = [float(i) for i in range(1000)]
    threshold, _ = resolve_drop_threshold(
        pool, target_rate=rate, min_pool=100, fallback_pp=15.0
    )
    fired = [d for d in pool if d >= threshold]
    assert abs(len(fired) / len(pool) - rate) <= 0.01, (rate, len(fired))


# ── 설정 경유 — 하드코딩 금지 ────────────────────────────────────


def test_params_live_in_threshold_config() -> None:
    """임계를 바꾸려면 설정을 거쳐야 한다(03 §1) — 값이 아니라 파라미터가 버전 관리된다."""
    config = default_threshold_config()
    assert config.r1.target_alert_rate == 0.05
    assert config.r1.quantile_min_pool == 100
    assert config.r1.drop_pp == 15.0  # 폴백 지위로 남는다


def test_config_is_frozen() -> None:
    """config_version 없이 임계를 못 바꾼다 — 설정 객체가 불변이다."""
    config = default_threshold_config()
    with pytest.raises(Exception):  # noqa: B017 — pydantic ValidationError
        config.r1.target_alert_rate = 0.5  # type: ignore[misc]


# ── 엔진 통합 ────────────────────────────────────────────────────


def _drop_plan(ref: str, final: float, weeks: int = 10) -> StudentPlan:
    return StudentPlan(
        student_ref=ref,
        class_ref="cl_a1",
        weeks=weeks,
        solves_per_week=20,
        accuracy=(0.8,) * (weeks - 2) + (final, final),
    )


def _detect(
    plans: list[StudentPlan], config: ThresholdConfig | None = None
) -> DetectResponse:
    request = build_detect_request(week_start=WEEK_START, seed=1, students=plans)
    return detect(request, config) if config else detect(request)


def test_small_tenant_uses_fallback_and_reports_it() -> None:
    """🔴 소규모 테넌트는 폴백 — stats가 어느 경로였는지 밝힌다."""
    response = _detect([_drop_plan("st_1", 0.60), _drop_plan("st_2", 0.78)])
    assert response.stats.r1_threshold_source == "fallback"
    assert response.stats.r1_threshold_pp == 15.0
    assert (response.stats.r1_pool_n or 0) < 100


def test_fallback_keeps_current_behaviour() -> None:
    """폴백 경로에서는 기존 −15%p 거동이 그대로다(회귀 방어)."""
    fires = _detect([_drop_plan("st_1", 0.65)])  # 정확히 15%p
    silent = _detect([_drop_plan("st_2", 0.70)])  # 10%p
    assert any(s.rule_id.value == "R1" for s in fires.signals)
    assert not any(s.rule_id.value == "R1" for s in silent.signals)


def test_large_tenant_uses_quantile() -> None:
    """🔴 표본이 충분하면 분위 경로 — 8주 × 전 학생이 풀이다."""
    plans = [_drop_plan(f"st_{i:03d}", 0.80 - i * 0.001) for i in range(20)]
    response = _detect(plans)
    assert (response.stats.r1_pool_n or 0) >= 100, response.stats.r1_pool_n
    assert response.stats.r1_threshold_source == "quantile"


def test_stats_threshold_is_the_one_actually_used() -> None:
    """불변식 8 — 기록이 실제 사용분이다. 임계 이상 하락한 학생만 R1이 뜬다."""
    plans = [_drop_plan(f"st_{i:03d}", 0.80 - i * 0.01) for i in range(20)]
    response = _detect(plans)
    used = response.stats.r1_threshold_pp
    assert used is not None
    fired = {s.student_ref for s in response.signals if s.rule_id.value == "R1"}
    for plan in plans:
        drop_pp = (0.8 - plan.accuracy[-1]) * 100  # type: ignore[index]
        if drop_pp >= used + 1e-9:
            assert plan.student_ref in fired, (plan.student_ref, drop_pp, used)


def test_determinism_with_quantile() -> None:
    """같은 스냅숏 = 같은 임계 = 같은 응답(불변식 8)."""
    plans = [_drop_plan(f"st_{i:03d}", 0.80 - i * 0.001) for i in range(20)]
    assert _detect(plans) == _detect(plans)


# ── 기존 거동 불변 ───────────────────────────────────────────────


def test_new_student_still_excluded() -> None:
    """재원 2주 미만은 분위와 무관하게 제외된다."""
    plans = [_drop_plan(f"st_{i:03d}", 0.50) for i in range(20)]
    plans.append(
        StudentPlan(
            student_ref="st_new",
            class_ref="cl_a1",
            weeks=10,
            enrolled_weeks=1,
            solves_per_week=20,
            accuracy=(0.8,) * 8 + (0.30, 0.30),
        )
    )
    response = _detect(plans)
    assert "st_new" not in {s.student_ref for s in response.signals}
    assert response.stats.excluded_under_2w >= 1
