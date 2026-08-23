"""리포트 스튜디오 결정론 조립기의 필터·집계 경계."""

from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from ai.contracts.diagnosis import (
    CellVerdict,
    MisconceptionReport,
    WeaknessCell,
    WeaknessMap,
)
from ai.contracts.problem_generation import DifficultyBand, ItemResult, ProblemItemStatus
from ai.contracts.report import (
    DifficultyDistributionPayload,
    MisconceptionFrequencyPayload,
    ReportAudience,
    ReportDataBlock,
    ReportDataBlockKind,
    ReportEvidenceRef,
    ReportMetricInput,
    ReportStudioData,
    ReportValueStatus,
    WeaknessGridPayload,
)
from ai.contracts.taxonomy import TypeTag
from ai.diagnosis.config import load_diagnosis_config
from ai.report.assembler import (
    ReportAssemblyError,
    assemble_report_studio_data,
    filter_report_metrics,
)


def _evidence(record_id: str = "metric-1") -> tuple[ReportEvidenceRef, ...]:
    return (
        ReportEvidenceRef(
            source_table="benchmarks",
            record_id=record_id,
            summary="리포트 입력 수치",
        ),
    )


def _weakness_map(*, include_apply: bool = False) -> WeaknessMap:
    cells = {
        "language×concept": WeaknessCell(
            acc=0.9,
            n=10,
            verdict=CellVerdict.OK,
        ),
        "reading×fact": WeaknessCell(
            acc=0.4,
            n=10,
            verdict=CellVerdict.WEAK,
            severity=0.6,
        ),
    }
    if include_apply:
        cells["literature×apply"] = WeaknessCell(
            acc=0.2,
            n=3,
            verdict=CellVerdict.WEAK,
            severity=0.8,
        )
    return WeaknessMap(
        graph_version="graph-v1",
        taxonomy_version="v1",
        config_version="config-v1",
        snapshot_hash="sha256:report-studio",
        cells=cells,
    )


def _item(item_id: str, band: DifficultyBand) -> ItemResult:
    return ItemResult(
        item_id=UUID(item_id),
        status=ProblemItemStatus.VERIFIED,
        attempt_no=1,
        difficulty_band=band,
    )


def _assemble(*, include_apply: bool = False) -> ReportStudioData:
    return assemble_report_studio_data(
        _weakness_map(include_apply=include_apply),
        MisconceptionReport(
            by_area={"reading": {"scope_confusion": 2}},
            by_node={"read.root": {"scope_confusion": 1}},
            excluded_missing_chosen_no=1,
            excluded_missing_misconception_tag=3,
        ),
        (
            _item("00000000-0000-4000-8000-000000000301", DifficultyBand.LOW),
            _item("00000000-0000-4000-8000-000000000302", DifficultyBand.HIGH),
        ),
        cell_min_items=load_diagnosis_config().params.cell_min_items,
        audience=ReportAudience.GUARDIAN,
    )


def test_guardian_assembly_physically_excludes_teacher_only_metric() -> None:
    guardian = ReportMetricInput(
        metric_key="national_percentile",
        value=71,
        audience=ReportAudience.GUARDIAN,
        evidence=_evidence("national-1"),
    )
    teacher_only = ReportMetricInput(
        metric_key="class_average",
        value=63,
        audience=ReportAudience.TEACHER_ONLY,
        evidence=_evidence("class-average-1"),
    )

    assembled = filter_report_metrics(
        (guardian, teacher_only),
        audience=ReportAudience.GUARDIAN,
    )

    assert assembled == (guardian,)
    assert all(metric.value != 63 for metric in assembled)


