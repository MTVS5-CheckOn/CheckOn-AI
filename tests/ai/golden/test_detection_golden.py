"""감지 골든셋 G1~G12 + 횡단 검증 3종 — 08_evaluation_plan.md §2.

시나리오별 기대 발화/미발화가 threshold default-v1로 재현되는지 고정한다.
기대값을 코드에 맞춰 고치지 말 것 — 04 시트가 원본이다.
"""

from __future__ import annotations

from typing import Final

import pytest

from ai.contracts.detection import RuleId
from ai.contracts.evaluation import GoldenSuite
from ai.detection.engine import detect
from ai.evaluation.detection_eval import DetectionEvaluator
from ai.evaluation.fake_snapshot import StudentPlan, build_detect_request
from ai.evaluation.golden.detection.scenarios import (
    GoldenScenario,
    all_scenarios,
    ongoing_over_cap_request,
    suppression_promotion_request,
)

WEEK_START = "2026-07-13"


@pytest.mark.parametrize("scenario", all_scenarios(), ids=lambda s: s.case_id)
def test_golden_scenario(scenario: GoldenScenario) -> None:
    """각 G# 시나리오가 기대 발화/미발화를 재현한다."""
    response = detect(scenario.request)
    fired = frozenset(signal.signal_type.value for signal in response.signals)
    if scenario.expect_single_merged:
        assert len(response.signals) == 1, f"{scenario.case_id}: 복합 병합 실패"
    else:
        assert fired == scenario.expected_fired, f"{scenario.case_id}: {sorted(fired)}"
    if scenario.expected_advisory is not None:
        # 04 §1 R4 재정의(2026-08-03) — 판정 기대값은 그대로고 **소비 축 기대가 추가**됐다.
        assert len(response.signals) == 1, f"{scenario.case_id}: advisory 검사는 단일 경보 전제"
        assert response.signals[0].advisory is scenario.expected_advisory, scenario.case_id


def test_evaluator_suite_passes() -> None:
    """Evaluator로 스위트 전체가 통과한다 (CI 게이트 단위)."""
    result = DetectionEvaluator().evaluate()
    assert result.suite is GoldenSuite.DETECTION
    assert result.passed, [c.reason for c in result.failed_cases]


# ── 횡단 검증 3종 (08 §2) ──


def test_under_2_weeks_excluded_not_listed() -> None:
    """데이터 2주 미만 학생 → 미발화 + signals 미포함 + excluded_under_2w 집계."""
    acc = (0.8,) * 8 + (0.5, 0.5)  # 하락 궤적이어도
    plan = StudentPlan(
        student_ref="st_newcomer",
        class_ref="cl_a1",
        weeks=10,
        enrolled_weeks=1,
        solves_per_week=20,
        accuracy=acc,
    )
    response = detect(build_detect_request(week_start=WEEK_START, seed=1, students=[plan]))
    assert response.stats.excluded_under_2w == 1
    assert all(signal.student_ref != "st_newcomer" for signal in response.signals)
    assert len(response.signals) == 0


def test_top_cap_applied_and_capped_out_counted() -> None:
    """반 TOP 3~5 상한 — 6명 발화 시 5건만 응답, 나머지는 capped_out."""
    acc = (0.8,) * 8 + (0.5, 0.5)
    students = [
        StudentPlan(
            student_ref=f"st_cap_{i}",
            class_ref="cl_a1",
            weeks=10,
            solves_per_week=20,
            accuracy=acc,
        )
        for i in range(6)
    ]
    response = detect(build_detect_request(week_start=WEEK_START, seed=1, students=students))
    assert len(response.signals) == 5  # cap_max
    assert response.stats.capped_out == 1
    ranks = sorted(signal.rank for signal in response.signals)
    assert ranks == [1, 2, 3, 4, 5]


def test_suppression_before_ranking_promotes_sixth() -> None:
    """억제 승격(7/22 버그) — 반 6명 중 최상위 1명 억제 시 6번째가 5슬롯에 승격.

    억제를 랭킹 뒤에 적용하면(구 버그) 응답 4건 + st_sup_6 누락. 앞에 적용하면 5건 + 포함.
    """
    response = detect(suppression_promotion_request())
    refs = [signal.student_ref for signal in response.signals]
    assert len(response.signals) == 5, refs
    assert "st_sup_1" not in refs, "억제된 최상위는 응답에서 빠져야 한다"
    assert "st_sup_6" in refs, "억제로 열린 슬롯에 6번째 후보가 승격돼야 한다"
    assert response.stats.capped_out == 0  # 억제 후 5명 = cap_max이므로 컷 없음


