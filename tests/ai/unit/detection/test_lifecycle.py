"""lifecycle 판정 회귀 — 09 §4 A판정(7/22): 14일 경계·이력 다건·격리 고정.

- 2주 경계 = 14일 포함(≤ 14일 → follow_up, 15일 → new)
- 이력 다건: open 우선, resolved는 최신 건 기준
- 다른 학생·다른 유형 이력은 격리(영향 없음)
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ai.contracts.detection import AlertContextItem, AlertStatus, Lifecycle, SignalType
from ai.detection.lifecycle import resolve_lifecycle

_KST = ZoneInfo("Asia/Seoul")
WEEK = date(2026, 7, 13)


def _resolved(
    days_ago: int, followed_up: bool, signal: SignalType = SignalType.ACC_DROP
) -> AlertContextItem:
    at = datetime(WEEK.year, WEEK.month, WEEK.day, tzinfo=_KST) - timedelta(days=days_ago)
    return AlertContextItem(
        student_ref="st_1",
        signal_type=signal,
        status=AlertStatus.RESOLVED,
        resolved_at=at,
        followed_up=followed_up,
    )


def _open(signal: SignalType = SignalType.ACC_DROP) -> AlertContextItem:
    return AlertContextItem(
        student_ref="st_1",
        signal_type=signal,
        status=AlertStatus.OPEN,
        resolved_at=None,
        followed_up=False,
    )


# ── 14일 경계 ──


def test_exactly_14_days_is_followup() -> None:
    """정확히 14일 = "2주 이내" 포함 → follow_up."""
    ctx = (_resolved(14, followed_up=False),)
    assert resolve_lifecycle(SignalType.ACC_DROP, ctx, WEEK) is Lifecycle.FOLLOW_UP


def test_15_days_is_new() -> None:
    """14일 초과 → new (쿨다운 지남)."""
    ctx = (_resolved(15, followed_up=False),)
    assert resolve_lifecycle(SignalType.ACC_DROP, ctx, WEEK) is Lifecycle.NEW


def test_14_days_followed_up_is_suppressed() -> None:
    """2주 이내 + 팔로업 완료 → 억제(None)."""
    assert resolve_lifecycle(SignalType.ACC_DROP, (_resolved(7, followed_up=True),), WEEK) is None


# ── 이력 다건 ──


def test_open_wins_over_resolved() -> None:
    """같은 유형에 open이 있으면 resolved 여럿이 있어도 ongoing."""
    ctx = (_open(), _resolved(7, followed_up=True))
    assert resolve_lifecycle(SignalType.ACC_DROP, ctx, WEEK) is Lifecycle.ONGOING


def test_latest_resolved_governs_followup() -> None:
    """resolved 여러 건이면 최신 건 기준 — 최신이 미팔로업이면 follow_up."""
    # 최신=5일 전(미팔로업), 오래된=20일 전(팔로업)
    ctx = (_resolved(20, followed_up=True), _resolved(5, followed_up=False))
    assert resolve_lifecycle(SignalType.ACC_DROP, ctx, WEEK) is Lifecycle.FOLLOW_UP


def test_latest_resolved_followed_up_suppresses() -> None:
    """최신 resolved가 팔로업 완료면 억제 — 오래된 미팔로업 건이 있어도."""
    ctx = (_resolved(5, followed_up=True), _resolved(20, followed_up=False))  # 최신=5일 전, 팔로업
    assert resolve_lifecycle(SignalType.ACC_DROP, ctx, WEEK) is None


# ── 격리 ──


def test_other_signal_type_isolated() -> None:
    """다른 유형 이력은 이 신호에 영향 없음 → new."""
    ctx = (_open(signal=SignalType.HIDDEN_RISK),)  # 다른 유형 open
    assert resolve_lifecycle(SignalType.ACC_DROP, ctx, WEEK) is Lifecycle.NEW


def test_empty_context_is_new() -> None:
    assert resolve_lifecycle(SignalType.ACC_DROP, (), WEEK) is Lifecycle.NEW
