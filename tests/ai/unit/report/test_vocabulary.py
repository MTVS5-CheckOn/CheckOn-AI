"""리포트 블록 어휘 YAML의 실패 닫힘 검증."""

import json
from pathlib import Path

import pytest

from ai.contracts.report import ReportBlockKind, ReportUnproducedMetric
from ai.report.vocabulary import (
    ReportVocabularyError,
    TimeSeriesMetricKind,
    load_report_block_vocabulary,
    load_report_root_cause_vocabulary,
    load_report_time_series_vocabulary,
    load_report_unproduced_vocabulary,
)


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "block_kinds.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _write_unproduced(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "unproduced_metrics.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _write_root_cause(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "root_cause_metrics.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _write_time_series(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "time_series_metrics.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_default_vocabulary_has_all_five_kinds() -> None:
    vocabulary = load_report_block_vocabulary()

    assert set(vocabulary.block_kinds) == set(ReportBlockKind)


@pytest.mark.parametrize(
    "body, reason",
    [
        (
            """version: report-block-kinds.v1
block_kinds: [greeting, fact, chart_analysis, suggestion]
""",
            "완전하지 않다",
        ),
        (
            """version: report-block-kinds.v1
block_kinds: [greeting, fact, chart_analysis, suggestion, closing, closing]
""",
            "중복",
        ),
        (
            """version: report-block-kinds.v1
block_kinds: [greeting, fact, chart_analysis, suggestion, closing]
unexpected: true
""",
            "unexpected",
        ),
        (
            """version: report-block-kinds.v1
block_kinds: [greeting, fact, chart_analysis, suggestion, unknown]
""",
            "unknown",
        ),
    ],
)
def test_vocabulary_fails_closed(tmp_path: Path, body: str, reason: str) -> None:
    with pytest.raises(ReportVocabularyError, match=reason):
        load_report_block_vocabulary(_write(tmp_path, body))


def test_malformed_yaml_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ReportVocabularyError, match="YAML"):
        load_report_block_vocabulary(_write(tmp_path, "block_kinds: [greeting"))


def test_default_unproduced_vocabulary_covers_contract_enum() -> None:
    vocabulary = load_report_unproduced_vocabulary()

    assert set(vocabulary.unproduced_metrics) == {metric.value for metric in ReportUnproducedMetric}
    assert all(vocabulary.reason_for(metric) for metric in ReportUnproducedMetric)


@pytest.mark.parametrize(
    "body",
    [
        """version: report-unproduced-metrics.v1
unproduced_metrics:
  national_percentile: {reason: 비교집단 계약 미확정}
  weekly_accuracy_intervention: {reason: 주차별 입력 없음}
""",
        """version: report-unproduced-metrics.v1
unproduced_metrics:
  national_percentile: {reason: 비교집단 계약 미확정}
  weekly_accuracy_intervention: {reason: 주차별 입력 없음}
  recent_six_week_baseline: {reason: 기간 정책 미확정}
  monthly_trend: {reason: 다기간 입력 없음}
""",
    ],
    ids=("missing", "extra"),
)
def test_unproduced_vocabulary_fails_closed_for_enum_mismatch(
    tmp_path: Path,
    body: str,
) -> None:
    with pytest.raises(ReportVocabularyError, match="완전하지 않다"):
        load_report_unproduced_vocabulary(_write_unproduced(tmp_path, body))


@pytest.mark.parametrize("section", ["metric_keys", "area_labels", "type_labels"])
def test_root_cause_vocabulary_fails_closed_for_missing_or_extra_keys(
    tmp_path: Path,
    section: str,
) -> None:
    source = load_report_root_cause_vocabulary().model_dump(mode="json")
    values = source[section]
    assert isinstance(values, dict)
    values.pop(next(iter(values)))
    values["unknown"] = "미등록"

    with pytest.raises(ReportVocabularyError, match="완전하지 않다"):
        load_report_root_cause_vocabulary(
            _write_root_cause(tmp_path, json.dumps(source, ensure_ascii=False))
        )


def test_default_time_series_vocabulary_has_policy_and_all_metric_keys() -> None:
    vocabulary = load_report_time_series_vocabulary()

    assert vocabulary.bucket_min_items == 10
    assert vocabulary.baseline_window_weeks == 6
    assert vocabulary.timezone_name == "Asia/Seoul"
    assert set(vocabulary.metric_keys) == {kind.value for kind in TimeSeriesMetricKind}


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("missing", "완전하지 않다"),
        ("extra", "완전하지 않다"),
        ("placeholder", "자리표시자"),
        ("timezone", "IANA"),
    ],
)
def test_time_series_vocabulary_fails_closed(
    tmp_path: Path,
    mutation: str,
    reason: str,
) -> None:
    source = load_report_time_series_vocabulary().model_dump(mode="json")
    keys = source["metric_keys"]
    assert isinstance(keys, dict)
    if mutation == "missing":
        keys.pop("monthly_accuracy")
    elif mutation == "extra":
        keys["unknown"] = "time_series.unknown"
    elif mutation == "placeholder":
        keys["monthly_accuracy"] = "time_series.monthly.accuracy"
    else:
        source["timezone_name"] = "Mars/Olympus"

    with pytest.raises(ReportVocabularyError, match=reason):
        load_report_time_series_vocabulary(
            _write_time_series(tmp_path, json.dumps(source, ensure_ascii=False))
        )
