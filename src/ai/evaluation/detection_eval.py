"""감지 골든셋 평가자 — contracts/evaluation.py Evaluator 구현.

소유: 박진희 (evaluation, A — 02_ownership.md §5). 평가 격리 — 프로덕션 import 금지.

골든셋 G1~G12(golden/detection/scenarios.py)를 threshold 시트 v0(default-v1)로 실행해
기대 발화/미발화를 대조한다. 기대값을 코드에서 고치지 않는다 — 시나리오가 원본이며
시트(04) 개정 시 함께 개정한다(08 §8).
"""

from __future__ import annotations

from ai.contracts.evaluation import CaseResult, EvaluationResult, GoldenSuite
from ai.detection.engine import detect
from ai.evaluation.golden.detection.scenarios import GoldenScenario, all_scenarios


def _evaluate_case(scenario: GoldenScenario) -> CaseResult:
    response = detect(scenario.request)
    fired = frozenset(signal.signal_type.value for signal in response.signals)

    if scenario.expect_single_merged:
        passed = len(response.signals) == 1
        reason = None if passed else f"복합 병합 실패 — signals={len(response.signals)}"
    else:
        passed = fired == scenario.expected_fired
        reason = (
            None
            if passed
            else f"fired={sorted(fired)} != expected={sorted(scenario.expected_fired)}"
        )

    return CaseResult(case_id=scenario.case_id, passed=passed, reason=reason)


class DetectionEvaluator:
    """G1~G12 스위트 평가자 (threshold default-v1 기준)."""

    @property
    def suite(self) -> GoldenSuite:
        return GoldenSuite.DETECTION

    def evaluate(self) -> EvaluationResult:
        cases = tuple(_evaluate_case(scenario) for scenario in all_scenarios())
        return EvaluationResult(suite=GoldenSuite.DETECTION, cases=cases)
