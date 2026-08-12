"""**학습 이벤트가 0건인 분석 주**에서도 부재형 신호가 서는가 (99 #43 · #44).

🔴 **`StudentFeatures.weeks`는 학습 이벤트가 있는 주만 만든다.** 그래서 부재형 규칙이 그
목록을 순회하면 **완전 공백 주가 통째로 빠진다** — *"가장 심한 공백"* 이 판정 창에서
사라지는 것이다. R2의 완전 미제출 주, R3의 활동 0건, R5의 복귀 후 활동 0건이 전부 같은
구조로 묻혔다.

⚠ **전역 `StudentFeatures`에 빈 주를 억지로 넣지 않았다** — 그러면 R1·R4·R6의 평가 창까지
바뀌어 이번 작업과 무관한 골든이 흔들린다. 부재형 셋의 **시간축만** 떼어냈다.

🔴 **resolver 단위가 아니라 `detect(request)` 종단으로 잰다** — 그 사이 어디서 끊겨도 잡는다.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Final

import pytest

from ai.contracts.detection import DetectRequest, DetectResponse, RuleId
from ai.detection.engine import detect
from ai.detection.evidence import SKIP_AUTHORITATIVE_EVIDENCE_MISSING

_WEEK: Final = "2026-08-10"
_MONDAY: Final = date.fromisoformat(_WEEK)
_CLASS: Final = "cl_a1"
_STUDENT: Final = "st_quiet"


def _week(back: int) -> str:
    return (_MONDAY - timedelta(weeks=back)).isoformat()


def _request(
    *, evidence: list[dict[str, Any]], status: str = "enrolled"
) -> DetectRequest:
    """🔴 **`learning_events`가 비어 있다** — 이 파일의 전제다."""
    return DetectRequest.model_validate(
        {
            "snapshot_meta": {
                "week_start": _WEEK,
                "snapshot_hash": "sha256:zero",
                "term_context": "normal",
                "classes": [{"class_ref": _CLASS}],
            },
            "students": [
                {
                    "student_ref": _STUDENT,
                    "class_ref": _CLASS,
                    "enrolled_weeks": 20,
                    "status": status,
                    "consent": "granted",
                }
            ],
            "learning_events": [],
            "alert_context": [],
            "detection_evidence": evidence,
        }
    )


def _window(back: int, *, expected: int, submitted: int) -> dict[str, Any]:
    stamp = _week(back)
    return {
        "kind": "assignment_window",
        "source_table": "assignment_week_summary",
        "record_id": f"aws_{stamp}_{_STUDENT}",
        "student_ref": _STUDENT,
        "week_start": stamp,
        "expected_count": expected,
        "submitted_count": submitted,
    }


def _activity(back: int, *, count: int) -> dict[str, Any]:
    stamp = _week(back)
    return {
        "kind": "weekly_activity",
        "source_table": "student_week_activity",
        "record_id": f"swa_{stamp}_{_STUDENT}",
        "student_ref": _STUDENT,
        "week_start": stamp,
        "activity_count": count,
    }


def _transition(back: int = 0) -> dict[str, Any]:
    stamp = _week(back)
    return {
        "kind": "enrollment_transition",
        "source_table": "student_status_history",
        "record_id": f"ssh_{_STUDENT}_{stamp}",
        "student_ref": _STUDENT,
        "occurred_at": f"{stamp}T09:00:00+09:00",
        "from_status": "paused",
        "to_status": "returned",
    }


def _signals(response: DetectResponse, rule: RuleId) -> tuple[Any, ...]:
    return tuple(s for s in response.signals if s.rule_id is rule)


def _skipped(response: DetectResponse, rule: RuleId) -> int:
    return sum(
        row.students
        for row in response.stats.rules_skipped
        if row.rule_id is rule and row.reason == SKIP_AUTHORITATIVE_EVIDENCE_MISSING
    )


def _assert_no_learning_events(request: DetectRequest) -> None:
    """🔴 **절단 가드** — 픽스처가 이벤트를 만들면 이 파일은 아무것도 안 보는 것이다."""
    assert not request.learning_events, (
        "분석 주에 학습 이벤트가 있다 — 「이벤트 0건」을 재고 있지 않다"
    )
    assert request.detection_evidence, "정본 근거가 비었다 — 판정할 입력이 없다"


# ───────────────────────── R2 완전 미제출 ─────────────────────────


def test_r2_fires_with_zero_learning_events() -> None:
    """🔴 세 주 연속 `expected>0 · submitted=0` — **학습 이벤트가 하나도 없어도** 발화한다."""
    request = _request(
        evidence=[_window(back, expected=3, submitted=0) for back in range(3)]
    )
    _assert_no_learning_events(request)
    response = detect(request)

    signals = _signals(response, RuleId.R2)
    assert signals, "이벤트 0건이라 R2가 묻혔다 — features.weeks를 보고 있다"
    tables = [item.source_table for item in signals[0].evidence]
    assert tables == ["assignment_week_summary"] * 3, tables
    assert "learning_event" not in tables


def test_r2_still_requires_the_streak_length() -> None:
    """두 주만 미제출이면 발화하지 않는다 — 임계는 그대로다."""
    request = _request(
        evidence=[_window(back, expected=3, submitted=0) for back in range(2)]
    )
    _assert_no_learning_events(request)
    assert not _signals(detect(request), RuleId.R2)


# ───────────────────────── R3 완전 공백 ─────────────────────────


def _full_activity_baseline() -> list[dict[str, Any]]:
    """직전 8주는 충분한 활동, 분석 주는 **0건**."""
    return [_activity(0, count=0)] + [
        _activity(back, count=12) for back in range(1, 9)
    ]


def test_r3_fires_when_the_analysis_week_is_completely_empty() -> None:
    """🔴 **가장 심한 공백**(활동 0건)이 판정 창에 들어온다."""
    request = _request(evidence=_full_activity_baseline())
    _assert_no_learning_events(request)
    response = detect(request)

    signals = _signals(response, RuleId.R3)
    assert signals, "활동 0건인데 R3가 발화하지 않았다 — 분석 주가 판정 창에 없다"
    assert _skipped(response, RuleId.R3) == 0, "근거가 있는데 skip으로 샜다"
    assert [
        (item.source_table, item.record_id, item.summary) for item in signals[0].evidence
    ] == [
        (
            "student_week_activity",
            f"swa_{_week(0)}_{_STUDENT}",
            "해당 주 학습 활동 0건",
        )
    ]


def test_r3_skips_when_one_baseline_week_is_missing() -> None:
    """🔴 **rolling 창이 완전해야 한다** — 한 주만 빠져도 섞느니 판정하지 않는다."""
    rows = _full_activity_baseline()
    del rows[4]  # baseline 한 주 제거
    request = _request(evidence=rows)
    _assert_no_learning_events(request)
    response = detect(request)
    assert not _signals(response, RuleId.R3)
    assert _skipped(response, RuleId.R3) == 1


def test_r3_skips_when_the_analysis_week_aggregate_is_missing() -> None:
    """측정 부재는 **0이 아니다.**"""
    rows = [_activity(back, count=12) for back in range(1, 9)]
    request = _request(evidence=rows)
    _assert_no_learning_events(request)
    response = detect(request)
    assert not _signals(response, RuleId.R3)
    assert _skipped(response, RuleId.R3) == 1


# ───────────────────────── R5 복귀 후 활동 0건 ─────────────────────────


def test_r5_fires_after_a_return_with_no_activity() -> None:
    """🔴 복귀 첫 주에 활동이 0건인 것은 **드문 일이 아니고** 오히려 케어 대상이다."""
    request = _request(evidence=[_transition()], status="returned")
    _assert_no_learning_events(request)
    response = detect(request)

    signals = _signals(response, RuleId.R5)
    assert signals, "복귀 후 활동 0건이라 R5가 묻혔다"
    assert [
        (item.source_table, item.summary) for item in signals[0].evidence
    ] == [("student_status_history", "휴원 후 복귀 상태 전환 기록")]


def test_r5_ignores_a_transition_from_another_week_even_with_no_events() -> None:
    request = _request(evidence=[_transition(back=2)], status="returned")
    _assert_no_learning_events(request)
    response = detect(request)
    assert not _signals(response, RuleId.R5)
    assert _skipped(response, RuleId.R5) == 1


# ───────────────────────── 시간축 분리의 절단 가드 ─────────────────────────


def test_the_student_has_no_feature_weeks_at_all() -> None:
    """🔴 **전제 확인** — 이벤트가 0건이면 `StudentFeatures.weeks`가 비어 있다.

    ⚠ 그 사실이 깨지면(빈 주를 만들게 되면) 위 검사들은 **다른 것을 재는 것**이 된다 —
    R1·R4·R6의 평가 창이 함께 바뀌었다는 뜻이므로 그때 이 파일을 다시 읽어야 한다.
    """
    from ai.detection.features import extract_features  # noqa: PLC0415

    request = _request(evidence=[_transition()], status="returned")
    assert extract_features(request)[_STUDENT].weeks == (), (
        "이벤트 0건인데 주차 피처가 생겼다 — 판정 창 구조가 바뀌었다(99 #44)"
    )


@pytest.mark.parametrize("rule", [RuleId.R1, RuleId.R4, RuleId.R6])
def test_the_event_based_rules_are_untouched(rule: RuleId) -> None:
    """R1·R4·R6는 학습 기록이 근거라 **이벤트 0건이면 판정 대상이 아니다**(현행 유지)."""
    request = _request(evidence=[_activity(0, count=0)])
    response = detect(request)
    assert not _signals(response, rule)
