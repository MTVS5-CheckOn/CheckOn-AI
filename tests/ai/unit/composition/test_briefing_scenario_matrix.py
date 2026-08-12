"""위험신호 대표·경계·병합·생애주기 매트릭스 — A단계 결정론 검증 (지시서 79 §4·§5).

🔴 **이 파일은 GPT를 부르지 않는다.** 규칙 발화·병합·생애주기·근거·게이트 피드백을 전부
결정론으로 잰다. **여기서 실패하면 실제 OpenAI를 호출하지 않는다**(§2).

⚠ **판정 대상은 엔진이다** — `Signal`을 손으로 만들지 않고 `DetectRequest`부터 통과시킨다.

🔴 **부동소수 때문에 「정확히 임계값」이 언제나 만들어지지는 않는다**(2026-08-12 실측):

| 경계 | 정수 이벤트로 도달 | 근거 |
| --- | --- | --- |
| R1 15.0pp | ✅ | `18/20 → 15/20` 이 정확히 15.0pp다 |
| R3 40% | ✅ | `8/20 == 0.4` |
| R4 시간 1.5배 | ✅ | 이분수 `200/800 → 300/800` |
| R4 정답률 5.0pp | ❌ | `0.05`가 이진수로 표현 불가 — bracket으로 좁힌다 |
| R6 0.5·10문항 | ✅ | `10/20 == 0.5` |

⚠ **못 만드는 경계를 「만들었다」고 적지 않는다** — 좁힌 bracket을 그대로 밝힌다.
"""

from __future__ import annotations

from typing import Any, Final

import pytest
from detect_briefing_scenarios import ANALYSIS_WEEK, ScenarioBuilder, builder, week_of

from ai.contracts.detection import DetectResponse, RuleId, SignalType
from ai.detection.engine import detect
from ai.detection.evidence import SKIP_AUTHORITATIVE_EVIDENCE_MISSING
from ai.detection.thresholds import default_threshold_config

_CONFIG: Final = default_threshold_config()

#: 응답 evidence 상한 — 계약값을 여기 복제하지 않고 계약에서 읽는다.
_EVIDENCE_CAP: Final = 3


def _run(scenario: ScenarioBuilder) -> DetectResponse:
    return detect(scenario.build(), _CONFIG)


def _rules(response: DetectResponse) -> list[str]:
    return sorted(s.rule_id.value for s in response.signals)


def _skips(response: DetectResponse, rule: RuleId) -> list[str]:
    return [r.reason for r in response.stats.rules_skipped if r.rule_id is rule]


def _drop_history(name: str, *, n: int = 20, base: int = 18, week: int = 15) -> ScenarioBuilder:
    """R1이 발화하는 기본형 — 기준선 `base/n`, 판정 2주 `week/n`."""
    scenario = builder(name).student("st_a")
    scenario.steady_history("st_a", n=n, correct=base, skip_recent=2)
    for back in (0, 1):
        scenario.solves("st_a", back=back, n=n, correct=week)
    return scenario


# ═════════════════════ A-1. R1~R6 단독 발화 ═════════════════════


def test_s01_r1_alone_fires_new_with_learning_event_evidence() -> None:
    """S01 — R1 학습 성과 급락 단독.

    🔴 기준선·현재값이 **학습 기록 근거**로 인용된다 — GPT가 새 수치를 계산할 필요가 없다.
    """
    response = _run(_drop_history("s01"))
    assert _rules(response) == ["R1"], _rules(response)
    signal = response.signals[0]
    assert signal.signal_type is SignalType.ACC_DROP
    assert signal.lifecycle.value == "new"
    assert signal.advisory is False
    assert signal.evidence, "근거 없는 신호는 생성될 수 없다(불변식 2)"
    assert all(item.source_table == "learning_event" for item in signal.evidence)


