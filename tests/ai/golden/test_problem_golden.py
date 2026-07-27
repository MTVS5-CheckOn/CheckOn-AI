"""M2 문제출제 골든셋 구조·개수·blind 스냅숏 CI 게이트."""

from pathlib import Path

import pytest

from ai.evaluation.problem_eval import ProblemGoldenError, ProblemGoldenEvaluator

GOLDEN_ROOT = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "ai"
    / "evaluation"
    / "golden"
    / "problems"
)


def test_problem_golden_evaluator_passes_offline() -> None:
    report = ProblemGoldenEvaluator(GOLDEN_ROOT).evaluate()

    assert report.passed
    assert report.total_cases == 61
    assert report.prompt_snapshot_count == 3
    assert report.educational_review_status == "expert_review_pending"
    assert report.real_model_evaluated is False
    assert {summary.suite: summary.case_count for summary in report.suites} == {
        "rule_violations": 18,
        "ambiguity": 11,
        "normal_pass": 10,
        "refine": 11,
        "graphrag": 11,
    }


def test_all_problem_golden_cases_are_marked_expert_review_pending() -> None:
    report = ProblemGoldenEvaluator(GOLDEN_ROOT).evaluate()

    assert report.educational_review_status == "expert_review_pending"


def test_cross_solve_snapshot_has_no_blind_fields() -> None:
    content = (
        GOLDEN_ROOT / "prompt_snapshots" / "cross_solve.snapshot.txt"
    ).read_text(encoding="utf-8")

    assert '"answer"' not in content
    assert '"rationale"' not in content
    assert '"evidence"' not in content
    assert '"target_metadata"' in content


def test_problem_golden_rejects_missing_suite(tmp_path: Path) -> None:
    with pytest.raises(ProblemGoldenError, match="읽을 수 없다"):
        ProblemGoldenEvaluator(tmp_path).evaluate()
