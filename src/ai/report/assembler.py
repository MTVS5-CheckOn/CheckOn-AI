"""리포트 스튜디오 데이터의 결정론 조립기 — LLM·I/O 없음."""

from __future__ import annotations

from math import fsum

from ai.contracts.diagnosis import CellVerdict, MisconceptionReport, WeaknessCell, WeaknessMap
from ai.contracts.problem_generation import DifficultyBand, ItemResult
from ai.contracts.report import (
    AreaAchievementPayload,
    DifficultyDistributionPayload,
    ItemCountsPayload,
    MisconceptionFrequencyPayload,
    ReportAreaAchievementData,
    ReportAudience,
    ReportDataBlock,
    ReportDataBlockKind,
    ReportEvidenceRef,
    ReportGridCellData,
    ReportMetricInput,
    ReportMisconceptionEntry,
    ReportNumber,
    ReportRepresentativeTypeData,
    ReportStudioData,
    ReportUnproducedMetric,
    ReportValueStatus,
    RepresentativeTypesPayload,
    WeaknessGridPayload,
)
from ai.contracts.taxonomy import AreaTag, TypeTag


class ReportAssemblyError(ValueError):
    """입력 계약만으로 근거 있는 리포트 블록을 만들 수 없음."""


def filter_report_metrics(
    metrics: tuple[ReportMetricInput, ...],
    *,
    audience: ReportAudience,
) -> tuple[ReportMetricInput, ...]:
    """guardian 조립 전에 teacher_only 항목을 물리적으로 제거한다."""

    if audience is ReportAudience.TEACHER_ONLY:
        return metrics
    return tuple(metric for metric in metrics if metric.audience is ReportAudience.GUARDIAN)


def assemble_report_studio_data(
    weakness_map: WeaknessMap,
    misconceptions: MisconceptionReport,
    item_results: tuple[ItemResult, ...],
    *,
    cell_min_items: int,
) -> ReportStudioData:
    """이미 확정된 진단·출제 값만 접어 여섯 데이터 블록을 만든다."""

    if cell_min_items < 1:
        raise ReportAssemblyError("cell_min_items는 1 이상이어야 한다")

    weakness_evidence = (
        ReportEvidenceRef(
            source_table="weakness_map",
            record_id=weakness_map.snapshot_hash,
            summary="진단 영역×유형 집계와 오개념 집계의 입력 스냅숏",
        ),
    )
    parsed_cells = _parse_cells(weakness_map.cells)
    grid = _build_grid(parsed_cells, cell_min_items=cell_min_items)
    representatives = _build_representatives(
        parsed_cells,
        cell_min_items=cell_min_items,
    )
    achievements = _build_area_achievements(parsed_cells)
    misconception_payload = _build_misconceptions(misconceptions)
    item_evidence = _item_evidence(item_results)
    difficulty = _build_difficulty_distribution(item_results)

    blocks = (
        ReportDataBlock(
            kind=ReportDataBlockKind.WEAKNESS_GRID,
            payload=WeaknessGridPayload(cell_min_items=cell_min_items, cells=grid),
            numbers_used=_grid_numbers(grid, weakness_evidence),
        ),
        ReportDataBlock(
            kind=ReportDataBlockKind.REPRESENTATIVE_TYPES,
            payload=representatives,
            numbers_used=_representative_numbers(representatives, weakness_evidence),
        ),
        ReportDataBlock(
            kind=ReportDataBlockKind.AREA_ACHIEVEMENT,
            payload=achievements,
            numbers_used=_achievement_numbers(achievements, weakness_evidence),
        ),
        ReportDataBlock(
            kind=ReportDataBlockKind.MISCONCEPTION_FREQUENCY,
            payload=misconception_payload,
            numbers_used=_misconception_numbers(misconception_payload, weakness_evidence),
        ),
        ReportDataBlock(
            kind=ReportDataBlockKind.DIFFICULTY_DISTRIBUTION,
            payload=difficulty,
            numbers_used=tuple(
                _number(f"difficulty.{band.value}", difficulty.counts[band], item_evidence)
                for band in DifficultyBand
            ),
        ),
        ReportDataBlock(
            kind=ReportDataBlockKind.ITEM_COUNTS,
            payload=_build_item_counts(parsed_cells, cell_min_items=cell_min_items),
            numbers_used=_item_count_numbers(
                parsed_cells,
                cell_min_items=cell_min_items,
                evidence=weakness_evidence,
            ),
        ),
    )
    return ReportStudioData(
        blocks=blocks,
        unproduced=tuple(ReportUnproducedMetric),
    )


def _parse_cells(
    cells: dict[str, WeaknessCell],
) -> dict[tuple[AreaTag, TypeTag], WeaknessCell]:
    parsed: dict[tuple[AreaTag, TypeTag], WeaknessCell] = {}
    for key, cell in cells.items():
        parts = key.split("×")
        if len(parts) != 2:
            raise ReportAssemblyError(f"영역×유형 셀 키 형식이 아니다: {key}")
        try:
            coordinate = (AreaTag(parts[0]), TypeTag(parts[1]))
        except ValueError as error:
            raise ReportAssemblyError(f"등록되지 않은 영역×유형 셀 키다: {key}") from error
        if coordinate in parsed:
            raise ReportAssemblyError(f"영역×유형 셀 좌표가 중복됐다: {key}")
        parsed[coordinate] = cell
    return parsed


