"""R2·R3·R5가 **정본 근거로 판정하는가** (99 #43).

🔴 **판정에 쓴 숫자와 응답 문면의 숫자가 같아야 한다** — 갈리면 BE가 원본을 열었을 때
안 맞고, 그건 evidence가 있는데도 근거가 없는 것과 같다.
"""

from __future__ import annotations

from typing import Any, Final

import pytest

from ai.contracts.detection import DetectRequest, DetectResponse, RuleId
from ai.detection.engine import detect
from ai.detection.evidence import SKIP_AUTHORITATIVE_EVIDENCE_MISSING
from ai.evaluation.fake_snapshot import StudentPlan, build_detect_request

_WEEK: Final = "2026-08-10"
_CLASS: Final = "cl_a1"


def _response(plan: StudentPlan) -> DetectResponse:
    return detect(build_detect_request(week_start=_WEEK, seed=7, students=[plan]))


def _rule_signals(response: DetectResponse, rule: RuleId) -> tuple[Any, ...]:
    return tuple(s for s in response.signals if s.rule_id is rule)


def _skipped(response: DetectResponse, rule: RuleId) -> int:
    return sum(
        row.students
        for row in response.stats.rules_skipped
        if row.rule_id is rule and row.reason == SKIP_AUTHORITATIVE_EVIDENCE_MISSING
    )


def _drop_evidence(request: DetectRequest) -> DetectRequest:
    """정본 근거만 뺀 요청 — **기존 형태의 요청**이 그대로 오는 경로다."""
    return request.model_copy(update={"detection_evidence": ()})


# ───────────────────────── R2 ─────────────────────────


def test_r2_is_silent_when_no_assignment_was_due() -> None:
    """🔴 `expected_count == 0`은 **미제출이 아니다** — 과제가 없던 주다."""
    plan = StudentPlan(
        student_ref="st_none_due",
        class_ref=_CLASS,
        weeks=10,
        submit_ok=(True,) * 7 + (False, False, False),
        assignment_expected=0,
    )
    assert not _rule_signals(_response(plan), RuleId.R2), (
        "예정 과제가 0인데 미제출로 발화했다 — 방학·휴강이 위험신호가 된다"
    )


def test_r2_fires_on_three_weeks_with_expected_but_no_submission() -> None:
    plan = StudentPlan(
        student_ref="st_missing",
        class_ref=_CLASS,
        weeks=10,
        submit_ok=(True,) * 7 + (False, False, False),
        assignment_expected=3,
    )
    signals = _rule_signals(_response(plan), RuleId.R2)
    assert signals, "예정 3·제출 0이 3주 연속인데 발화하지 않았다"
    assert {item.source_table for item in signals[0].evidence} == {
        "assignment_week_summary"
    }, "과제 집계 외의 기록을 인용했다"


def test_r2_stops_the_streak_on_a_partial_submission() -> None:
    """일부 제출은 **연속을 끊는다** — 제출률 하락 경로는 아직 열지 않았다."""
    plan = StudentPlan(
        student_ref="st_partial",
        class_ref=_CLASS,
        weeks=10,
        submit_ok=(True,) * 7 + (False, True, False),
        assignment_expected=3,
    )
    assert not _rule_signals(_response(plan), RuleId.R2)


def test_r2_skips_without_the_aggregate_even_if_solves_exist() -> None:
    """🔴 집계가 없으면 **발화하지 않고 skip**한다 — `solve`로 대신하지 않는다."""
    plan = StudentPlan(
        student_ref="st_no_agg",
        class_ref=_CLASS,
        weeks=10,
        submit_ok=(True,) * 7 + (False, False, False),
        emit_detection_evidence=False,
    )
    response = detect(
        _drop_evidence(build_detect_request(week_start=_WEEK, seed=7, students=[plan]))
    )
    assert not _rule_signals(response, RuleId.R2)
    assert _skipped(response, RuleId.R2) == 1


