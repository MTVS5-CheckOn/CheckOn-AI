"""커리큘럼 그래프 로드와 결정론적 무결성 검증."""

from collections.abc import Mapping
from datetime import date
from enum import StrEnum
from heapq import heapify, heappop, heappush
from pathlib import Path
from typing import Literal

import yaml  # type: ignore[import-untyped]  # PyYAML은 공식 타입 정보를 제공하지 않는다.
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ai.contracts.taxonomy import AreaTag, TypeTag


class GraphLoadError(ValueError):
    """그래프를 안전하게 사용할 수 없을 때의 기본 예외."""


class GraphParseError(GraphLoadError):
    """YAML 구문을 해석할 수 없음."""


class GraphSchemaError(GraphLoadError):
    """YAML 값이 그래프 스키마를 위반함."""


class GraphIntegrityError(GraphLoadError):
    """노드·엣지 참조 무결성이 깨짐."""


class GraphVersionMismatchError(GraphLoadError):
    """그래프 taxonomy 버전이 실행 버전과 다름."""


class GraphCycleError(GraphLoadError):
    """선수 관계 그래프에 순환이 존재함."""


class EdgeKind(StrEnum):
    """그래프 엣지 의미."""

    REQUIRES = "requires"
    BUILDS_ON = "builds_on"
    RELATED = "related"


class GraphMeta(BaseModel):
    """그래프 재현성 메타."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    graph_version: str = Field(min_length=1)
    taxonomy_version: str = Field(min_length=1)
    updated: date
    review_status: Literal["expert_review_pending"] | None = None
    scope_note: str | None = Field(default=None, min_length=1)


class GraphSourceStage(StrEnum):
    """교육 내용 초안이 어떤 정본 단계에서 파생됐는지 표시한다."""

    AREA_SPECS = "area_specs"
    OFFICIAL_PUBLIC = "official_public"


class GraphNode(BaseModel):
    """커리큘럼 개념·기능 노드."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    area_tag: AreaTag
    type_affinity: tuple[TypeTag, ...] = Field(min_length=1)
    level: int | None = Field(default=None, ge=1)
    desc: str = Field(min_length=1)
    source_stage: GraphSourceStage | None = None
    source_refs: tuple[str, ...] = ()

    @field_validator("type_affinity")
    @classmethod
    def normalize_type_affinity(cls, value: tuple[TypeTag, ...]) -> tuple[TypeTag, ...]:
        if len(set(value)) != len(value):
            raise ValueError("type_affinity는 중복될 수 없다")
        return tuple(sorted(value, key=lambda item: item.value))

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not ref for ref in value):
            raise ValueError("source_refs의 출처 참조는 비어 있을 수 없다")
        if len(set(value)) != len(value):
            raise ValueError("source_refs는 중복될 수 없다")
        return value


class GraphEdge(BaseModel):
    """선수·누적·관련 관계 엣지."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    from_node: str = Field(alias="from", min_length=1)
    to_node: str = Field(alias="to", min_length=1)
    kind: EdgeKind
    weight: float = Field(gt=0.0, le=1.0)


class SkillGraph(BaseModel):
    """검증·정규화가 끝난 커리큘럼 그래프."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    meta: GraphMeta
    nodes: tuple[GraphNode, ...] = Field(min_length=1)
    edges: tuple[GraphEdge, ...] = ()

    def node_index(self) -> dict[str, GraphNode]:
        """노드 ID로 조회하는 새 사전을 반환한다."""

        return {node.id: node for node in self.nodes}

    def propagation_parents(self, node_id: str) -> tuple[GraphEdge, ...]:
        """해당 노드에서 역추적할 requires·builds_on 엣지를 반환한다."""

        return tuple(
            edge
            for edge in self.edges
            if edge.to_node == node_id and edge.kind is not EdgeKind.RELATED
        )

    def topological_node_ids(self) -> tuple[str, ...]:
        """related를 제외한 결정론적 위상 순서를 반환한다."""

        return _topological_node_ids(self.nodes, self.edges)


