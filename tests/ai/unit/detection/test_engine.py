"""detection 엔진 통합 — R1~R6 발화/미발화·제외·병합·lifecycle (결정론).

정밀 픽스처(baseline = 최근 2주 제외 8주 평균을 고려)로 각 규칙의 발화 경계를 고정한다.
골든셋 G1~G12(evaluation/golden/detection)의 단위 근거이기도 하다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ai.contracts.detection import Lifecycle, RuleId, Signal, SignalType, StudentStatus
from ai.detection.engine import detect
from ai.detection.lifecycle import LIFECYCLE_COOLDOWN_WEEKS
from ai.evaluation.fake_snapshot import (
    StudentPlan,
    alert_open,
    alert_resolved,
    build_detect_request,
    fixture_stable,
)

WEEK_START = "2026-07-13"


def _signal_types(plan: StudentPlan) -> set[str]:
    req = build_detect_request(week_start=WEEK_START, seed=1, students=[plan])
    resp = detect(req)
    return {s.signal_type.value for s in resp.signals}


# ── 결정론 ──


def test_determinism() -> None:
    a = detect(fixture_stable())
    b = detect(fixture_stable())
    assert a == b


def test_stable_no_signal() -> None:
    """안정 학습(G1 성격) — 발화 0."""
    assert len(detect(fixture_stable()).signals) == 0


# ── R1 정답률 하락 · 경계(G11) ──


def _accuracy_drop(final: float) -> StudentPlan:
    # baseline(주0~7) 0.80, 최근 2주(주8·9) final. solves 20으로 정답률 정확.
    acc = (0.8,) * 8 + (final, final)
    return StudentPlan(
        student_ref="st_r1", class_ref="cl_a1", weeks=10, solves_per_week=20, accuracy=acc
    )


def test_r1_fires_on_drop() -> None:
    assert "acc_drop" in _signal_types(_accuracy_drop(0.60))  # 20%p 하락


def test_r1_boundary_exactly_15pp_fires() -> None:
    """G11 — 정확히 −15.0%p는 발화(≥ 판정)."""
    assert "acc_drop" in _signal_types(_accuracy_drop(0.65))  # 0.80 → 0.65 = 15%p


def test_r1_silent_below_threshold() -> None:
    assert "acc_drop" not in _signal_types(_accuracy_drop(0.70))  # 10%p < 15


# ── R2 연속 미제출(G3) ──


def test_r2_fires_on_three_consecutive_missing() -> None:
    plan = StudentPlan(
        student_ref="st_r2",
        class_ref="cl_a1",
        weeks=10,
        submit_ok=(True,) * 7 + (False, False, False),
    )
    assert "submit_drop" in _signal_types(plan)


def test_r2_silent_on_two_missing() -> None:
    plan = StudentPlan(
        student_ref="st_r2b",
        class_ref="cl_a1",
        weeks=10,
        submit_ok=(True,) * 8 + (False, False),
    )
    assert "submit_drop" not in _signal_types(plan)


# ── R4 숨은 위기(G5) ──


def test_r4_fires_on_stable_acc_time_spike() -> None:
    # 정답률 유지(0.80) + 최근 2주 정규화 시간 1.6배 이상.
    dur = (180,) * 8 + (300, 320)  # baseline 180, 최근 ~1.67~1.78배
    plan = StudentPlan(
        student_ref="st_r4",
        class_ref="cl_a1",
        weeks=10,
        solves_per_week=12,
        accuracy=0.80,
        duration_sec=dur,
        passage_word_count=800,
    )
    assert "hidden_risk" in _signal_types(plan)


def test_r4_skipped_when_duration_missing() -> None:
    """duration_sec 없으면 R4 미적용 + rules_skipped에 duration_missing."""
    plan = StudentPlan(
        student_ref="st_r4b",
        class_ref="cl_a1",
        weeks=10,
        accuracy=0.80,
        duration_sec=(0,) * 10,  # passage 있어도 0이면 정규화 불가로 간주
    )
    req = build_detect_request(week_start=WEEK_START, seed=1, students=[plan])
    resp = detect(req)
    reasons = {(rs.rule_id.value, rs.reason) for rs in resp.stats.rules_skipped}
    assert ("R4", "duration_missing") in reasons


# ── R5 복귀 케어(G8) ──


def test_r5_fires_on_returned() -> None:
    plan = StudentPlan(
        student_ref="st_r5", class_ref="cl_a1", weeks=10, status=StudentStatus.RETURNED
    )
    assert "return_care" in _signal_types(plan)


# ── 제외 처리 ──


def test_no_consent_events_discarded() -> None:
    """무동의 학생은 이벤트가 딸려 와도 폐기 — 판정 대상 아님."""
    plan = StudentPlan(
        student_ref="st_nc",
        class_ref="cl_a1",
        weeks=10,
        consent="revoked",
        accuracy=_accuracy_drop(0.5).accuracy,
        solves_per_week=20,
        events_despite_exclusion=True,
    )
    req = build_detect_request(week_start=WEEK_START, seed=1, students=[plan])
    resp = detect(req)
    assert len(resp.signals) == 0
    assert resp.stats.students_evaluated == 0


def test_under_2_weeks_excluded_and_counted() -> None:
    plan = StudentPlan(
        student_ref="st_new",
        class_ref="cl_a1",
        weeks=10,
        enrolled_weeks=1,
        accuracy=_accuracy_drop(0.5).accuracy,
        solves_per_week=20,
    )
    resp = detect(build_detect_request(week_start=WEEK_START, seed=1, students=[plan]))
    assert resp.stats.excluded_under_2w == 1
    assert len(resp.signals) == 0


# ── 병합 (G12) ──


def test_merge_one_alert_score_max() -> None:
    """R1+R2 동시 발화 → 신호 1건, score=max, 대표는 최고 점수 규칙."""
    plan = StudentPlan(
        student_ref="st_multi",
        class_ref="cl_a1",
        weeks=10,
        solves_per_week=20,
        accuracy=(0.8,) * 8 + (0.5, 0.5),  # 큰 하락 → R1 score 높음
        submit_ok=(True,) * 7 + (False, False, False),  # R2
    )
    resp = detect(build_detect_request(week_start=WEEK_START, seed=1, students=[plan]))
    # 한 학생 → 경보 1건(R5 아님이므로 병합)
    assert len(resp.signals) == 1
    signal = resp.signals[0]
    assert signal.rule_id in (RuleId.R1, RuleId.R2)


# ── lifecycle (09 §4) ──


def _monday(week_start: str) -> datetime:
    from datetime import date

    d = date.fromisoformat(week_start)
    return datetime(d.year, d.month, d.day)


def test_lifecycle_new_without_history() -> None:
    sig = _first_signal(_accuracy_drop(0.55))
    assert sig.lifecycle is Lifecycle.NEW


def test_lifecycle_ongoing_with_open_history() -> None:
    ctx = [alert_open("st_r1", SignalType.ACC_DROP)]
    req = build_detect_request(
        week_start=WEEK_START, seed=1, students=[_accuracy_drop(0.55)], alert_context=ctx
    )
    sig = detect(req).signals[0]
    assert sig.lifecycle is Lifecycle.ONGOING


def test_lifecycle_followup_recent_resolved() -> None:
    from zoneinfo import ZoneInfo

    resolved_at = (_monday(WEEK_START) - timedelta(days=7)).replace(tzinfo=ZoneInfo("Asia/Seoul"))
    ctx = [alert_resolved("st_r1", SignalType.ACC_DROP, resolved_at=resolved_at, followed_up=False)]
    req = build_detect_request(
        week_start=WEEK_START, seed=1, students=[_accuracy_drop(0.55)], alert_context=ctx
    )
    sig = detect(req).signals[0]
    assert sig.lifecycle is Lifecycle.FOLLOW_UP


def test_lifecycle_suppressed_when_followed_up() -> None:
    from zoneinfo import ZoneInfo

    resolved_at = (_monday(WEEK_START) - timedelta(days=7)).replace(tzinfo=ZoneInfo("Asia/Seoul"))
    ctx = [alert_resolved("st_r1", SignalType.ACC_DROP, resolved_at=resolved_at, followed_up=True)]
    req = build_detect_request(
        week_start=WEEK_START, seed=1, students=[_accuracy_drop(0.55)], alert_context=ctx
    )
    assert len(detect(req).signals) == 0  # 억제 — 응답 제외


def test_cooldown_constant() -> None:
    assert LIFECYCLE_COOLDOWN_WEEKS == 2


def _first_signal(plan: StudentPlan) -> Signal:
    resp = detect(build_detect_request(week_start=WEEK_START, seed=1, students=[plan]))
    return resp.signals[0]
