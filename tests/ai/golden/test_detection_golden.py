"""감지 골든셋 G1~G12 + 횡단 검증 3종 — 08_evaluation_plan.md §2.

시나리오별 기대 발화/미발화가 threshold default-v1로 재현되는지 고정한다.
기대값을 코드에 맞춰 고치지 말 것 — 04 시트가 원본이다.
"""

from __future__ import annotations

import pytest

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
    """모든 신호의 evidence record_id가 입력 스냅숏에 실존한다."""
    for scenario in all_scenarios():
        response = detect(scenario.request)
        known = {event.record_id for event in scenario.request.learning_events}
        for signal in response.signals:
            assert signal.evidence, f"{scenario.case_id}: evidence 비어 있음"
            for item in signal.evidence:
                assert item.record_id in known, f"{scenario.case_id}: {item.record_id} 미존재"
