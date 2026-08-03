"""개인 베이스라인 — 이동 8주 창 (반 평균 아님, 04 §1 공통 전제).

소유: 박진희 (detection). 순수 함수 — 피처를 받아 기준선을 반환한다.

베이스라인은 **판정 대상 주(최근 ASSESSMENT_WEEKS)를 제외한** 직전 window주의 평균이다.
R1·R4는 최근 2주를 연속 판정하므로 그 2주가 자기 기준선에 섞이지 않게 제외한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from ai.detection.features import StudentFeatures, WeekFeatures

#: 판정 대상 주 수 — 최근 이만큼(최대 consecutive_weeks)은 베이스라인에서 뺀다.
ASSESSMENT_WEEKS = 2


@dataclass(frozen=True)
class Baseline:
    """개인 기준선 — None은 재료가 없어 산출 불가(해당 규칙 미적용 신호)."""

    weeks_used: int
    accuracy: float | None
    norm_time: float | None
    volume: float | None
    """평균 주간 이벤트 수."""

    residual: float | None = None
    """잔차 기준선 — 기대치 층이 켜진 주들의 (실제 − 기대) 평균(04 §1).

    None이면 기대치 층 미적용이라 R1이 원 정답률 하락폭으로 판정한다.
    """


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def prior_weeks(features: StudentFeatures, window: int) -> tuple[WeekFeatures, ...]:
    """베이스라인 산출에 쓰는 직전 window주 (판정 대상 주 제외, 과거→최근)."""
    if len(features.weeks) <= ASSESSMENT_WEEKS:
        return ()
    return features.weeks[:-ASSESSMENT_WEEKS][-window:]


def compute_baseline(features: StudentFeatures, window: int) -> Baseline:
    """직전 window주 평균으로 기준선을 만든다."""
    prior = prior_weeks(features, window)
    residuals = [w.residual for w in prior if w.residual is not None]
    return Baseline(
        residual=_mean(residuals),
        weeks_used=len(prior),
        accuracy=_mean([w.accuracy for w in prior if w.accuracy is not None]),
        norm_time=_mean([w.norm_time for w in prior if w.norm_time is not None]),
        volume=_mean([float(w.event_count) for w in prior]),
    )