def test_s02_r2_alone_cites_only_assignment_aggregates() -> None:
    """S02 — 3주 연속 완전 미제출. 🔴 인용은 **과제 집계**뿐이다(과거 solve 금지)."""
    scenario = builder("s02").student("st_a").steady_history("st_a")
    for back in range(_CONFIG.r2.consecutive_missing):
        scenario.assignment("st_a", back=back, expected=3, submitted=0)
    scenario.assignment("st_a", back=_CONFIG.r2.consecutive_missing, expected=3, submitted=3)

    response = _run(scenario)
    assert _rules(response) == ["R2"], _rules(response)
    signal = response.signals[0]
    assert signal.signal_type is SignalType.SUBMIT_DROP
    assert all(i.source_table == "assignment_week_summary" for i in signal.evidence)
    #: 🔴 예정 수·제출 수가 문면에 그대로 있다 — 「제출률 0%」 같은 파생은 근거가 아니다.
    assert any("예정 과제" in i.summary and "제출" in i.summary for i in signal.evidence)


def test_s03_r3_alone_distinguishes_zero_from_absent() -> None:
    """S03 — 이번 주 활동 0건. 🔴 **집계 부재와 0건은 다른 사실**이다."""
    scenario = builder("s03").student("st_a").steady_history("st_a")
    scenario.activity_series("st_a", counts={0: 0}, baseline=20)

    response = _run(scenario)
    assert _rules(response) == ["R3"], _rules(response)
    signal = response.signals[0]
    assert signal.signal_type is SignalType.VOLUME_GAP
    assert all(i.source_table == "student_week_activity" for i in signal.evidence)
    assert any("0건" in i.summary for i in signal.evidence), [
        i.summary for i in signal.evidence
    ]


def test_s04_r4_alone_is_advisory_and_not_an_accuracy_drop() -> None:
    """S04 — 숨은 부담. 🔴 `advisory=true`이고 **성과 하락이 아니다**."""
    scenario = builder("s04").student("st_a")
    scenario.steady_history("st_a", n=20, correct=16, seconds=200, words=800, skip_recent=2)
    for back in (0, 1):
        scenario.solves("st_a", back=back, n=20, correct=16, seconds=320, words=800)

    response = _run(scenario)
    assert _rules(response) == ["R4"], _rules(response)
    signal = response.signals[0]
    assert signal.signal_type is SignalType.HIDDEN_RISK
    assert signal.advisory is True, "R4 단독은 참고 신호다"
    #: 🔴 정답률은 안정 구간에 남아 있다 — 하락으로 표현하면 거짓이다.
    assert RuleId.R1.value not in _rules(response)


def test_s05_r5_alone_cites_only_the_transition() -> None:
    """S05 — 복귀 관찰. 🔴 그 주 학습 기록이 0건인데 **활동량을 만들지 않는다**."""
    scenario = builder("s05").student("st_a", status="returned")
    scenario.steady_history("st_a", skip_recent=1)
    scenario.returned("st_a", back=0)

    response = _run(scenario)
    assert _rules(response) == ["R5"], _rules(response)
    signal = response.signals[0]
    assert signal.signal_type is SignalType.RETURN_CARE
    assert [i.source_table for i in signal.evidence] == ["student_status_history"]
    assert "복귀" in signal.evidence[0].summary


def test_s06_r6_alone_does_not_leak_internal_tags() -> None:
    """S06 — 유형 편중. ⚠ 내부 enum·영문 태그가 **사용자 문면에 노출되지 않는다**."""
    scenario = builder("s06").student("st_a").steady_history("st_a", skip_recent=1)
    scenario.solves(
        "st_a", back=0, n=12, correct=3, area="language", type_tag="fact"
    )

    response = _run(scenario)
    assert _rules(response) == ["R6"], _rules(response)
    signal = response.signals[0]
    assert signal.signal_type is SignalType.TYPE_BIAS
    for token in ("language", "fact", "R6", "TYPE_BIAS"):
        assert token not in signal.display_label, signal.display_label


# ═════════════════════ A-2. 병합과 분리 ═════════════════════


