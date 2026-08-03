"""발동률 목표 임계 — R1 분위 산출 (04 §1 "R1 임계 — 발동률 목표 방식" · 13 §4-1).

**순수 함수다.** 같은 표본 → 같은 임계이며 시계·난수를 쓰지 않는다(불변식 8).

**왜 분위인가.** 적중률은 고정 임계와 **동등**하다(13 §4-1 정정 — 종전 인용값 37.8%는
발동 횟수 불일치로 무효). 바뀌는 것은 **알림 수가 설계값으로 고정된다**는 성질이고, 그게
`09` §1-8 경보 상한 철학(강사의 주의 예산 관리)과 맞는다. 고정 임계는 학생·테넌트마다
발동률이 제각각이다.

**버전 관리의 대상이 값에서 파라미터로 옮겨간다** — 임계는 스냅숏에서 산출되므로
`THRESHOLD_CONFIG`에 저장되지 않고, `target_alert_rate`·`quantile_min_pool`이 버전
관리된다(`cap_max`가 값이 아니라 규칙으로 관리되는 것과 같은 형태). 실제로 쓴 임계·표본
수·산출 경로는 응답 `stats`에 실려 `AI_RUN`으로 추적된다.

> **예약 — 배치 이행.** `04` §4 캘리브레이션 배치가 구현되면 분위 산출을 주기 배치로 옮기고
> `THRESHOLD_CONFIG`(`source=calibrated`)에 저장한다. 지금 요청 시 산출을 택한 이유는
> 배치 인프라가 미구현이라 그렇지 않으면 분위 전환 자체가 돌지 않기 때문이다.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from ai.detection.baseline import Baseline as BaselineLike
from ai.detection.features import WeekFeatures


class ThresholdSource(StrEnum):
    """임계가 어느 경로에서 나왔는지 — `stats.r1_threshold_source`로 나간다."""

    QUANTILE = "quantile"
    FALLBACK = "fallback"
    """풀 표본이 `quantile_min_pool` 미만 — 고정 `drop_pp`를 썼다."""


def resolve_drop_threshold(
    pool: Sequence[float],
    *,
    target_rate: float,
    min_pool: int,
    fallback_pp: float,
) -> tuple[float, ThresholdSource]:
    """하락폭 표본에서 발동률 목표 임계를 구한다. (임계 %p, 산출 경로) 반환.

    `pool`은 **하락폭**(baseline − 주간, %p)이므로 **클수록 나쁘다** — 상위
    `target_rate` 지점이 임계다. 하위 5% 분위라는 말은 "정답률이 하위 5%"라는 뜻이고
    하락폭 축에서는 상위 5%에 대응한다.

    표본이 `min_pool` 미만이면 **고정 폴백**을 쓴다 — 꼬리에 표본이 한둘이면 그 한 명이
    임계를 좌우한다(13 §7-2가 개인 분위를 기각한 것과 같은 이유).

    보간은 하지 않는다 — 정렬 후 인덱스를 취하는 방식이라 부동소수 연산 순서에 의존하지
    않고 바이트 동일 재현이 보장된다.
    """
    if len(pool) < min_pool or not pool:
        return fallback_pp, ThresholdSource.FALLBACK
    ordered = sorted(pool, reverse=True)  # 하락폭 큰 순
    index = min(int(len(ordered) * target_rate), len(ordered) - 1)
    return ordered[index], ThresholdSource.QUANTILE


def accuracy_drop_series(
    weeks: Sequence[WeekFeatures], baseline_accuracy: float | None
) -> list[float]:
    """**원 정답률** 하락폭(%p) 목록 — 기대치 층이 꺼져 있을 때의 시리즈."""
    if baseline_accuracy is None:
        return []
    return [
        (baseline_accuracy - week.accuracy) * 100
        for week in weeks
        if week.accuracy is not None
    ]


def residual_drop_series(
    weeks: Sequence[WeekFeatures], baseline_residual: float | None
) -> list[float]:
    """**잔차** 하락폭(%p) 목록 — 기대치 층이 켜진 주들만(04 §1 기대치 입력 층).

    잔차 = 실제 − 기대이고, 하락폭 = 기준선 잔차 − 주간 잔차다. **판정식의 모양은
    원 정답률 경로와 같다** — 시리즈만 갈아끼운 것이다.
    """
    if baseline_residual is None:
        return []
    return [
        (baseline_residual - week.residual) * 100
        for week in weeks
        if week.residual is not None
    ]


def drop_series(
    weeks: Sequence[WeekFeatures], baseline: BaselineLike
) -> list[float]:
    """R1 판정 시리즈 — **잔차가 있으면 잔차, 없으면 원 정답률**(04 §1).

    🔴 **여기가 기대치 층의 유일한 스위치다.** 통계가 비면 잔차가 서지 않아 원 정답률
    경로로 떨어지고, 그게 **보정 없음과 동치**라 콜드 스타트 회귀 위험이 0이다.
    """
    residuals = residual_drop_series(weeks, baseline.residual)
    if residuals:
        return residuals
    return accuracy_drop_series(weeks, baseline.accuracy)


__all__ = [
    "ThresholdSource",
    "accuracy_drop_series",
    "drop_series",
    "residual_drop_series",
    "resolve_drop_threshold",
]