def test_r2_summary_uses_the_input_numbers() -> None:
    """🔴 문면 숫자 = 입력 숫자. 분모 없이 제출률 하락을 말하지 않는다."""
    plan = StudentPlan(
        student_ref="st_summary",
        class_ref=_CLASS,
        weeks=10,
        submit_ok=(True,) * 7 + (False, False, False),
        assignment_expected=4,
    )
    signals = _rule_signals(_response(plan), RuleId.R2)
    assert signals
    for item in signals[0].evidence:
        assert item.summary == "예정 과제 4건 중 제출 0건"
    assert not any("제출률" in item.summary for item in signals[0].evidence)


# ───────────────────────── R3 ─────────────────────────


def test_r3_fires_from_the_weekly_aggregate_and_cites_it() -> None:
    plan = StudentPlan(
        student_ref="st_gap",
        class_ref=_CLASS,
        weeks=10,
        solves_per_week=(10,) * 9 + (1,),
    )
    signals = _rule_signals(_response(plan), RuleId.R3)
    assert signals, "학습량이 급감했는데 R3가 발화하지 않았다"
    evidence = signals[0].evidence
    assert {item.source_table for item in evidence} == {"student_week_activity"}
    #: 🔴 **판정에 쓴 count와 문면 숫자가 같다.**
    assert len(evidence) == 1
    assert evidence[0].summary.startswith("해당 주 학습 활동 ")
    assert evidence[0].summary.endswith("건")


def test_r3_skips_when_the_aggregate_is_missing() -> None:
    """🔴 **집계 부재를 0으로 간주하지 않는다** — 「기록이 없다」는 증명할 수 없다."""
    plan = StudentPlan(
        student_ref="st_gap_noagg",
        class_ref=_CLASS,
        weeks=10,
        solves_per_week=(10,) * 9 + (1,),
        emit_detection_evidence=False,
    )
    response = detect(
        _drop_evidence(build_detect_request(week_start=_WEEK, seed=7, students=[plan]))
    )
    assert not _rule_signals(response, RuleId.R3)
    assert _skipped(response, RuleId.R3) == 1


def test_a_zero_count_aggregate_is_still_evidence() -> None:
    """🔴 **0건도 실존 레코드다** — 그래서 근거가 만들어진다.

    ⚠ **엔진 왕복으로 못 잰다**(실측): 그 주에 학습 이벤트가 하나도 없으면
    `extract_features`가 **그 주 자체를 만들지 않아** R3가 보는 「최근 주」가 한 주 앞으로
    밀린다 — 즉 **완전 공백 주는 판정 창에 안 들어온다.** 그건 이 안건과 다른 축이라
    별건으로 등재했고(99 #44), 여기서는 **resolver가 0건 집계로 근거를 만드는지**만 본다.
    """
    from datetime import date  # noqa: PLC0415

    from ai.contracts.detection import (  # noqa: PLC0415
        EvidenceKind,
        WeeklyActivityEvidence,
    )
    from ai.detection.evidence import (  # noqa: PLC0415
        EvidenceRequest,
        StudentEvidence,
        resolve_r3_evidence,
    )

    monday = date(2026, 8, 10)
    zero = WeeklyActivityEvidence(
        kind=EvidenceKind.WEEKLY_ACTIVITY,
        source_table="student_week_activity",
        record_id="swa_zero",
        student_ref="st_zero",
        week_start=monday,
        activity_count=0,
    )
    items = resolve_r3_evidence(
        EvidenceRequest(
            student_ref="st_zero",
            label="volume_gap",
            evidence_weeks=(monday,),
            learning_events={},
            student_evidence=StudentEvidence(weekly_activity={monday: zero}),
        )
    )
    assert [(i.source_table, i.record_id, i.summary) for i in items] == [
        ("student_week_activity", "swa_zero", "해당 주 학습 활동 0건")
    ]


# ───────────────────────── R5 ─────────────────────────


def test_r5_fires_with_a_matching_transition() -> None:
    from ai.contracts.detection import StudentStatus  # noqa: PLC0415

    plan = StudentPlan(
        student_ref="st_back", class_ref=_CLASS, weeks=10, status=StudentStatus.RETURNED
    )
    signals = _rule_signals(_response(plan), RuleId.R5)
    assert signals, "복귀 전환 이력이 있는데 R5가 발화하지 않았다"
    assert [
        (item.source_table, item.summary) for item in signals[0].evidence
    ] == [("student_status_history", "휴원 후 복귀 상태 전환 기록")]