def _build_grid(
    cells: dict[tuple[AreaTag, TypeTag], WeaknessCell],
    *,
    cell_min_items: int,
) -> tuple[ReportGridCellData, ...]:
    result: list[ReportGridCellData] = []
    for area_tag in AreaTag:
        for type_tag in TypeTag:
            cell = cells.get((area_tag, type_tag))
            if type_tag is TypeTag.APPLY:
                result.append(
                    ReportGridCellData(
                        area_tag=area_tag,
                        type_tag=type_tag,
                        status=ReportValueStatus.NOT_PRODUCED,
                        n=0 if cell is None else cell.n,
                    )
                )
                continue
            if cell is None or cell.n == 0:
                result.append(
                    ReportGridCellData(
                        area_tag=area_tag,
                        type_tag=type_tag,
                        status=ReportValueStatus.NO_DATA,
                        n=0,
                    )
                )
                continue
            verdict = CellVerdict.UNKNOWN if cell.n < cell_min_items else cell.verdict
            result.append(
                ReportGridCellData(
                    area_tag=area_tag,
                    type_tag=type_tag,
                    status=ReportValueStatus.AVAILABLE,
                    acc=cell.acc,
                    n=cell.n,
                    verdict=verdict,
                    severity=cell.severity if verdict is CellVerdict.WEAK else None,
                )
            )
    return tuple(result)


def _build_representatives(
    cells: dict[tuple[AreaTag, TypeTag], WeaknessCell],
    *,
    cell_min_items: int,
) -> RepresentativeTypesPayload:
    area_order = {area: index for index, area in enumerate(AreaTag)}
    type_order = {type_tag: index for index, type_tag in enumerate(TypeTag)}
    eligible = [
        (coordinate, cell)
        for coordinate, cell in cells.items()
        if coordinate[1] is not TypeTag.APPLY and cell.n >= cell_min_items
    ]
    strengths = sorted(
        (pair for pair in eligible if pair[1].verdict is CellVerdict.OK),
        key=lambda pair: (
            -pair[1].acc,
            -pair[1].n,
            area_order[pair[0][0]],
            type_order[pair[0][1]],
        ),
    )
    weaknesses = sorted(
        (pair for pair in eligible if pair[1].verdict is CellVerdict.WEAK),
        key=lambda pair: (
            -(pair[1].severity or 0.0),
            pair[1].acc,
            -pair[1].n,
            area_order[pair[0][0]],
            type_order[pair[0][1]],
        ),
    )
    return RepresentativeTypesPayload(
        strength=_representative(strengths[0]) if strengths else None,
        weakness=_representative(weaknesses[0]) if weaknesses else None,
    )


def _representative(
    pair: tuple[tuple[AreaTag, TypeTag], WeaknessCell],
) -> ReportRepresentativeTypeData:
    (area_tag, type_tag), cell = pair
    return ReportRepresentativeTypeData(
        area_tag=area_tag,
        type_tag=type_tag,
        acc=cell.acc,
        n=cell.n,
        severity=cell.severity,
    )


def _build_area_achievements(
    cells: dict[tuple[AreaTag, TypeTag], WeaknessCell],
) -> AreaAchievementPayload:
    areas: list[ReportAreaAchievementData] = []
    total_n = 0
    weighted_total = 0.0
    for area_tag in AreaTag:
        observed = [
            cell
            for (cell_area, type_tag), cell in cells.items()
            if cell_area is area_tag and type_tag is not TypeTag.APPLY
        ]
        n = sum(cell.n for cell in observed)
        if n == 0:
            areas.append(
                ReportAreaAchievementData(
                    area_tag=area_tag,
                    status=ReportValueStatus.NO_DATA,
                    n=0,
                )
            )
            continue
        weighted = fsum(cell.acc * cell.n for cell in observed)
        acc = weighted / n
        areas.append(
            ReportAreaAchievementData(
                area_tag=area_tag,
                status=ReportValueStatus.AVAILABLE,
                acc=acc,
                n=n,
            )
        )
        total_n += n
        weighted_total += weighted
    return AreaAchievementPayload(
        areas=tuple(areas),
        own_average=None if total_n == 0 else weighted_total / total_n,
    )


def _build_misconceptions(report: MisconceptionReport) -> MisconceptionFrequencyPayload:
    return MisconceptionFrequencyPayload(
        by_area=_misconception_entries(report.by_area),
        by_node=_misconception_entries(report.by_node),
        excluded_missing_chosen_no=report.excluded_missing_chosen_no,
        excluded_missing_misconception_tag=report.excluded_missing_misconception_tag,
    )


def _misconception_entries(
    groups: dict[str, dict[str, int]],
) -> tuple[ReportMisconceptionEntry, ...]:
    return tuple(
        ReportMisconceptionEntry(
            group_key=group_key,
            misconception_tag=tag,
            count=count,
        )
        for group_key, tags in sorted(groups.items())
        for tag, count in sorted(tags.items(), key=lambda item: (-item[1], item[0]))
    )


