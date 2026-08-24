"""진단 이벤트를 학부모 리포트의 월·주 시계열 지표로 접는 순수 함수."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import fmean
from zoneinfo import ZoneInfo

from ai.contracts.diagnosis import DiagnosisEvent, DiagnosisInput, Period
from ai.contracts.report import (
    ReportAudience,
    ReportEvidenceRef,
    ReportMetricInput,
)
from ai.report.vocabulary import (
    ReportTimeSeriesVocabulary,
    TimeSeriesMetricKind,
    default_report_time_series_vocabulary,
)


class ReportTimeSeriesError(ValueError):
    """진단 이벤트 시각을 결정론 버킷으로 바꿀 수 없음."""


@dataclass(frozen=True)
class _Bucket:
    key: str
    events: tuple[DiagnosisEvent, ...]

    @property
    def accuracy(self) -> float:
        return sum(event.correct for event in self.events) / len(self.events)


def build_time_series_metrics(
    diagnosis_input: DiagnosisInput,
    *,
    vocabulary: ReportTimeSeriesVocabulary,
    timezone: ZoneInfo,
) -> tuple[ReportMetricInput, ...]:
    """기간 안의 채점 이벤트로 월·주 추이와 최근 6주 개인 기준선을 만든다."""

    return build_time_series_metrics_from_events(
        diagnosis_input.events,
        period=diagnosis_input.period,
        vocabulary=vocabulary,
        timezone=timezone,
    )


def build_time_series_metrics_from_events(
    events: tuple[DiagnosisEvent, ...],
    *,
    period: Period,
    vocabulary: ReportTimeSeriesVocabulary,
    timezone: ZoneInfo,
) -> tuple[ReportMetricInput, ...]:
    """스냅숏의 최소 시계열 입력을 월·주 지표로 접는다."""

    monthly: dict[str, list[DiagnosisEvent]] = defaultdict(list)
    weekly: dict[str, list[DiagnosisEvent]] = defaultdict(list)
    for event in events:
        local_day = _local_day(event, timezone)
        if not period.from_date <= local_day <= period.to_date:
            continue
        monthly[local_day.strftime("%Y-%m")].append(event)
        weekly[_monday_of(local_day).isoformat()].append(event)

    monthly_buckets = _ordered_buckets(monthly)
    weekly_buckets = _ordered_buckets(weekly)
    metrics = [
        *_bucket_metrics(
            monthly_buckets,
            count_kind=TimeSeriesMetricKind.MONTHLY_GRADED_ITEMS,
            accuracy_kind=TimeSeriesMetricKind.MONTHLY_ACCURACY,
            vocabulary=vocabulary,
        ),
        *_bucket_metrics(
            weekly_buckets,
            count_kind=TimeSeriesMetricKind.WEEKLY_GRADED_ITEMS,
            accuracy_kind=TimeSeriesMetricKind.WEEKLY_ACCURACY,
            vocabulary=vocabulary,
        ),
        *_baseline_metrics(weekly_buckets, vocabulary=vocabulary),
    ]
    return tuple(metrics)


def build_default_time_series_metrics(
    diagnosis_input: DiagnosisInput,
) -> tuple[ReportMetricInput, ...]:
    """검증·캐시된 기본 시계열 어휘와 IANA 시간대를 주입한다."""

    vocabulary = default_report_time_series_vocabulary()
    return build_time_series_metrics(
        diagnosis_input,
        vocabulary=vocabulary,
        timezone=ZoneInfo(vocabulary.timezone_name),
    )


def build_default_time_series_metrics_from_events(
    events: tuple[DiagnosisEvent, ...],
    *,
    period: Period,
) -> tuple[ReportMetricInput, ...]:
    """기본 어휘와 시간대로 최소 시계열 스냅숏을 접는다."""

    vocabulary = default_report_time_series_vocabulary()
    return build_time_series_metrics_from_events(
        events,
        period=period,
        vocabulary=vocabulary,
        timezone=ZoneInfo(vocabulary.timezone_name),
    )


def _local_day(event: DiagnosisEvent, timezone: ZoneInfo) -> date:
    if event.occurred_at.tzinfo is None or event.occurred_at.utcoffset() is None:
        raise ReportTimeSeriesError(f"occurred_at은 timezone-aware여야 한다: {event.event_id}")
    return event.occurred_at.astimezone(timezone).date()


def _monday_of(day: date) -> date:
    """detection/features.py와 같은 ISO 월요일 주 경계를 쓴다."""

    return day - timedelta(days=day.weekday())


def _ordered_buckets(source: dict[str, list[DiagnosisEvent]]) -> tuple[_Bucket, ...]:
    return tuple(
        _Bucket(
            key=key,
            events=tuple(
                sorted(source[key], key=lambda event: (event.occurred_at, event.event_id))
            ),
        )
        for key in sorted(source)
        if source[key]
    )


def _bucket_metrics(
    buckets: tuple[_Bucket, ...],
    *,
    count_kind: TimeSeriesMetricKind,
    accuracy_kind: TimeSeriesMetricKind,
    vocabulary: ReportTimeSeriesVocabulary,
) -> tuple[ReportMetricInput, ...]:
    metrics: list[ReportMetricInput] = []
    for bucket in buckets:
        evidence = _evidence(bucket.events, bucket=bucket.key)
        metrics.append(
            _metric(
                vocabulary.metric_key_for(count_kind, bucket=bucket.key),
                float(len(bucket.events)),
                evidence,
            )
        )
        if len(bucket.events) >= vocabulary.bucket_min_items:
            metrics.append(
                _metric(
                    vocabulary.metric_key_for(accuracy_kind, bucket=bucket.key),
                    bucket.accuracy,
                    evidence,
                )
            )
    return tuple(metrics)


def _baseline_metrics(
    weekly_buckets: tuple[_Bucket, ...],
    *,
    vocabulary: ReportTimeSeriesVocabulary,
) -> tuple[ReportMetricInput, ...]:
    window = weekly_buckets[-vocabulary.baseline_window_weeks :]
    eligible = tuple(
        bucket for bucket in window if len(bucket.events) >= vocabulary.bucket_min_items
    )
    if not eligible:
        return ()
    evidence = _evidence(
        tuple(event for bucket in eligible for event in bucket.events),
        bucket="recent-six-week-baseline",
    )
    return (
        _metric(
            vocabulary.metric_key_for(TimeSeriesMetricKind.BASELINE_ACCURACY),
            fmean(bucket.accuracy for bucket in eligible),
            evidence,
        ),
        _metric(
            vocabulary.metric_key_for(TimeSeriesMetricKind.BASELINE_GRADED_ITEMS_PER_WEEK),
            fmean(len(bucket.events) for bucket in eligible),
            evidence,
        ),
        _metric(
            vocabulary.metric_key_for(TimeSeriesMetricKind.BASELINE_WEEKS_USED),
            float(len(eligible)),
            evidence,
        ),
    )


def _evidence(
    events: tuple[DiagnosisEvent, ...],
    *,
    bucket: str,
) -> tuple[ReportEvidenceRef, ...]:
    return tuple(
        ReportEvidenceRef(
            source_table="learning_event",
            record_id=event.event_id,
            summary=f"{bucket} 채점 이벤트",
        )
        for event in events
    )


def _metric(
    metric_key: str,
    value: float,
    evidence: tuple[ReportEvidenceRef, ...],
) -> ReportMetricInput:
    return ReportMetricInput(
        metric_key=metric_key,
        value=value,
        audience=ReportAudience.GUARDIAN,
        evidence=evidence,
    )


__all__ = [
    "ReportTimeSeriesError",
    "build_default_time_series_metrics",
    "build_default_time_series_metrics_from_events",
    "build_time_series_metrics",
    "build_time_series_metrics_from_events",
]
