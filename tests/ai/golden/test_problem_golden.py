"""M2 문제출제 골든셋 구조·개수·blind 스냅숏 CI 게이트."""

from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from ai.evaluation.problem_eval import (
    ProblemGoldenCase,
    ProblemGoldenError,
    ProblemGoldenEvaluator,
    _load_suite,
)

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
    assert report.total_cases == 69
    assert report.prompt_snapshot_count == 3
    assert report.educational_review_status == "expert_review_pending"
    assert report.real_model_evaluated is False
    assert {summary.suite: summary.case_count for summary in report.suites} == {
        "rule_violations": 18,
        "ambiguity": 11,
        "normal_pass": 10,
        "refine": 11,
        "graphrag": 11,
        "suneung_format": 6,
        "passage_generation": 2,
    }


def test_suneung_format_suite_separates_supported_shapes_and_design_gap() -> None:
    report = ProblemGoldenEvaluator(GOLDEN_ROOT).evaluate()

    assert set(report.suneung_format_features) == {
        "single_stimulus",
        "labeled_examples",
        "text_table",
        "historical_korean",
        "compound_choices",
        "shared_stimulus",
    }
    assert report.design_gap_case_ids == ("SF6",)


def test_suneung_format_design_gap_forbids_llm_call() -> None:
    raw = yaml.safe_load(
        (GOLDEN_ROOT / "suneung_format" / "cases.yaml").read_text(encoding="utf-8")
    )
    design_gap = raw["cases"][-1]
    design_gap["expected"]["llm_calls"] = 1

    with pytest.raises(ValueError, match="LLM"):
        ProblemGoldenCase.model_validate(design_gap)


def test_passage_generation_suite_covers_success_and_fail_closed() -> None:
    suite = _load_suite(GOLDEN_ROOT, "passage_generation")

    assert tuple(case.expected.decision for case in suite.cases) == (
        "verified",
        "rejected_insufficient",
    )
    assert tuple(case.expected.llm_calls for case in suite.cases) == (3, 1)


def test_suneung_format_requires_explicit_contract_support(tmp_path: Path) -> None:
    source = GOLDEN_ROOT / "suneung_format" / "cases.yaml"
    target = tmp_path / "suneung_format"
    target.mkdir()
    raw = source.read_text(encoding="utf-8").replace(
        "    contract_support: supported_in_stem\n",
        "",
        1,
    )
    (target / "cases.yaml").write_text(raw, encoding="utf-8")

    with pytest.raises(ProblemGoldenError, match="지원 여부"):
        _load_suite(tmp_path, "suneung_format")


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
