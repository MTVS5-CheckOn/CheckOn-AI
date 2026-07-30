"""detection 엔진 통합 — R1~R6 발화/미발화·제외·병합·lifecycle (결정론).

정밀 픽스처(baseline = 최근 2주 제외 8주 평균을 고려)로 각 규칙의 발화 경계를 고정한다.
골든셋 G1~G12(evaluation/golden/detection)의 단위 근거이기도 하다.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta

from ai.contracts.detection import (
    AlertContextItem,
    Lifecycle,
    RuleId,
    Signal,
    SignalType,
    StudentStatus,
)
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


# ── 상한·rank 조합 회귀 (04 §3 · 09 §4 · 99 #14 · part_b/09 §1-8) ──
#
# 확정 파이프라인(99 #14 = 정본): 학생별 병합 → lifecycle 억제 탈락 →
# **new·follow_up만** 랭킹·상한 → **ongoing·R5는 상한 밖에서 합류** → 응답.
# 따라서 전체 signals 수와 rank는 cap_max를 넘을 수 있고, capped_out은
# new·follow_up 후보의 탈락 수만 센다(04 §3).
#
# 아래 기대값은 전부 문서에서 손으로 계산했다 — 엔진 산출을 붙여넣지 않았다.

#: 04 §3 "반·일 TOP 3~5 (cap_min: 3, cap_max: 5)" — 기대값 계산의 근거 상수.
_DOC_CAP_MAX = 5


def _risk_plan(student_ref: str, class_ref: str = "cl_a1") -> StudentPlan:
    """R1만 발화하는 학생 — 병합 없이 경보 1건(ACC_DROP)을 만든다.

    baseline(주0~7) 0.80 → 최근 2주 0.60 = 20%p 하락(04 §1 임계 15%p 초과 → 발화).
    duration이 일정해 R4는 발화하지 않는다(정답률 유지 + 시간 급증 조건 불성립).
    """
    return StudentPlan(
        student_ref=student_ref,
        class_ref=class_ref,
        weeks=10,
        solves_per_week=20,
        accuracy=(0.8,) * 8 + (0.60, 0.60),
    )


def _resolved_recently(student_ref: str) -> AlertContextItem:
    """week_start 7일 전 해소 + 미팔로업 → 09 §4 3행 = follow_up."""
    from zoneinfo import ZoneInfo

    resolved_at = (_monday(WEEK_START) - timedelta(days=7)).replace(tzinfo=ZoneInfo("Asia/Seoul"))
    return alert_resolved(
        student_ref, SignalType.ACC_DROP, resolved_at=resolved_at, followed_up=False
    )


def test_cap_max_default_matches_threshold_doc() -> None:
    """04 §3의 cap_max=5가 설정 기본값과 일치 — 아래 기대값들의 전제."""
    from ai.detection.thresholds import default_threshold_config

    assert default_threshold_config().cap_max == _DOC_CAP_MAX


def test_ongoing3_new6_yields_8_signals_and_capped_out_1() -> None:
    """ongoing 3 + new 6 → 최종 8, capped_out=1 (part_b/09 §1-8 요구 케이스).

    손계산(04 §3): 상한 대상은 new 6건 → 통과 min(6, cap_max=5)=5, 탈락 6−5=**1**.
    ongoing 3건은 상한 밖이라 전부 합류 → 최종 5+3=**8**.
    """
    ongoing_refs = ["st_ong_1", "st_ong_2", "st_ong_3"]
    students = [_risk_plan(ref) for ref in ongoing_refs]
    students += [_risk_plan(f"st_new_{i}") for i in range(1, 7)]
    context = [alert_open(ref, SignalType.ACC_DROP) for ref in ongoing_refs]

    resp = detect(
        build_detect_request(
            week_start=WEEK_START, seed=1, students=students, alert_context=context
        )
    )

    assert len(resp.signals) == 8
    assert resp.stats.capped_out == 1
    by_lifecycle = Counter(s.lifecycle for s in resp.signals)
    assert by_lifecycle[Lifecycle.ONGOING] == 3
    assert by_lifecycle[Lifecycle.NEW] == 5


def test_new_and_followup_share_one_cap_budget() -> None:
    """new·follow_up은 **한 예산**을 나눠 쓴다(04 §3 "new·follow_up만 랭킹·상한").

    손계산: 상한 후보 = new 3 + follow_up 4 = 7건 → 통과 5, 탈락 7−5=**2**.
    상한 밖(ongoing·R5)이 없으므로 최종 신호 수 = 통과분 **5**.
    """
    followup_refs = [f"st_fu_{i}" for i in range(1, 5)]
    students = [_risk_plan(ref) for ref in followup_refs]
    students += [_risk_plan(f"st_new_{i}") for i in range(1, 4)]
    context = [_resolved_recently(ref) for ref in followup_refs]

    resp = detect(
        build_detect_request(
            week_start=WEEK_START, seed=1, students=students, alert_context=context
        )
    )

    assert len(resp.signals) == 5
    assert resp.stats.capped_out == 2
    assert {s.lifecycle for s in resp.signals} <= {Lifecycle.NEW, Lifecycle.FOLLOW_UP}


def test_r5_joins_outside_cap() -> None:
    """R5는 상한 밖(04 §3 "R5는 상한 밖 — 정책 신호").

    손계산: 상한 후보 = new 6 → 통과 5, 탈락 **1**. R5 1건은 상한 밖 합류 →
    최종 5+1=**6**이고 R5의 rank는 통과분 다음인 **6**(cap_max 초과).
    """
    students = [_risk_plan(f"st_new_{i}") for i in range(1, 7)]
    students.append(
        StudentPlan(
            student_ref="st_r5_only",
            class_ref="cl_a1",
            weeks=10,
            status=StudentStatus.RETURNED,
        )
    )

    resp = detect(build_detect_request(week_start=WEEK_START, seed=1, students=students))

    assert len(resp.signals) == 6
    assert resp.stats.capped_out == 1
    care = [s for s in resp.signals if s.signal_type is SignalType.RETURN_CARE]
    assert len(care) == 1
    assert care[0].rank == 6  # 상한 밖 — rank가 cap_max를 넘는다


def test_cap_is_per_class_not_summed() -> None:
    """상한은 **반별**이다(04 §3 "반·일 TOP 3~5") — 반을 합쳐서 세지 않는다.

    손계산: 두 반 각각 new 6 → 반마다 통과 5·탈락 1.
    최종 신호 5+5=**10**, capped_out은 응답 전체 합 1+1=**2**,
    각 반의 rank 집합은 독립적으로 **{1,2,3,4,5}**.
    """
    students = [_risk_plan(f"st_a{i}", class_ref="cl_a1") for i in range(1, 7)]
    students += [_risk_plan(f"st_b{i}", class_ref="cl_b2") for i in range(1, 7)]

    resp = detect(build_detect_request(week_start=WEEK_START, seed=1, students=students))

    assert len(resp.signals) == 10
    assert resp.stats.capped_out == 2
    ranks_by_class: dict[str, set[int]] = defaultdict(set)
    for signal in resp.signals:
        ranks_by_class[signal.class_ref].add(signal.rank)
    assert ranks_by_class["cl_a1"] == {1, 2, 3, 4, 5}
    assert ranks_by_class["cl_b2"] == {1, 2, 3, 4, 5}


def test_outside_cap_signals_are_ordered_by_student_ref_with_exact_ranks() -> None:
    """상한 밖 합류분은 통과분 **뒤에 student_ref 오름차순**으로 rank를 받는다.

    손계산(04 §3 적용 순서 ④ · part_b/09 §1-8 데모 정합분 `st_08(R5)=3 · st_10(ongoing)=4`):
    new 6 → 통과 5(rank 1~5). ongoing 3은 student_ref 오름차순
    (st_ong_1 < st_ong_2 < st_ong_3)으로 **rank 6·7·8**. 최대 rank 8 > cap_max 5.
    """
    ongoing_refs = ["st_ong_1", "st_ong_2", "st_ong_3"]
    students = [_risk_plan(ref) for ref in ongoing_refs]
    students += [_risk_plan(f"st_new_{i}") for i in range(1, 7)]
    context = [alert_open(ref, SignalType.ACC_DROP) for ref in ongoing_refs]

    resp = detect(
        build_detect_request(
            week_start=WEEK_START, seed=1, students=students, alert_context=context
        )
    )

    outside = sorted(
        (s for s in resp.signals if s.lifecycle is Lifecycle.ONGOING),
        key=lambda s: s.rank,
    )
    assert [(s.student_ref, s.rank) for s in outside] == [
        ("st_ong_1", 6),
        ("st_ong_2", 7),
        ("st_ong_3", 8),
    ]
    passed_ranks = {s.rank for s in resp.signals if s.lifecycle is Lifecycle.NEW}
    assert passed_ranks == {1, 2, 3, 4, 5}
    assert max(s.rank for s in resp.signals) > _DOC_CAP_MAX


# ── 병합 lifecycle 양방향 — **열린 안건**(현행 동작 기록) ──────────
#
# TODO(09 §1-8): 병합 lifecycle 경계 A+BE 결정 대기 — 결정 시 이 테스트가 바뀔 수 있음.
# 현재 구현은 `primary.signal_type` 하나로만 lifecycle을 판정한다(engine._rank_with_lifecycle
# → resolve_lifecycle의 same_type 필터). 아래 두 테스트는 그 현행 동작을 **기록**할 뿐
# 확정 사양이 아니다.


def _merged_two_rule_plan(student_ref: str) -> StudentPlan:
    """R1+R2 동시 발화 → 1경보로 병합. primary는 score max인 R1(ACC_DROP).

    손계산(04 §3.1): R1 = clamp((30−15)/(25−15))=**1.0**(0.80→0.50 = 30%p 하락),
    R2 = clamp((3−3)/(5−3))=**0.0**(연속 미제출 정확히 3회). 따라서 primary=R1.
    """
    return StudentPlan(
        student_ref=student_ref,
        class_ref="cl_a1",
        weeks=10,
        solves_per_week=20,
        accuracy=(0.8,) * 8 + (0.50, 0.50),
        submit_ok=(True,) * 7 + (False, False, False),
    )


def test_merged_lifecycle_follows_primary_when_primary_is_ongoing() -> None:
    """primary=ongoing · secondary=new → 병합 경보 전체가 ongoing(상한 밖).

    TODO(09 §1-8): 병합 lifecycle 경계 A+BE 결정 대기 — 결정 시 이 테스트가 바뀔 수 있음.
    현행 기록용. 손계산: 상한 후보는 new 5건뿐이라 통과 5·탈락 **0**,
    병합 학생은 상한 밖으로 합류해 최종 **6**건(슬롯 미소비).
    """
    students = [_merged_two_rule_plan("st_merge")]
    students += [_risk_plan(f"st_new_{i}") for i in range(1, 6)]
    context = [alert_open("st_merge", SignalType.ACC_DROP)]  # primary(R1) 유형에 open

    resp = detect(
        build_detect_request(
            week_start=WEEK_START, seed=1, students=students, alert_context=context
        )
    )

    merged = next(s for s in resp.signals if s.student_ref == "st_merge")
    assert merged.rule_id is RuleId.R1  # primary = score max
    assert merged.lifecycle is Lifecycle.ONGOING
    assert len(resp.signals) == 6
    assert resp.stats.capped_out == 0  # ongoing이 슬롯을 먹지 않았다


def test_merged_lifecycle_ignores_secondary_history_when_primary_is_new() -> None:
    """primary=new · secondary=ongoing → secondary 이력이 무시되고 전체가 new(상한 대상).

    TODO(09 §1-8): 병합 lifecycle 경계 A+BE 결정 대기 — 결정 시 이 테스트가 바뀔 수 있음.
    현행 기록용. secondary(R2=submit_drop)에 open 이력이 있어도 판정은 primary(R1=acc_drop)
    기준이라 same_type이 비어 **new**가 된다(part_b/09 §1-8 "확인된 현행").
    """
    context = [alert_open("st_merge", SignalType.SUBMIT_DROP)]  # secondary(R2) 유형에만 open

    resp = detect(
        build_detect_request(
            week_start=WEEK_START,
            seed=1,
            students=[_merged_two_rule_plan("st_merge")],
            alert_context=context,
        )
    )

    merged = next(s for s in resp.signals if s.student_ref == "st_merge")
    assert merged.rule_id is RuleId.R1
    assert merged.lifecycle is Lifecycle.NEW