def test_s07_r1_and_r6_merge_into_one_signal() -> None:
    """S07 — R1 + R6은 **한 건으로 병합**되고 대표는 결정론이다."""
    scenario = builder("s07").student("st_a")
    scenario.steady_history("st_a", n=25, correct=22, skip_recent=2)
    for back in (0, 1):
        scenario.solves(
            "st_a", back=back, n=12, correct=3, area="language", type_tag="fact"
        )

    response = _run(scenario)
    assert len(response.signals) == 1, _rules(response)
    signal = response.signals[0]
    assert signal.rule_id is RuleId.R1, "대표는 최고 score다"
    assert 0 < len(signal.evidence) <= _EVIDENCE_CAP
    assert len({i.record_id for i in signal.evidence}) == len(signal.evidence)


def test_s08_r2_and_r3_merge_without_inventing_causation() -> None:
    """S08 — 두 관측이 **한 건**으로 병합된다. 인과는 엔진도 만들지 않는다."""
    scenario = builder("s08").student("st_a").steady_history("st_a")
    for back in range(_CONFIG.r2.consecutive_missing):
        scenario.assignment("st_a", back=back, expected=3, submitted=0)
    scenario.activity_series("st_a", counts={0: 0}, baseline=20)

    response = _run(scenario)
    assert len(response.signals) == 1, _rules(response)
    signal = response.signals[0]
    assert signal.rule_id in {RuleId.R2, RuleId.R3}
    tables = {i.source_table for i in signal.evidence}
    assert tables <= {"assignment_week_summary", "student_week_activity"}, tables
    assert len(signal.evidence) <= _EVIDENCE_CAP


def test_s09_r4_merged_with_a_non_advisory_rule_is_not_advisory() -> None:
    """S09 — 🔴 병합 결과가 R4뿐이 **아니면** advisory가 아니다(`ranking.merge_student`)."""
    scenario = builder("s09").student("st_a")
    scenario.steady_history("st_a", n=20, correct=16, seconds=200, words=800, skip_recent=2)
    for back in (0, 1):
        scenario.solves("st_a", back=back, n=20, correct=16, seconds=320, words=800)
        scenario.solves(
            "st_a", back=back, n=12, correct=3, area="language", type_tag="fact"
        )

    response = _run(scenario)
    assert len(response.signals) == 1, _rules(response)
    assert response.signals[0].advisory is False, "R4가 섞였어도 정식 신호다"


def test_s10_r5_stays_a_separate_signal() -> None:
    """S10 — R5는 다른 규칙에 **병합되지 않는다**.

    ⚠ 복귀 학생은 `readapt_relax`가 걸려 R2 임계가 올라간다(3 → 4주) — 실제 도달 가능한
    조합으로 맞춘다(억지 픽스처 금지).
    """
    needed = round(_CONFIG.r2.consecutive_missing * _CONFIG.segments.readapt_relax)
    scenario = builder("s10").student("st_a", status="returned")
    scenario.steady_history("st_a")
    for back in range(needed):
        scenario.assignment("st_a", back=back, expected=3, submitted=0)
    scenario.assignment("st_a", back=needed, expected=3, submitted=3)
    scenario.returned("st_a", back=0)

    response = _run(scenario)
    assert _rules(response) == ["R2", "R5"], _rules(response)
    by_rule = {s.rule_id: s for s in response.signals}
    #: 🔴 같은 근거를 잘못 공유하지 않는다.
    r2_ids = {i.record_id for i in by_rule[RuleId.R2].evidence}
    r5_ids = {i.record_id for i in by_rule[RuleId.R5].evidence}
    assert not (r2_ids & r5_ids), r2_ids & r5_ids


# ═════════════════════ A-3. 생애주기 ═════════════════════


@pytest.mark.parametrize(
    ("case", "history", "expected"),
    [
        ("s11", None, "new"),
        ("s12", {"status": "open"}, "ongoing"),
        (
            "s13",
            {"status": "resolved", "resolved_days_ago": 7, "followed_up": False},
            "follow_up",
        ),
    ],
)
def test_s11_s13_lifecycle_is_decided_by_alert_context(
    case: str, history: dict[str, Any] | None, expected: str
) -> None:
    """S11~S13 — `alert_context`가 lifecycle을 **결정론으로** 정한다."""
    scenario = _drop_history(case)
    if history is not None:
        scenario.history("st_a", signal_type=SignalType.ACC_DROP.value, **history)

    response = _run(scenario)
    assert _rules(response) == ["R1"], _rules(response)
    assert response.signals[0].lifecycle.value == expected