def test_assembly_path_filters_guardian_and_preserves_teacher_only_metrics() -> None:
    teacher_only = ReportMetricInput(
        metric_key="class_average",
        value=63,
        audience=ReportAudience.TEACHER_ONLY,
        evidence=_evidence("class-average-path-1"),
    )

    guardian_result = assemble_report_studio_data(
        _weakness_map(),
        MisconceptionReport(),
        (_item("00000000-0000-4000-8000-000000000304", DifficultyBand.MEDIUM),),
        cell_min_items=load_diagnosis_config().params.cell_min_items,
        audience=ReportAudience.GUARDIAN,
        metrics=(teacher_only,),
    )
    teacher_result = assemble_report_studio_data(
        _weakness_map(),
        MisconceptionReport(),
        (_item("00000000-0000-4000-8000-000000000305", DifficultyBand.MEDIUM),),
        cell_min_items=load_diagnosis_config().params.cell_min_items,
        audience=ReportAudience.TEACHER_ONLY,
        metrics=(teacher_only,),
    )

    assert guardian_result.metrics == ()
    assert teacher_result.metrics == (teacher_only,)


def test_assembler_fills_six_grounded_blocks_deterministically() -> None:
    first = _assemble()
    second = _assemble()

    assert first.model_dump_json() == second.model_dump_json()
    assert tuple(block.kind for block in first.blocks) == tuple(ReportDataBlockKind)
    assert all(block.numbers_used for block in first.blocks)
    assert all(number.evidence for block in first.blocks for number in block.numbers_used)

    difficulty = first.blocks[4].payload
    assert isinstance(difficulty, DifficultyDistributionPayload)
    assert difficulty.counts == {
        DifficultyBand.LOW: 1,
        DifficultyBand.MEDIUM: 0,
        DifficultyBand.HIGH: 1,
    }
    misconception = first.blocks[3].payload
    assert isinstance(misconception, MisconceptionFrequencyPayload)
    assert misconception.by_area[0].count == 2
    assert misconception.excluded_missing_misconception_tag == 3


def test_sample_below_canonical_minimum_is_pending() -> None:
    minimum = load_diagnosis_config().params.cell_min_items
    weakness_map = _weakness_map().model_copy(
        update={
            "cells": {
                "language×concept": WeaknessCell(
                    acc=0.0,
                    n=minimum - 1,
                    verdict=CellVerdict.WEAK,
                    severity=1.0,
                )
            }
        }
    )

    assembled = assemble_report_studio_data(
        weakness_map,
        MisconceptionReport(),
        (_item("00000000-0000-4000-8000-000000000303", DifficultyBand.MEDIUM),),
        cell_min_items=minimum,
        audience=ReportAudience.GUARDIAN,
    )

    grid = assembled.blocks[0].payload
    assert isinstance(grid, WeaknessGridPayload)
    cell = next(cell for cell in grid.cells if cell.type_tag is TypeTag.CONCEPT)
    assert cell.verdict is CellVerdict.UNKNOWN
    assert cell.severity is None


def test_assembler_fails_when_difficulty_numbers_have_no_evidence() -> None:
    with pytest.raises(ReportAssemblyError, match="evidence"):
        assemble_report_studio_data(
            _weakness_map(),
            MisconceptionReport(),
            (),
            cell_min_items=load_diagnosis_config().params.cell_min_items,
            audience=ReportAudience.GUARDIAN,
        )

    with pytest.raises(ValidationError, match="numbers_used"):
        valid = _assemble().blocks[0]
        ReportDataBlock(
            kind=valid.kind,
            payload=valid.payload,
            numbers_used=(),
        )


def test_apply_input_remains_visible_as_not_produced_without_a_verdict() -> None:
    assembled = _assemble(include_apply=True)
    grid = assembled.blocks[0].payload
    assert isinstance(grid, WeaknessGridPayload)
    apply_cell = next(
        cell
        for cell in grid.cells
        if cell.type_tag is TypeTag.APPLY and cell.area_tag.value == "literature"
    )

    assert apply_cell.status is ReportValueStatus.NOT_PRODUCED
    assert apply_cell.n == 3
    assert apply_cell.acc is None
    assert apply_cell.verdict is None
