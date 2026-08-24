"""진단 이벤트 시계열의 버킷·표본·재현성 불변식."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from ai.contracts.detection import (
    ClassRef,
    DetectRequest,
    EventSource,
    EventType,
    LearningEvent,
    SnapshotMeta,
    StudentInput,
    StudentStatus,
    TermContext,
)
from ai.contracts.diagnosis import DiagnosisEvent, DiagnosisInput, Period
from ai.contracts.taxonomy import AreaTag, TypeTag
from ai.detection.features import extract_features
from ai.report.time_series import ReportTimeSeriesError, build_time_series_metrics
from ai.report.vocabulary import (
    ReportTimeSeriesVocabulary,
    TimeSeriesMetricKind,
    load_report_time_series_vocabulary,
)

SEOUL = ZoneInfo("Asia/Seoul")


def _event(event_id: str, occurred_at: datetime, *, correct: bool = True) -> DiagnosisEvent:
    return DiagnosisEvent(
        event_id=event_id,
        area_tag=AreaTag.LANGUAGE,
        type_tag=TypeTag.CONCEPT,
        correct=correct,
        occurred_at=occurred_at,
        tag_confirmed=True,
    )


def _input(*events: DiagnosisEvent) -> DiagnosisInput:
    return DiagnosisInput(
        tenant_id="tenant-a",
        student_ref="student-a",
        period=Period(from_date=date(2026, 6, 1), to_date=date(2026, 9, 30)),
        as_of=datetime(2026, 10, 1, tzinfo=UTC),
        snapshot_hash="sha256:time-series",
        events=events,
    )


def _vocabulary(
    *,
    bucket_min_items: int = 1,
    baseline_window_weeks: int = 6,
) -> ReportTimeSeriesVocabulary:
    return load_report_time_series_vocabulary().model_copy(
        update={
            "bucket_min_items": bucket_min_items,
            "baseline_window_weeks": baseline_window_weeks,
        }
    )


def _values(
    diagnosis_input: DiagnosisInput,
    *,
    vocabulary: ReportTimeSeriesVocabulary | None = None,
) -> dict[str, float]:
    metrics = build_time_series_metrics(
        diagnosis_input,
        vocabulary=vocabulary or _vocabulary(),
        timezone=SEOUL,
    )
    return {metric.metric_key: metric.value for metric in metrics}


def test_three_month_fixture_builds_three_month_buckets_and_week_buckets() -> None:
    events = tuple(
        _event(
            f"event-{month}-{index}",
            datetime(2026, month, 10 + index, 12, tzinfo=SEOUL),
            correct=index % 2 == 0,
        )
        for month in (6, 7, 8)
        for index in range(2)
    )
    vocabulary = _vocabulary()
    values = _values(_input(*events), vocabulary=vocabulary)

    assert [
        values[vocabulary.metric_key_for(TimeSeriesMetricKind.MONTHLY_GRADED_ITEMS, bucket=month)]
        for month in ("2026-06", "2026-07", "2026-08")
    ] == [2.0, 2.0, 2.0]
    assert [
        values[vocabulary.metric_key_for(TimeSeriesMetricKind.MONTHLY_ACCURACY, bucket=month)]
        for month in ("2026-06", "2026-07", "2026-08")
    ] == [0.5, 0.5, 0.5]
    weekly_count_keys = [
        key for key in values if ".weekly." in key and key.endswith("graded_items")
    ]
    assert len(weekly_count_keys) == 3


def test_month_and_week_boundaries_use_local_calendar_and_iso_monday() -> None:
    vocabulary = _vocabulary()
    diagnosis_input = _input(
        _event("sun", datetime(2026, 6, 28, 23, tzinfo=SEOUL)),
        _event("mon", datetime(2026, 6, 29, 1, tzinfo=SEOUL)),
        _event("wed", datetime(2026, 7, 1, 1, tzinfo=SEOUL)),
    )
    values = _values(diagnosis_input, vocabulary=vocabulary)

    assert values[
        vocabulary.metric_key_for(TimeSeriesMetricKind.MONTHLY_GRADED_ITEMS, bucket="2026-06")
    ] == 2.0
    assert values[
        vocabulary.metric_key_for(TimeSeriesMetricKind.MONTHLY_GRADED_ITEMS, bucket="2026-07")
    ] == 1.0
    assert values[
        vocabulary.metric_key_for(TimeSeriesMetricKind.WEEKLY_GRADED_ITEMS, bucket="2026-06-22")
    ] == 1.0
    assert values[
        vocabulary.metric_key_for(TimeSeriesMetricKind.WEEKLY_GRADED_ITEMS, bucket="2026-06-29")
    ] == 2.0


def test_week_boundary_matches_detection_feature_extraction() -> None:
    occurred_at = datetime(2026, 8, 30, 23, 30, tzinfo=SEOUL)
    vocabulary = _vocabulary()
    values = _values(_input(_event("diagnosis-event", occurred_at)), vocabulary=vocabulary)
    request = DetectRequest(
        snapshot_meta=SnapshotMeta(
            week_start="2026-08-24",
            snapshot_hash="sha256:detection-week",
            term_context=TermContext.NORMAL,
            classes=(ClassRef(class_ref="class-a"),),
        ),
        students=(
            StudentInput(
                student_ref="student-a",
                class_ref="class-a",
                enrolled_weeks=12,
                status=StudentStatus.ENROLLED,
                consent="granted",
            ),
        ),
        learning_events=(
            LearningEvent(
                record_id="learning-event",
                student_ref="student-a",
                type=EventType.SOLVE,
                occurred_at=occurred_at,
                correct=True,
                source=EventSource.TRACK_A,
            ),
        ),
    )
    detection_week = extract_features(request)["student-a"].weeks[0].week_monday.isoformat()

    assert (
        vocabulary.metric_key_for(TimeSeriesMetricKind.WEEKLY_GRADED_ITEMS, bucket=detection_week)
        in values
    )


def test_empty_and_out_of_period_buckets_create_no_metrics() -> None:
    empty = _input()
    outside = _event("outside", datetime(2026, 10, 1, tzinfo=SEOUL))

    assert _values(empty) == {}
    assert _values(_input(outside)) == {}


def test_thin_bucket_keeps_count_but_withholds_accuracy_and_baseline() -> None:
    vocabulary = _vocabulary(bucket_min_items=3)
    diagnosis_input = _input(
        _event("thin-1", datetime(2026, 8, 24, tzinfo=SEOUL)),
        _event("thin-2", datetime(2026, 8, 25, tzinfo=SEOUL), correct=False),
    )
    values = _values(diagnosis_input, vocabulary=vocabulary)

    assert values[
        vocabulary.metric_key_for(TimeSeriesMetricKind.WEEKLY_GRADED_ITEMS, bucket="2026-08-24")
    ] == 2.0
    assert (
        vocabulary.metric_key_for(TimeSeriesMetricKind.WEEKLY_ACCURACY, bucket="2026-08-24")
        not in values
    )
    assert vocabulary.metric_key_for(TimeSeriesMetricKind.BASELINE_ACCURACY) not in values


def test_recent_baseline_uses_only_latest_six_eligible_weeks() -> None:
    vocabulary = _vocabulary(bucket_min_items=2, baseline_window_weeks=6)
    start = datetime(2026, 7, 6, 12, tzinfo=SEOUL)
    events = tuple(
        _event(
            f"week-{week}-{index}",
            start + timedelta(weeks=week, days=index),
            correct=week > 0,
        )
        for week in range(7)
        for index in range(2)
    )
    values = _values(_input(*events), vocabulary=vocabulary)

    assert values[vocabulary.metric_key_for(TimeSeriesMetricKind.BASELINE_ACCURACY)] == 1.0
    assert values[
        vocabulary.metric_key_for(TimeSeriesMetricKind.BASELINE_GRADED_ITEMS_PER_WEEK)
    ] == 2.0
    assert values[vocabulary.metric_key_for(TimeSeriesMetricKind.BASELINE_WEEKS_USED)] == 6.0


def test_same_input_is_byte_stable_and_every_metric_has_evidence() -> None:
    diagnosis_input = _input(
        _event("a", datetime(2026, 8, 24, tzinfo=SEOUL)),
        _event("b", datetime(2026, 8, 25, tzinfo=SEOUL), correct=False),
    )
    vocabulary = _vocabulary()

    first = build_time_series_metrics(
        diagnosis_input,
        vocabulary=vocabulary,
        timezone=SEOUL,
    )
    second = build_time_series_metrics(
        diagnosis_input,
        vocabulary=vocabulary,
        timezone=SEOUL,
    )

    assert [metric.model_dump_json() for metric in first] == [
        metric.model_dump_json() for metric in second
    ]
    assert all(metric.evidence for metric in first)


def test_naive_event_time_fails_closed() -> None:
    invalid = _event("naive", datetime(2026, 8, 24))

    with pytest.raises(ReportTimeSeriesError, match="timezone-aware"):
        _values(_input(invalid))