def test_s14_a_followed_up_resolution_suppresses_the_signal() -> None:
    """S14 — 팔로업까지 끝난 최근 해소는 **발화하지 않는다**.

    🔴 빈 브리핑을 만들어 통과시키지 않는다 — `signals` 배열에서의 **부재**로 표현된다.
    """
    scenario = _drop_history("s14")
    scenario.history(
        "st_a",
        signal_type=SignalType.ACC_DROP.value,
        status="resolved",
        resolved_days_ago=7,
        followed_up=True,
    )

    response = _run(scenario)
    assert response.signals == (), _rules(response)
    assert response.stats.students_evaluated == 1, "제외가 아니라 억제여야 한다"


# ═════════════════════ A-4. 임계값 경계 ═════════════════════


def test_s15_r1_boundary_fires_at_exactly_the_threshold() -> None:
    """S15 — R1은 임계값과 **정확히 같을 때 발화**하고 그 아래는 안 한다.

    실측: `18/20 → 15/20`은 정확히 `15.0pp`다. 미발화 쪽은 **소수점 아래에서** 갈리는
    `114/120 → 96/120`(14.999…pp)를 쓴다 — 10pp짜리 넉넉한 반례보다 경계를 좁힌다.
    """
    assert _CONFIG.r1.drop_pp == 15.0, "임계가 바뀌었다 — 이 경계 픽스처를 다시 만들어라"
    assert _rules(_run(_drop_history("s15fire", n=20, base=18, week=15))) == ["R1"]
    assert _rules(_run(_drop_history("s15under", n=120, base=114, week=96))) == []


def test_s16_r3_boundary_and_absent_aggregate() -> None:
    """S16 — 40%는 미발화, 그 아래는 발화, **기준창 한 주 누락은 skip**이다."""
    ratio = _CONFIG.r3.volume_ratio
    assert ratio == 0.4, "임계가 바뀌었다"
    baseline = 20
    at_boundary = int(baseline * ratio)  # 8건 = 정확히 40%

    def scenario(name: str, current: int, *, gap: int | None = None) -> ScenarioBuilder:
        built = builder(name).student("st_a").steady_history("st_a")
        counts = {0: current} | ({gap: -1} if gap is not None else {})
        built.activity_series("st_a", counts=counts, baseline=baseline)
        return built

    assert _rules(_run(scenario("s16eq", at_boundary))) == [], "40%는 발화하지 않는다"
    assert _rules(_run(scenario("s16lo", at_boundary - 1))) == ["R3"]

    absent = _run(scenario("s16gap", at_boundary - 1, gap=3))
    assert _rules(absent) == [], "집계가 빠졌는데 발화했다"
    assert SKIP_AUTHORITATIVE_EVIDENCE_MISSING in _skips(absent, RuleId.R3)


def test_s17_r4_time_boundary_is_exact_and_accuracy_band_is_bracketed() -> None:
    """S17 — 시간 1.5배는 **정확히** 잴 수 있고, 정답률 5.0pp는 **못 만든다**.

    🔴 `0.05`는 이진수로 표현되지 않는다 — `0.80 - 0.75`가 `5.000000000000004`라
    「정확히 5.0pp」가 **초과로 판정**된다(실측). ⇒ 이분수로 4.6875pp(허용) ·
    6.25pp(초과) bracket을 쓴다. **못 만드는 경계를 만들었다고 적지 않는다.**
    """
    assert _CONFIG.r4.time_ratio == 1.5 and _CONFIG.r4.acc_stable_band_pp == 5.0

    def scenario(name: str, seconds: int, week_correct: int) -> ScenarioBuilder:
        built = builder(name).student("st_a")
        built.steady_history(
            "st_a", n=64, correct=52, seconds=200, words=800, skip_recent=2
        )
        for back in (0, 1):
            built.solves(
                "st_a", back=back, n=64, correct=week_correct, seconds=seconds, words=800
            )
        return built

    #: 시간 정확히 1.5배(200→300) · 정답률 동일 → 발화.
    assert _rules(_run(scenario("s17eq", 300, 52))) == ["R4"]
    #: 1.45배 → 미발화.
    assert _rules(_run(scenario("s17lo", 290, 52))) == []
    #: 1.6배 · 정답률 4.6875pp 하락(허용 안) → 발화.
    assert _rules(_run(scenario("s17band", 320, 49))) == ["R4"]
    #: 1.6배 · 정답률 6.25pp 하락(허용 초과) → 미발화.
    assert _rules(_run(scenario("s17over", 320, 48))) == []