def load_skill_graph(
    source: Path | str,
    expected_taxonomy_version: str,
) -> SkillGraph:
    """Path 또는 YAML 텍스트를 읽어 검증·정규화된 그래프를 반환한다.

    문자열은 항상 YAML 본문으로 취급한다. 파일 입력은 명시적으로 ``Path``를 전달한다.
    """

    if not expected_taxonomy_version:
        raise GraphVersionMismatchError("expected_taxonomy_version은 비어 있을 수 없다")

    text = _read_source(source)
    try:
        raw: object = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise GraphParseError(f"YAML 구문 오류: {error}") from error

    if not isinstance(raw, Mapping):
        raise GraphSchemaError("그래프 YAML 최상위 값은 객체여야 한다")

    try:
        parsed = SkillGraph.model_validate(raw)
    except ValidationError as error:
        raise GraphSchemaError(f"그래프 스키마 오류: {error}") from error

    _validate_unique_node_ids(parsed.nodes)
    _validate_edge_references(parsed.nodes, parsed.edges)
    _validate_taxonomy_version(parsed.meta, expected_taxonomy_version)
    _topological_node_ids(parsed.nodes, parsed.edges)
    return _canonicalize(parsed)


def _read_source(source: Path | str) -> str:
    if isinstance(source, Path):
        try:
            return source.read_text(encoding="utf-8")
        except OSError as error:
            raise GraphLoadError(f"그래프 파일을 읽을 수 없다: {source}") from error
    return source


def _validate_unique_node_ids(nodes: tuple[GraphNode, ...]) -> None:
    node_ids = [node.id for node in nodes]
    duplicates = sorted(node_id for node_id in set(node_ids) if node_ids.count(node_id) > 1)
    if duplicates:
        raise GraphIntegrityError(f"중복 노드 ID: {', '.join(duplicates)}")


def _validate_edge_references(
    nodes: tuple[GraphNode, ...],
    edges: tuple[GraphEdge, ...],
) -> None:
    node_ids = {node.id for node in nodes}
    for edge in edges:
        missing = sorted({edge.from_node, edge.to_node} - node_ids)
        if missing:
            raise GraphIntegrityError(
                f"엣지 {edge.from_node}->{edge.to_node}의 미존재 노드: {', '.join(missing)}"
            )


def _validate_taxonomy_version(meta: GraphMeta, expected: str) -> None:
    if meta.taxonomy_version != expected:
        raise GraphVersionMismatchError(
            f"taxonomy_version 불일치: graph={meta.taxonomy_version}, expected={expected}"
        )


def _topological_node_ids(
    nodes: tuple[GraphNode, ...],
    edges: tuple[GraphEdge, ...],
) -> tuple[str, ...]:
    node_ids = tuple(sorted(node.id for node in nodes))
    indegree = dict.fromkeys(node_ids, 0)
    children: dict[str, list[str]] = {node_id: [] for node_id in node_ids}

    for edge in edges:
        if edge.kind is EdgeKind.RELATED:
            continue
        indegree[edge.to_node] += 1
        children[edge.from_node].append(edge.to_node)

    ready = [node_id for node_id in node_ids if indegree[node_id] == 0]
    heapify(ready)
    ordered: list[str] = []
    while ready:
        node_id = heappop(ready)
        ordered.append(node_id)
        for child_id in sorted(children[node_id]):
            indegree[child_id] -= 1
            if indegree[child_id] == 0:
                heappush(ready, child_id)

    if len(ordered) != len(node_ids):
        cyclic_nodes = sorted(node_id for node_id, degree in indegree.items() if degree > 0)
        raise GraphCycleError(f"선수 관계 순환 노드: {', '.join(cyclic_nodes)}")
    return tuple(ordered)


def _canonicalize(graph: SkillGraph) -> SkillGraph:
    nodes = tuple(sorted(graph.nodes, key=lambda node: node.id))
    edges = tuple(
        sorted(
            graph.edges,
            key=lambda edge: (
                edge.from_node,
                edge.to_node,
                edge.kind.value,
                edge.weight,
            ),
        )
    )
    return SkillGraph(meta=graph.meta, nodes=nodes, edges=edges)
