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
from ai.evaluation.golden.detection.scenarios import GoldenScenario, all_scenarios

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


def test_evidence_record_ids_all_exist() -> None:
    """모든 신호의 evidence record_id가 입력 스냅숏에 실존한다."""
    for scenario in all_scenarios():
        response = detect(scenario.request)
        known = {event.record_id for event in scenario.request.learning_events}
        for signal in response.signals:
            assert signal.evidence, f"{scenario.case_id}: evidence 비어 있음"
            for item in signal.evidence:
                assert item.record_id in known, f"{scenario.case_id}: {item.record_id} 미존재"