def test_s18_r6_boundaries() -> None:
    """S18 — 태깅률·셀 문항 수·오답 비중·셀 정답률 네 경계."""
    p = _CONFIG.r6
    assert (p.tagging_rate_min, p.cell_min_items, p.cell_error_share, p.cell_acc_below) == (
        0.6,
        10,
        0.5,
        0.5,
    )

    def scenario(
        name: str, *, cell_n: int, cell_correct: int, other_n: int, other_correct: int
    ) -> ScenarioBuilder:
        built = builder(name).student("st_a").steady_history("st_a", skip_recent=1)
        built.solves(
            "st_a", back=0, n=cell_n, correct=cell_correct, area="language", type_tag="fact"
        )
        if other_n:
            built.solves("st_a", back=0, n=other_n, correct=other_correct)
        return built

    def fired(name: str, **kwargs: int) -> list[str]:
        return _rules(_run(scenario(name, **kwargs)))

    #: 셀 문항 10 · 셀 정답률 0.4 · 오답 비중 6/(6+0)=1.0 → 발화.
    assert fired("s18n10", cell_n=10, cell_correct=4, other_n=0, other_correct=0) == ["R6"]
    #: 셀 문항 9 → 최소 문항 미달 → 미발화.
    assert fired("s18n9", cell_n=9, cell_correct=3, other_n=0, other_correct=0) == []
    #: 오답 비중 정확히 0.5(셀 오답 6 · 타셀 오답 6) · 셀 정답률 0.4 → 발화.
    assert fired("s18share", cell_n=10, cell_correct=4, other_n=20, other_correct=14) == ["R6"]
    #: 셀 정답률 정확히 0.5 → 미발화(`>=`로 거른다).
    assert fired("s18acc", cell_n=10, cell_correct=5, other_n=0, other_correct=0) == []


def test_s18_r6_tagging_rate_boundary() -> None:
    """S18 — 태깅률 60% 경계. ⚠ 태그 없는 이벤트를 섞어 비율을 만든다."""
    scenario = builder("s18tag").student("st_a").steady_history("st_a", skip_recent=1)
    #: 태깅 12 / 전체 20 = 60% → 평가 가능.
    scenario.solves("st_a", back=0, n=12, correct=4, area="language", type_tag="fact")
    for index in range(8):
        scenario.learning_events.append(
            {
                "record_id": f"le_untagged_{index}",
                "student_ref": "st_a",
                "type": "solve",
                "occurred_at": f"{week_of(0).isoformat()}T19:00:00+00:00",
                "correct": True,
                "duration_sec": 100,
                "passage_word_count": 500,
                "source": "trackB",
            }
        )
    assert _rules(_run(scenario)) == ["R6"]


# ═════════════════════ A-5. 부재 근거와 스트레스 ═════════════════════


def test_s19_r2_separates_no_assignment_from_no_submission() -> None:
    """S19 — 과제가 **없던 주**는 연속에서 제외되고, 부분 제출은 연속을 끊는다."""
    #: 과제 없는 주(expected=0)가 중간에 있어도 연속은 이어진다.
    scenario = builder("s19skip").student("st_a").steady_history("st_a")
    scenario.assignment("st_a", back=0, expected=3, submitted=0)
    scenario.assignment("st_a", back=1, expected=0, submitted=0)  # 과제 없음
    scenario.assignment("st_a", back=2, expected=3, submitted=0)
    scenario.assignment("st_a", back=3, expected=3, submitted=0)
    assert _rules(_run(scenario)) == ["R2"]

    #: 부분 제출이 끼면 연속이 끊긴다.
    broken = builder("s19break").student("st_a").steady_history("st_a")
    broken.assignment("st_a", back=0, expected=3, submitted=0)
    broken.assignment("st_a", back=1, expected=3, submitted=1)  # 부분 제출
    broken.assignment("st_a", back=2, expected=3, submitted=0)
    assert _rules(_run(broken)) == []


