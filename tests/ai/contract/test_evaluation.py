"""contracts/evaluation.py 스모크 — enum 값 고정 · 왕복 직렬화 · 합격 판정.

소비자는 evaluation/golden/ 코퍼스다 (docs/part_a/08_evaluation_plan.md §1).
"""

import pytest

from ai.contracts.evaluation import (
    CaseResult,
    EvaluationResult,
    Evaluator,
    GoldenSuite,
    Metric,
    MetricKind,
)


class _StubEvaluator:
    @property
    def suite(self) -> GoldenSuite:
        return GoldenSuite.DETECTION

    def evaluate(self) -> EvaluationResult:
        return EvaluationResult(
            suite=self.suite,
            cases=(CaseResult(case_id="G5", passed=True),),
        )


def test_golden_suite_values_frozen() -> None:
    """평가 계획서 §1 디렉터리 구성과 1:1 — golden/ 하위 폴더명이 곧 값."""
    assert {suite.value for suite in GoldenSuite} == {
        "detection",
        "tone",
        "tagging",
        "refine_attack",
        "redaction",
        "import_corpus",
        "classify",
        "problems",
        "diagnosis",
    }


def test_metric_kind_values_frozen() -> None:
    assert {kind.value for kind in MetricKind} == {
        "accuracy",
        "recall",
        "precision",
        "false_negatives",
        "false_positives",
    }


def test_metric_roundtrip() -> None:
    metric = Metric(kind=MetricKind.ACCURACY, value=0.93, threshold=0.90, passed=True)
    assert Metric.model_validate(metric.model_dump(mode="json")) == metric


def test_evaluation_result_roundtrip() -> None:
    result = EvaluationResult(
        suite=GoldenSuite.TAGGING,
        cases=(CaseResult(case_id="boundary_1", passed=True),),
        metrics=(Metric(kind=MetricKind.ACCURACY, value=0.93, threshold=0.90, passed=True),),
    )
    assert EvaluationResult.model_validate(result.model_dump(mode="json")) == result


def test_stub_satisfies_evaluator_protocol() -> None:
    assert isinstance(_StubEvaluator(), Evaluator)
    assert _StubEvaluator().evaluate().passed is True


def test_failed_case_fails_suite() -> None:
    result = EvaluationResult(
        suite=GoldenSuite.REFINE_ATTACK,
        cases=(
            CaseResult(case_id="A1", passed=True),
            CaseResult(case_id="A2", passed=False, reason="comparison_exposure 미탐"),
        ),
    )
    assert result.passed is False
    assert [case.case_id for case in result.failed_cases] == ["A2"]


def test_failed_metric_fails_suite() -> None:
    """케이스가 전부 통과해도 지표 미달이면 스위트는 실패다."""
    result = EvaluationResult(
        suite=GoldenSuite.CLASSIFY,
        cases=(CaseResult(case_id="c1", passed=True),),
        metrics=(Metric(kind=MetricKind.RECALL, value=0.80, threshold=0.95, passed=False),),
    )
    assert result.passed is False


def test_unjudged_metric_does_not_fail_suite() -> None:
    """threshold 없는 지표는 관측만 — 계획서의 기준 다수가 아직 [제안]."""
    result = EvaluationResult(
        suite=GoldenSuite.IMPORT_CORPUS,
        cases=(CaseResult(case_id="form_1", passed=True),),
        metrics=(Metric(kind=MetricKind.ACCURACY, value=0.71),),
    )
    assert result.passed is True


def test_empty_suite_is_not_passed() -> None:
    """케이스 미수집을 성공으로 읽으면 골든셋이 비어도 CI가 초록이 된다."""
    assert EvaluationResult(suite=GoldenSuite.DETECTION).passed is False


def test_metric_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="kind"):
        Metric.model_validate({"kind": "f1_score", "value": 0.9})