def test_ongoing_exempt_from_cap() -> None:
    """ongoing 상한 제외(7/22) — ongoing 3 + new 5 → 8건, capped_out=0."""
    response = detect(ongoing_over_cap_request())
    lifecycles = [signal.lifecycle.value for signal in response.signals]
    assert len(response.signals) == 8, lifecycles
    assert lifecycles.count("ongoing") == 3, "ongoing 3건 전부 응답에 남아야 한다"
    assert lifecycles.count("new") == 5, "new 5건이 상한을 채운다"
    assert response.stats.capped_out == 0  # ongoing은 슬롯 미소비


def test_evidence_record_ids_all_exist() -> None:
    """모든 신호의 evidence record_id가 입력 스냅숏에 실존한다.

    ⚠ **정본 근거가 생기면서 출처가 둘이 됐다**(99 #43) — 학습 기록과 `detection_evidence`.
    두 집합의 합에서 찾는다. 🔴 **어느 표에서 왔는지는 아래 검사가 따로 본다** —
    여기서 합쳐 놓고 끝내면 *"R2가 학습 기록을 인용한다"* 가 다시 통과한다.
    """
    for scenario in all_scenarios():
        response = detect(scenario.request)
        known = {event.record_id for event in scenario.request.learning_events}
        known |= {item.record_id for item in scenario.request.detection_evidence}
        for signal in response.signals:
            assert signal.evidence, f"{scenario.case_id}: evidence 비어 있음"
            for item in signal.evidence:
                assert item.record_id in known, f"{scenario.case_id}: {item.record_id} 미존재"


#: 🔴 부재형 규칙이 인용해야 하는 **정본 테이블** — 학습 기록을 인용하면 red다(99 #43).
_ABSENCE_SOURCE_TABLES: Final = {
    RuleId.R2: "assignment_week_summary",
    RuleId.R3: "student_week_activity",
    RuleId.R5: "student_status_history",
}


def test_absence_rules_cite_their_authoritative_table() -> None:
    """🔴 **R2·R3·R5는 자기 주장의 정본만 인용한다** — 골든 전 시나리오에서.

    종전에는 근거가 비면 **가장 최근 아무 `learning_event`** 를 붙였다. 그 폴백이 사라졌는지를
    시나리오 전량에서 본다.
    """
    seen: set[RuleId] = set()
    for scenario in all_scenarios():
        response = detect(scenario.request)
        for signal in response.signals:
            expected_table = _ABSENCE_SOURCE_TABLES.get(signal.rule_id)
            if expected_table is None:
                continue
            seen.add(signal.rule_id)
            tables = {item.source_table for item in signal.evidence}
            assert tables == {expected_table}, (
                f"{scenario.case_id}: {signal.rule_id.value}가 {sorted(tables)}를 인용한다 "
                f"— {expected_table}만 근거다"
            )
    assert seen, "골든에 부재형 신호가 하나도 없다 — 이 검사가 아무것도 안 보고 있다"


# ── 기대치 입력 층 — 콜드 스타트 동일성 (04 §1 · 2026-08-03) ────


def test_expectation_cold_start_matches_legacy_for_all_scenarios() -> None:
    """🔴 **골든 전 시나리오**에서 기대치가 없으면 판정이 종전과 같다.

    기대치 층의 핵심 안전장치다 — 통계가 비면 전량 폴백(전체 평균)이라
    잔차 ≈ 기존 하락폭이 되어 회귀 위험이 0이다. 이걸 단위 테스트에만 두면
    "골든은 통과하는데 실제로는 바뀌는" 상황을 못 잡는다.
    """
    from dataclasses import replace

    from ai.detection.features import extract_features

    for scenario in all_scenarios():
        baseline_response = detect(scenario.request)
        # 기대치를 명시적으로 None으로 채운 피처 — 콜드 스타트와 같은 상태다.
        features = {
            ref: replace(
                sf,
                weeks=tuple(replace(w, expected_accuracy=None) for w in sf.weeks),
            )
            for ref, sf in extract_features(scenario.request).items()
        }
        forced = detect(scenario.request, stored_features={k: v.weeks for k, v in features.items()})
        assert {s.signal_type for s in forced.signals} == {
            s.signal_type for s in baseline_response.signals
        }, scenario.case_id