def test_s20_an_absent_aggregate_is_not_treated_as_zero() -> None:
    """S20 — 집계 자체가 없으면 **0으로 간주하지 않고** skip이다."""
    scenario = builder("s20").student("st_a").steady_history("st_a")
    #: 분석 주 집계를 아예 안 넣는다.
    scenario.activity_series("st_a", counts={0: -1}, baseline=20)

    response = _run(scenario)
    assert _rules(response) == []
    assert SKIP_AUTHORITATIVE_EVIDENCE_MISSING in _skips(response, RuleId.R3)


def test_s21_evidence_is_capped_and_deduplicated() -> None:
    """S21 — 병합해도 응답 evidence는 상한을 넘지 않는다."""
    scenario = builder("s21").student("st_a")
    scenario.steady_history("st_a", n=25, correct=22, skip_recent=2)
    for back in (0, 1):
        scenario.solves(
            "st_a", back=back, n=12, correct=3, area="language", type_tag="fact"
        )
    for back in range(_CONFIG.r2.consecutive_missing):
        scenario.assignment("st_a", back=back, expected=3, submitted=0)
    scenario.activity_series("st_a", counts={0: 0}, baseline=20)

    response = _run(scenario)
    assert len(response.signals) == 1
    evidence = response.signals[0].evidence
    assert 0 < len(evidence) <= _EVIDENCE_CAP, len(evidence)
    assert len({i.record_id for i in evidence}) == len(evidence)


def test_s22_similar_students_do_not_share_facts() -> None:
    """S22 — 🔴 비슷하지만 **다른 수치**인 두 학생의 근거가 섞이지 않는다.

    ⚠ 신규 신호 상한(`cap_max`)에 걸리지 않게 **반을 나눈다** — 프로덕션 상한을
    우회하지 않는다(§4 A-5 주의).
    """
    scenario = builder("s22")
    scenario.student("st_a", class_ref="cl_a").student("st_b", class_ref="cl_b")
    #: A: 기준선 90% → 70%(20pp) · B: 기준선 85% → 65%(20pp) — 값이 다르다.
    scenario.steady_history("st_a", n=20, correct=18, skip_recent=2)
    scenario.steady_history("st_b", n=20, correct=17, skip_recent=2)
    for back in (0, 1):
        scenario.solves("st_a", back=back, n=20, correct=14)
        scenario.solves("st_b", back=back, n=20, correct=13)

    response = _run(scenario)
    assert len(response.signals) == 2, _rules(response)
    by_student = {s.student_ref: s for s in response.signals}
    assert set(by_student) == {"st_a", "st_b"}
    #: 🔴 근거 레코드가 학생별로 완전히 분리된다.
    a_ids = {i.record_id for i in by_student["st_a"].evidence}
    b_ids = {i.record_id for i in by_student["st_b"].evidence}
    assert a_ids and b_ids and not (a_ids & b_ids), a_ids & b_ids
    #: ⚠ 식별자는 사용자 문면(`display_label`)에 노출되지 않는다.
    for signal in response.signals:
        for token in (signal.student_ref, signal.class_ref, str(signal.signal_id)):
            assert token not in signal.display_label, signal.display_label


def test_the_analysis_week_is_the_declared_one() -> None:
    """🔴 **절단 가드** — 분석 주가 밀리면 위 전부가 다른 것을 재고 있다."""
    response = _run(_drop_history("guard"))
    assert response.stats.students_evaluated == 1
    assert str(ANALYSIS_WEEK) == "2026-07-20"
    assert response.signals[0].evidence, "근거가 비면 이 파일의 전제가 깨졌다"
