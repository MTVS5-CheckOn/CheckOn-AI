"""리포트 블록 닫힌 어휘의 엄격 YAML 로더."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ai.contracts.report import ReportBlockKind, ReportUnproducedMetric
from ai.contracts.taxonomy import AreaTag, TypeTag

DEFAULT_BLOCK_KINDS_PATH = Path(__file__).resolve().parent / "data" / "block_kinds.yaml"
DEFAULT_UNPRODUCED_METRICS_PATH = (
    Path(__file__).resolve().parent / "data" / "unproduced_metrics.yaml"
)
DEFAULT_ROOT_CAUSE_METRICS_PATH = (
    Path(__file__).resolve().parent / "data" / "root_cause_metrics.yaml"
)
DEFAULT_TIME_SERIES_METRICS_PATH = (
    Path(__file__).resolve().parent / "data" / "time_series_metrics.yaml"
)


class RootCauseMetricKind(StrEnum):
    """근본 원인 metric_key의 닫힌 종류."""

    CONFIRMED = "confirmed"
    SUSPECT = "suspect"
    PROPAGATED = "propagated"


class TimeSeriesMetricKind(StrEnum):
    """시계열 metric_key의 닫힌 종류."""

    MONTHLY_ACCURACY = "monthly_accuracy"
    MONTHLY_GRADED_ITEMS = "monthly_graded_items"
    WEEKLY_ACCURACY = "weekly_accuracy"
    WEEKLY_GRADED_ITEMS = "weekly_graded_items"
    BASELINE_ACCURACY = "baseline_accuracy"
    BASELINE_GRADED_ITEMS_PER_WEEK = "baseline_graded_items_per_week"
    BASELINE_WEEKS_USED = "baseline_weeks_used"


class ReportVocabularyError(ValueError):
    """리포트 블록 어휘를 읽거나 검증할 수 없음."""


class ReportBlockVocabulary(BaseModel):
    """버전이 고정된 리포트 블록 어휘 문서."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["report-block-kinds.v1"]
    block_kinds: tuple[ReportBlockKind, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_block_kinds(self) -> Self:
        if len(set(self.block_kinds)) != len(self.block_kinds):
            raise ValueError("리포트 블록 어휘는 중복될 수 없다")
        expected = set(ReportBlockKind)
        found = set(self.block_kinds)
        if found != expected:
            missing = sorted(kind.value for kind in expected - found)
            unknown = sorted(kind.value for kind in found - expected)
            raise ValueError(f"리포트 블록 어휘가 완전하지 않다: 누락={missing} 미등록={unknown}")
        return self


class ReportUnproducedReason(BaseModel):
    """미산출 축 하나를 비워 두는 근거."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str = Field(min_length=1)


class ReportUnproducedVocabulary(BaseModel):
    """버전이 고정된 미산출 축·사유 문서."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["report-unproduced-metrics.v1"]
    unproduced_metrics: dict[str, ReportUnproducedReason] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unproduced_metrics(self) -> Self:
        expected = {metric.value for metric in ReportUnproducedMetric}
        found = set(self.unproduced_metrics)
        if found != expected:
            missing = sorted(expected - found)
            unknown = sorted(found - expected)
            raise ValueError(f"리포트 미산출 어휘가 완전하지 않다: 누락={missing} 미등록={unknown}")
        return self

    def reason_for(self, metric: ReportUnproducedMetric) -> str:
        """계약 enum 축의 미산출 사유를 반환한다."""

        return self.unproduced_metrics[metric.value].reason


class ReportRootCauseVocabulary(BaseModel):
    """근본 원인 키와 학부모 표시명을 한 자리에 둔 버전 어휘."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["report-root-cause-metrics.v1"]
    metric_keys: dict[str, str] = Field(min_length=1)
    area_labels: dict[str, str] = Field(min_length=1)
    type_labels: dict[str, str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_coverage(self) -> Self:
        fields = (
            ("metric_keys", set(self.metric_keys), {kind.value for kind in RootCauseMetricKind}),
            ("area_labels", set(self.area_labels), {area.value for area in AreaTag}),
            ("type_labels", set(self.type_labels), {type_tag.value for type_tag in TypeTag}),
        )
        for field_name, found, expected in fields:
            if found != expected:
                missing = sorted(expected - found)
                unknown = sorted(found - expected)
                raise ValueError(
                    f"리포트 근본 원인 어휘가 완전하지 않다: "
                    f"필드={field_name} 누락={missing} 미등록={unknown}"
                )
        values = (
            *self.metric_keys.values(),
            *self.area_labels.values(),
            *self.type_labels.values(),
        )
        if any(not value for value in values):
            raise ValueError("리포트 근본 원인 어휘 값은 비어 있을 수 없다")
        if len(set(self.metric_keys.values())) != len(self.metric_keys):
            raise ValueError("리포트 근본 원인 metric_key는 중복될 수 없다")
        return self

    def metric_key_for(self, kind: RootCauseMetricKind) -> str:
        return self.metric_keys[kind.value]

    def area_label_for(self, area: AreaTag) -> str:
        return self.area_labels[area.value]

    def type_label_for(self, type_tag: TypeTag) -> str:
        return self.type_labels[type_tag.value]


class ReportTimeSeriesVocabulary(BaseModel):
    """시계열 키와 버킷·기준선 정책의 버전 정본."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["report-time-series-metrics.v1"]
    timezone_name: str = Field(min_length=1)
    bucket_min_items: int = Field(ge=1)
    baseline_window_weeks: int = Field(ge=1)
    metric_keys: dict[str, str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        expected = {kind.value for kind in TimeSeriesMetricKind}
        found = set(self.metric_keys)
        if found != expected:
            missing = sorted(expected - found)
            unknown = sorted(found - expected)
            raise ValueError(
                f"리포트 시계열 어휘가 완전하지 않다: 누락={missing} 미등록={unknown}"
            )
        if len(set(self.metric_keys.values())) != len(self.metric_keys):
            raise ValueError("리포트 시계열 metric_key는 중복될 수 없다")
        bucketed = {
            TimeSeriesMetricKind.MONTHLY_ACCURACY,
            TimeSeriesMetricKind.MONTHLY_GRADED_ITEMS,
            TimeSeriesMetricKind.WEEKLY_ACCURACY,
            TimeSeriesMetricKind.WEEKLY_GRADED_ITEMS,
        }
        for kind in TimeSeriesMetricKind:
            template = self.metric_keys[kind.value]
            expected_placeholders = 1 if kind in bucketed else 0
            if template.count("{bucket}") != expected_placeholders:
                raise ValueError(f"시계열 metric_key 버킷 자리표시자가 잘못됐다: {kind.value}")
            try:
                template.format(bucket="2000-01-03")
            except (IndexError, KeyError, ValueError) as error:
                raise ValueError(f"시계열 metric_key 형식이 잘못됐다: {kind.value}") from error
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as error:
            raise ValueError(f"등록되지 않은 IANA 시간대다: {self.timezone_name}") from error
        return self

    def metric_key_for(
        self,
        kind: TimeSeriesMetricKind,
        *,
        bucket: str | None = None,
    ) -> str:
        """종류와 버킷을 버전 어휘의 metric_key로 바꾼다."""

        template = self.metric_keys[kind.value]
        if "{bucket}" in template:
            if bucket is None:
                raise ValueError(f"버킷 metric_key에는 bucket이 필요하다: {kind.value}")
            return template.format(bucket=bucket)
        if bucket is not None:
            raise ValueError(f"기준선 metric_key에는 bucket을 받을 수 없다: {kind.value}")
        return template


def load_report_block_vocabulary(
    path: Path = DEFAULT_BLOCK_KINDS_PATH,
) -> ReportBlockVocabulary:
    """어휘 파일을 읽어 5종 완전성·중복·추가 필드를 실패 닫힘으로 검증한다."""

    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ReportVocabularyError(f"리포트 블록 어휘 파일을 읽을 수 없다: {path}") from error
    except yaml.YAMLError as error:
        raise ReportVocabularyError(f"리포트 블록 어휘 YAML 오류: {error}") from error
    try:
        return ReportBlockVocabulary.model_validate(raw)
    except ValidationError as error:
        raise ReportVocabularyError(f"리포트 블록 어휘 스키마 오류: {error}") from error


def load_report_unproduced_vocabulary(
    path: Path = DEFAULT_UNPRODUCED_METRICS_PATH,
) -> ReportUnproducedVocabulary:
    """미산출 축 파일을 읽어 계약 enum 전체와의 일치를 실패 닫힘으로 검증한다."""

    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ReportVocabularyError(f"리포트 미산출 어휘 파일을 읽을 수 없다: {path}") from error
    except yaml.YAMLError as error:
        raise ReportVocabularyError(f"리포트 미산출 어휘 YAML 오류: {error}") from error
    try:
        return ReportUnproducedVocabulary.model_validate(raw)
    except ValidationError as error:
        raise ReportVocabularyError(f"리포트 미산출 어휘 스키마 오류: {error}") from error


def load_report_root_cause_vocabulary(
    path: Path = DEFAULT_ROOT_CAUSE_METRICS_PATH,
) -> ReportRootCauseVocabulary:
    """근본 원인 키·표시명이 계약 enum 전체를 덮는지 실패 닫힘으로 검증한다."""

    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ReportVocabularyError(f"리포트 근본 원인 어휘 파일을 읽을 수 없다: {path}") from error
    except yaml.YAMLError as error:
        raise ReportVocabularyError(f"리포트 근본 원인 어휘 YAML 오류: {error}") from error
    try:
        return ReportRootCauseVocabulary.model_validate(raw)
    except ValidationError as error:
        raise ReportVocabularyError(f"리포트 근본 원인 어휘 스키마 오류: {error}") from error


def load_report_time_series_vocabulary(
    path: Path = DEFAULT_TIME_SERIES_METRICS_PATH,
) -> ReportTimeSeriesVocabulary:
    """시계열 키·버킷 정책을 엄격히 읽고 종류 전수를 실패 닫힘으로 검증한다."""

    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ReportVocabularyError(f"리포트 시계열 어휘 파일을 읽을 수 없다: {path}") from error
    except yaml.YAMLError as error:
        raise ReportVocabularyError(f"리포트 시계열 어휘 YAML 오류: {error}") from error
    try:
        return ReportTimeSeriesVocabulary.model_validate(raw)
    except ValidationError as error:
        raise ReportVocabularyError(f"리포트 시계열 어휘 스키마 오류: {error}") from error


@lru_cache
def default_report_block_vocabulary() -> ReportBlockVocabulary:
    """패키지 동봉 어휘를 프로세스당 한 번 검증한다."""

    return load_report_block_vocabulary()


@lru_cache
def default_report_unproduced_vocabulary() -> ReportUnproducedVocabulary:
    """패키지 동봉 미산출 어휘를 프로세스당 한 번 검증한다."""

    return load_report_unproduced_vocabulary()


@lru_cache
def default_report_root_cause_vocabulary() -> ReportRootCauseVocabulary:
    """패키지 동봉 근본 원인 어휘를 프로세스당 한 번 검증한다."""

    return load_report_root_cause_vocabulary()


@lru_cache
def default_report_time_series_vocabulary() -> ReportTimeSeriesVocabulary:
    """패키지 동봉 시계열 어휘·정책을 프로세스당 한 번 검증한다."""

    return load_report_time_series_vocabulary()


__all__ = [
    "DEFAULT_BLOCK_KINDS_PATH",
    "DEFAULT_UNPRODUCED_METRICS_PATH",
    "DEFAULT_ROOT_CAUSE_METRICS_PATH",
    "DEFAULT_TIME_SERIES_METRICS_PATH",
    "ReportBlockVocabulary",
    "ReportUnproducedReason",
    "ReportUnproducedVocabulary",
    "ReportRootCauseVocabulary",
    "ReportTimeSeriesVocabulary",
    "ReportVocabularyError",
    "RootCauseMetricKind",
    "TimeSeriesMetricKind",
    "default_report_block_vocabulary",
    "default_report_unproduced_vocabulary",
    "default_report_root_cause_vocabulary",
    "default_report_time_series_vocabulary",
    "load_report_block_vocabulary",
    "load_report_unproduced_vocabulary",
    "load_report_root_cause_vocabulary",
    "load_report_time_series_vocabulary",
]
