"""경보 생애(lifecycle) 판정 — 09 §4 표 (결정론).

소유: 박진희 (detection). 순수 함수 — alert_context 이력을 입력으로 판정한다.

09 §4 표 5행을 그대로 구현한다. 억제(해소 후 2주 이내 + 팔로업 완료)는 응답에서
제외되므로 None을 반환한다 — enum 값이 아니라 signals 배열에서의 부재로 표현.
"""

from __future__ import annotations

from datetime import date

from ai.contracts.detection import AlertContextItem, AlertStatus, Lifecycle, SignalType

#: 해소 후 재발을 follow_up으로 볼 쿨다운 창 — 09 §4 (AI 소유 경보 정책).
LIFECYCLE_COOLDOWN_WEEKS = 2


def resolve_lifecycle(
    signal_type: SignalType,
    alert_context: tuple[AlertContextItem, ...],
    week_start: date,
) -> Lifecycle | None:
    """이 신호의 lifecycle을 판정한다 (09 §4). None이면 응답에서 제외(억제).

    - 같은 유형 open 이력 → ongoing
    - 해소 후 2주 이내 재발 + 팔로업 미발송 → follow_up
    - 해소 후 2주 이내 + 팔로업 발송됨 → None(억제)
    - 해소 후 2주 지남 / 이력 없음 / 다른 유형 → new
    """
    same_type = [item for item in alert_context if item.signal_type is signal_type]

    # open 우선 (09 §4 A판정 7/22): 같은 유형에 open이 하나라도 있으면 ongoing
    if any(item.status is AlertStatus.OPEN for item in same_type):
        return Lifecycle.ONGOING

    # resolved는 최신 건 기준 (09 §4 A판정): 여러 건이면 가장 최근 해소 건으로 판정
    resolved_dated = [
        (item, item.resolved_at)
        for item in same_type
        if item.status is AlertStatus.RESOLVED and item.resolved_at is not None
    ]
    if resolved_dated:
        latest_item, latest_at = max(resolved_dated, key=lambda pair: pair[1])
        # "2주 이내" = 14일 포함(≤ LIFECYCLE_COOLDOWN_WEEKS)
        if _weeks_since(latest_at.date(), week_start) <= LIFECYCLE_COOLDOWN_WEEKS:
            return None if latest_item.followed_up else Lifecycle.FOLLOW_UP
    return Lifecycle.NEW


def has_return_care_history(
    student_ref: str, alert_context: tuple[AlertContextItem, ...]
) -> bool:
    """이 학생에게 return_care 경보 이력이 있는지 — readapt 세그먼트 판정 (04 §2)."""
    return any(
        item.student_ref == student_ref and item.signal_type is SignalType.RETURN_CARE
        for item in alert_context
    )


def _weeks_since(past: date, now: date) -> float:
    return (now - past).days / 7