def test_r5_skips_when_only_the_status_says_returned() -> None:
    """🔴 상태 필드는 **현재 값**이라 전환 시점을 말하지 않는다."""
    from ai.contracts.detection import StudentStatus  # noqa: PLC0415

    plan = StudentPlan(
        student_ref="st_back_noh",
        class_ref=_CLASS,
        weeks=10,
        status=StudentStatus.RETURNED,
        emit_detection_evidence=False,
    )
    response = detect(
        _drop_evidence(build_detect_request(week_start=_WEEK, seed=7, students=[plan]))
    )
    assert not _rule_signals(response, RuleId.R5)
    assert _skipped(response, RuleId.R5) == 1


def test_r5_ignores_a_transition_from_another_week() -> None:
    """다른 주의 복귀는 **이번 주** 신호가 아니다."""
    from ai.contracts.detection import StudentStatus  # noqa: PLC0415

    plan = StudentPlan(
        student_ref="st_back_old",
        class_ref=_CLASS,
        weeks=10,
        status=StudentStatus.RETURNED,
    )
    request = build_detect_request(week_start=_WEEK, seed=7, students=[plan])
    from datetime import timedelta  # noqa: PLC0415

    from ai.contracts.detection import (  # noqa: PLC0415
        EnrollmentTransitionEvidence,
    )

    shifted = tuple(
        item.model_copy(update={"occurred_at": item.occurred_at - timedelta(days=7)})
        if isinstance(item, EnrollmentTransitionEvidence)
        else item
        for item in request.detection_evidence
    )
    response = detect(request.model_copy(update={"detection_evidence": shifted}))
    assert not _rule_signals(response, RuleId.R5)
    assert _skipped(response, RuleId.R5) == 1


def test_r5_does_not_use_another_students_transition() -> None:
    """🔴 다른 학생의 전환 이력을 대신 쓰지 않는다."""
    from ai.contracts.detection import StudentStatus  # noqa: PLC0415

    back = StudentPlan(
        student_ref="st_a_back",
        class_ref=_CLASS,
        weeks=10,
        status=StudentStatus.RETURNED,
    )
    other = StudentPlan(
        student_ref="st_b_back",
        class_ref=_CLASS,
        weeks=10,
        status=StudentStatus.RETURNED,
        emit_detection_evidence=False,
    )
    request = build_detect_request(week_start=_WEEK, seed=7, students=[back, other])
    response = detect(request)
    fired = {s.student_ref for s in _rule_signals(response, RuleId.R5)}
    assert fired == {"st_a_back"}, f"남의 전환 이력이 쓰였다: {fired}"


# ───────────────────────── resolver 등록 ─────────────────────────


def test_every_rule_has_an_evidence_resolver() -> None:
    """🔴 **규칙이 늘었는데 resolver를 안 붙이면 red다** — 폴백을 두지 않는다."""
    from ai.detection.evidence import RULE_EVIDENCE_RESOLVERS  # noqa: PLC0415

    missing = sorted(rule.value for rule in RuleId if rule not in RULE_EVIDENCE_RESOLVERS)
    assert not missing, f"근거 resolver가 없는 규칙: {missing}"


def test_an_unregistered_rule_raises_instead_of_falling_back() -> None:
    """*"등록 안 된 규칙 → 최신 아무 기록"* 폴백이 없다는 것을 값으로 못 박는다."""
    from ai.detection.evidence import (  # noqa: PLC0415
        EMPTY_EVIDENCE,
        EvidenceRequest,
        resolve_evidence,
    )

    request = EvidenceRequest(
        student_ref="st_x",
        label="acc_drop",
        evidence_weeks=(),
        learning_events={},
        student_evidence=EMPTY_EVIDENCE,
    )
    with pytest.raises(KeyError):
        resolve_evidence("R99", request)  # type: ignore[arg-type]
