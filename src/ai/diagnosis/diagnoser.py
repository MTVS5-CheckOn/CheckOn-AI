"""결정론적 약점 진단 계산."""

from collections import deque
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from ai.contracts.diagnosis import (
    CellVerdict,
    DiagnosisEvent,
    DiagnosisInput,
    DiagnosisResult,
    DiagnosisStatus,
    NodeVerdict,
    PropagatedNode,
    WeaknessCell,
    WeaknessMap,
    WeaknessNode,
)
from ai.contracts.taxonomy import AreaTag, TypeTag
from ai.diagnosis.skill_graph import SkillGraph


class DiagnosisError(ValueError):
    """진단 입력이나 실행 버전이 일관되지 않음."""


class DiagnosisInputConflictError(DiagnosisError):
    """같은 event_id에 서로 다른 내용이 들어옴."""


class DiagnosisGraphReferenceError(DiagnosisError):
    """입력 이벤트가 현재 그래프에 없는 노드를 참조함."""


class DiagnosisVersionMismatchError(DiagnosisError):
    """주입된 실행 버전과 로드된 그래프 버전이 다름."""


class DiagnosisConfig(BaseModel):
    """버전 관리되는 진단 계산 설정."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cell_min_items: int = Field(ge=1)
    """A threshold 시트 R6의 cell_min_items 값."""

    relative_cut_pp: float = Field(lt=0.0)
    """B 기본값 시트 값. %p 단위 — 비율(acc)에 ×100 후 비교하며 직접 비교는 금지한다."""

    severity_saturation: float = Field(gt=0.0)
    """B 기본값 시트의 diag_severity_saturation 값."""

    decay: float = Field(gt=0.0, le=1.0)
    """B 기본값 시트의 diag_decay 값."""

    propagate_threshold: float = Field(ge=0.0)
    """B 기본값 시트의 diag_propagate_threshold 값."""

    suspect_damping: float = Field(gt=0.0, le=1.0)
    """B 기본값 시트의 diag_suspect_damping 값."""

    node_min_items: int = Field(ge=1)
    """B 기본값 시트의 node_min_items 값."""


@dataclass(frozen=True, slots=True)
class _Count:
    correct: int
    total: int

    @property
    def accuracy(self) -> float:
        return self.correct / self.total


@dataclass(frozen=True, slots=True)
class _NodeState:
    verdict: NodeVerdict
    severity: float | None
    basis: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Path:
    node_id: str
    distance: int
    weight_product: float


def diagnose(
    diagnosis_input: DiagnosisInput,
    graph: SkillGraph,
    config: DiagnosisConfig,
    graph_version: str,
    taxonomy_version: str,
    config_version: str,
) -> DiagnosisResult:
    """입력 스냅숏을 약점 지도로 변환한다.

    I/O·시계·난수·LLM을 사용하지 않으며 모든 임계값은 ``config``에서 받는다.
    """

    _validate_versions(graph, graph_version, taxonomy_version, config_version)
    events = _deduplicate_events(diagnosis_input.events)
    confirmed_events = tuple(event for event in events if event.tag_confirmed)
    _validate_node_references(confirmed_events, graph)

    if not confirmed_events:
        return _insufficient_result()

    overall_acc = _count(confirmed_events).accuracy
    cells, cell_coordinates = _build_cells(confirmed_events, overall_acc, config)
    judgeable_cells = tuple(
        cell for cell in cells.values() if cell.verdict is not CellVerdict.UNKNOWN
    )
    if not judgeable_cells:
        return _insufficient_result()

    node_states = _build_indirect_node_states(graph, cells, cell_coordinates)
    direct_counts = _group_direct_events(confirmed_events)
    direct_ok_nodes = _merge_direct_node_states(
        node_states,
        direct_counts,
        overall_acc,
        config,
    )
    propagated = _propagate(graph, node_states, direct_ok_nodes, config)
    nodes = {
        node_id: WeaknessNode(verdict=state.verdict, basis=state.basis)
        for node_id, state in sorted(node_states.items())
    }

    weakness_map = WeaknessMap(
        graph_version=graph_version,
        taxonomy_version=taxonomy_version,
        config_version=config_version,
        snapshot_hash=diagnosis_input.snapshot_hash,
        cells=cells,
        nodes=nodes,
        propagated=propagated,
        overall_low=all(cell.verdict is CellVerdict.WEAK for cell in judgeable_cells),
    )
    return DiagnosisResult(status=DiagnosisStatus.GENERATED, weakness_map=weakness_map)


def _validate_versions(
    graph: SkillGraph,
    graph_version: str,
    taxonomy_version: str,
    config_version: str,
) -> None:
    if graph_version != graph.meta.graph_version:
        raise DiagnosisVersionMismatchError(
            "graph_version 불일치: "
            f"graph={graph.meta.graph_version}, requested={graph_version}"
        )
    if taxonomy_version != graph.meta.taxonomy_version:
        raise DiagnosisVersionMismatchError(
            "taxonomy_version 불일치: "
            f"graph={graph.meta.taxonomy_version}, requested={taxonomy_version}"
        )
    if not config_version:
        raise DiagnosisVersionMismatchError("config_version은 비어 있을 수 없다")


def _deduplicate_events(events: tuple[DiagnosisEvent, ...]) -> tuple[DiagnosisEvent, ...]:
    by_id: dict[str, DiagnosisEvent] = {}
    for event in events:
        existing = by_id.get(event.event_id)
        if existing is None:
            by_id[event.event_id] = event
            continue
        if existing != event:
            raise DiagnosisInputConflictError(
                f"동일 event_id의 내용이 다르다: {event.event_id}"
            )
    return tuple(by_id[event_id] for event_id in sorted(by_id))


def _validate_node_references(events: tuple[DiagnosisEvent, ...], graph: SkillGraph) -> None:
    known_node_ids = set(graph.node_index())
    unknown_node_ids = sorted(
        {
            event.skill_node_id
            for event in events
            if event.skill_node_id is not None and event.skill_node_id not in known_node_ids
        }
    )
    if unknown_node_ids:
        raise DiagnosisGraphReferenceError(
            f"그래프에 없는 skill_node_id: {', '.join(unknown_node_ids)}"
        )


def _count(events: tuple[DiagnosisEvent, ...]) -> _Count:
    return _Count(correct=sum(event.correct for event in events), total=len(events))


def _group_cell_events(
    events: tuple[DiagnosisEvent, ...],
) -> dict[tuple[AreaTag, TypeTag], tuple[DiagnosisEvent, ...]]:
    grouped: dict[tuple[AreaTag, TypeTag], list[DiagnosisEvent]] = {}
    for event in events:
        grouped.setdefault((event.area_tag, event.type_tag), []).append(event)
    return {
        coordinate: tuple(grouped[coordinate])
        for coordinate in sorted(grouped, key=lambda item: (item[0].value, item[1].value))
    }


def _build_cells(
    events: tuple[DiagnosisEvent, ...],
    overall_acc: float,
    config: DiagnosisConfig,
) -> tuple[dict[str, WeaknessCell], dict[tuple[AreaTag, TypeTag], str]]:
    cells: dict[str, WeaknessCell] = {}
    coordinates: dict[tuple[AreaTag, TypeTag], str] = {}
    for coordinate, cell_events in _group_cell_events(events).items():
        count = _count(cell_events)
        key = _cell_key(*coordinate)
        coordinates[coordinate] = key
        if count.total < config.cell_min_items:
            cells[key] = WeaknessCell(
                acc=count.accuracy,
                n=count.total,
                verdict=CellVerdict.UNKNOWN,
            )
            continue

        cell_delta_pp = (count.accuracy - overall_acc) * 100
        if cell_delta_pp <= config.relative_cut_pp:
            cells[key] = WeaknessCell(
                acc=count.accuracy,
                n=count.total,
                verdict=CellVerdict.WEAK,
                severity=_severity(overall_acc, count.accuracy, config),
            )
            continue
        cells[key] = WeaknessCell(
            acc=count.accuracy,
            n=count.total,
            verdict=CellVerdict.OK,
        )
    return cells, coordinates


def _cell_key(area_tag: AreaTag, type_tag: TypeTag) -> str:
    return f"{area_tag.value}×{type_tag.value}"


def _severity(overall_acc: float, target_acc: float, config: DiagnosisConfig) -> float:
    ratio_gap = max(0.0, overall_acc - target_acc)
    return min(1.0, ratio_gap / config.severity_saturation)


def _build_indirect_node_states(
    graph: SkillGraph,
    cells: dict[str, WeaknessCell],
    cell_coordinates: dict[tuple[AreaTag, TypeTag], str],
) -> dict[str, _NodeState]:
    states: dict[str, _NodeState] = {}
    for node in graph.nodes:
        weak_basis: list[str] = []
        severities: list[float] = []
        for type_tag in node.type_affinity:
            cell_key = cell_coordinates.get((node.area_tag, type_tag))
            if cell_key is None:
                continue
            cell = cells[cell_key]
            if cell.verdict is not CellVerdict.WEAK:
                continue
            if cell.severity is None:
                raise AssertionError("weak 셀에는 계약상 severity가 있어야 한다")
            weak_basis.append(f"cell:{cell_key}")
            severities.append(cell.severity)
        if weak_basis:
            states[node.id] = _NodeState(
                verdict=NodeVerdict.SUSPECT,
                severity=max(severities),
                basis=tuple(sorted(weak_basis)),
            )
    return states


def _group_direct_events(
    events: tuple[DiagnosisEvent, ...],
) -> dict[str, tuple[DiagnosisEvent, ...]]:
    grouped: dict[str, list[DiagnosisEvent]] = {}
    for event in events:
        if event.skill_node_id is not None:
            grouped.setdefault(event.skill_node_id, []).append(event)
    return {
        node_id: tuple(grouped[node_id])
        for node_id in sorted(grouped)
    }


def _merge_direct_node_states(
    states: dict[str, _NodeState],
    direct_counts: dict[str, tuple[DiagnosisEvent, ...]],
    overall_acc: float,
    config: DiagnosisConfig,
) -> frozenset[str]:
    direct_ok_nodes: set[str] = set()
    for node_id, node_events in direct_counts.items():
        if len(node_events) < config.node_min_items:
            continue
        node_acc = _count(node_events).accuracy
        node_delta_pp = (node_acc - overall_acc) * 100
        basis = tuple(f"event:{event.event_id}" for event in node_events)
        if node_delta_pp <= config.relative_cut_pp:
            states[node_id] = _NodeState(
                verdict=NodeVerdict.WEAK_CONFIRMED,
                severity=_severity(overall_acc, node_acc, config),
                basis=basis,
            )
            continue
        states[node_id] = _NodeState(
            verdict=NodeVerdict.OK,
            severity=None,
            basis=basis,
        )
        direct_ok_nodes.add(node_id)
    return frozenset(direct_ok_nodes)


def _propagate(
    graph: SkillGraph,
    states: dict[str, _NodeState],
    direct_ok_nodes: frozenset[str],
    config: DiagnosisConfig,
) -> dict[str, PropagatedNode]:
    scores: dict[str, float] = {}
    origins: dict[str, set[str]] = {}
    source_order = tuple(reversed(graph.topological_node_ids()))
    for source_id in source_order:
        state = states.get(source_id)
        if state is None or state.verdict is NodeVerdict.OK:
            continue
        if state.severity is None:
            raise AssertionError("전파 출발 노드에는 severity가 있어야 한다")
        damping = config.suspect_damping if state.verdict is NodeVerdict.SUSPECT else 1.0
        queue: deque[_Path] = deque([_Path(source_id, 0, 1.0)])
        while queue:
            path = queue.popleft()
            for edge in graph.propagation_parents(path.node_id):
                parent_id = edge.from_node
                if parent_id in direct_ok_nodes:
                    continue
                distance = path.distance + 1
                weight_product = path.weight_product * edge.weight
                contribution = (
                    state.severity
                    * damping
                    * weight_product
                    * (config.decay**distance)
                )
                scores[parent_id] = scores.get(parent_id, 0.0) + contribution
                origins.setdefault(parent_id, set()).add(source_id)
                queue.append(_Path(parent_id, distance, weight_product))

    return {
        node_id: PropagatedNode(
            score=scores[node_id],
            from_nodes=tuple(sorted(origins[node_id])),
        )
        for node_id in sorted(scores)
        if scores[node_id] >= config.propagate_threshold
    }


def _insufficient_result() -> DiagnosisResult:
    return DiagnosisResult(
        status=DiagnosisStatus.REJECTED_INSUFFICIENT,
        status_reason="판정 가능한 셀이 없다",
    )