def _item_evidence(item_results: tuple[ItemResult, ...]) -> tuple[ReportEvidenceRef, ...]:
    evidence = tuple(
        ReportEvidenceRef(
            source_table="item_result",
            record_id=str(result.item_id),
            summary="출제 문항 최종 결과와 난이도 밴드",
        )
        for result in item_results
        if result.item_id is not None
    )
    if not evidence:
        raise ReportAssemblyError("난이도 분포 numbers_used에 연결할 item_result evidence가 없다")
    return evidence


def _build_difficulty_distribution(
    item_results: tuple[ItemResult, ...],
) -> DifficultyDistributionPayload:
    return DifficultyDistributionPayload(
        counts={
            band: sum(result.difficulty_band is band for result in item_results)
            for band in DifficultyBand
        }
    )


def _build_item_counts(
    cells: dict[tuple[AreaTag, TypeTag], WeaknessCell],
    *,
    cell_min_items: int,
) -> ItemCountsPayload:
    scored = sum(cell.n for cell in cells.values())
    judged = sum(
        cell.n
        for (_, type_tag), cell in cells.items()
        if type_tag is not TypeTag.APPLY
        and cell.n >= cell_min_items
        and cell.verdict is not CellVerdict.UNKNOWN
    )
    return ItemCountsPayload(scored_items=scored, judged_items=judged)


def _number(
    name: str,
    value: int | float,
    evidence: tuple[ReportEvidenceRef, ...],
) -> ReportNumber:
    return ReportNumber(name=name, value=value, evidence=evidence)


def _grid_numbers(
    grid: tuple[ReportGridCellData, ...],
    evidence: tuple[ReportEvidenceRef, ...],
) -> tuple[ReportNumber, ...]:
    numbers: list[ReportNumber] = []
    for cell in grid:
        prefix = f"grid.{cell.area_tag.value}.{cell.type_tag.value}"
        numbers.append(_number(f"{prefix}.n", cell.n, evidence))
        if cell.acc is not None:
            numbers.append(_number(f"{prefix}.acc", cell.acc, evidence))
        if cell.severity is not None:
            numbers.append(_number(f"{prefix}.severity", cell.severity, evidence))
    return tuple(numbers)


def _representative_numbers(
    payload: RepresentativeTypesPayload,
    evidence: tuple[ReportEvidenceRef, ...],
) -> tuple[ReportNumber, ...]:
    numbers: list[ReportNumber] = []
    for name, value in (("strength", payload.strength), ("weakness", payload.weakness)):
        if value is None:
            numbers.append(_number(f"representative.{name}.present", 0, evidence))
            continue
        numbers.extend(
            (
                _number(f"representative.{name}.acc", value.acc, evidence),
                _number(f"representative.{name}.n", value.n, evidence),
            )
        )
        if value.severity is not None:
            numbers.append(_number(f"representative.{name}.severity", value.severity, evidence))
    return tuple(numbers)


def _achievement_numbers(
    payload: AreaAchievementPayload,
    evidence: tuple[ReportEvidenceRef, ...],
) -> tuple[ReportNumber, ...]:
    numbers: list[ReportNumber] = []
    for area in payload.areas:
        numbers.append(_number(f"area.{area.area_tag.value}.n", area.n, evidence))
        if area.acc is not None:
            numbers.append(_number(f"area.{area.area_tag.value}.acc", area.acc, evidence))
    if payload.own_average is not None:
        numbers.append(_number("area.own_average", payload.own_average, evidence))
    return tuple(numbers)


def _misconception_numbers(
    payload: MisconceptionFrequencyPayload,
    evidence: tuple[ReportEvidenceRef, ...],
) -> tuple[ReportNumber, ...]:
    numbers = [
        _number(
            f"misconception.area.{entry.group_key}.{entry.misconception_tag}",
            entry.count,
            evidence,
        )
        for entry in payload.by_area
    ]
    numbers.extend(
        _number(
            f"misconception.node.{entry.group_key}.{entry.misconception_tag}",
            entry.count,
            evidence,
        )
        for entry in payload.by_node
    )
    numbers.extend(
        (
            _number(
                "misconception.excluded_missing_chosen_no",
                payload.excluded_missing_chosen_no,
                evidence,
            ),
            _number(
                "misconception.excluded_missing_misconception_tag",
                payload.excluded_missing_misconception_tag,
                evidence,
            ),
        )
    )
    return tuple(numbers)


def _item_count_numbers(
    cells: dict[tuple[AreaTag, TypeTag], WeaknessCell],
    *,
    cell_min_items: int,
    evidence: tuple[ReportEvidenceRef, ...],
) -> tuple[ReportNumber, ...]:
    payload = _build_item_counts(cells, cell_min_items=cell_min_items)
    return (
        _number("items.scored", payload.scored_items, evidence),
        _number("items.judged", payload.judged_items, evidence),
    )


__all__ = [
    "ReportAssemblyError",
    "assemble_report_studio_data",
    "filter_report_metrics",
]
