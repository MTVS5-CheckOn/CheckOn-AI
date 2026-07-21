"""커리큘럼 그래프 로더의 무결성·결정론 검증."""

from pathlib import Path

import pytest

from ai.contracts.taxonomy import AreaTag, TypeTag
from ai.diagnosis.skill_graph import (
    EdgeKind,
    GraphCycleError,
    GraphIntegrityError,
    GraphLoadError,
    GraphParseError,
    GraphSchemaError,
    GraphVersionMismatchError,
    load_skill_graph,
)

# 실 콘텐츠 아님 — 로더 검증용 최소 문법 그래프.
VALID_GRAPH_YAML = """
meta:
  graph_version: "0.1.0"
  taxonomy_version: "v1"
  updated: 2026-07-15
nodes:
  - id: lang.pronunciation
    label: "발음 적용"
    area_tag: language
    type_affinity: [fact]
    level: 3
    desc: "테스트용 후행 노드"
  - id: lang.phoneme_system
    label: "음운 체계"
    area_tag: language
    type_affinity: [concept, fact]
    level: 1
    desc: "테스트용 루트 노드"
  - id: lang.related_concept
    label: "관련 개념"
    area_tag: language
    type_affinity: [concept]
    level: 1
    desc: "관련 엣지 검증 노드"
  - id: lang.phoneme_change
    label: "음운 변동"
    area_tag: language
    type_affinity: [concept]
    level: 2
    desc: "테스트용 중간 노드"
edges:
  - from: lang.phoneme_change
    to: lang.pronunciation
    kind: builds_on
    weight: 0.8
  - from: lang.phoneme_system
    to: lang.phoneme_change
    kind: requires
    weight: 1.0
  - from: lang.related_concept
    to: lang.phoneme_system
    kind: related
    weight: 0.5
"""


def _replace_once(old: str, new: str) -> str:
    assert old in VALID_GRAPH_YAML
    return VALID_GRAPH_YAML.replace(old, new, 1)


def test_loads_and_canonicalizes_valid_graph() -> None:
    graph = load_skill_graph(VALID_GRAPH_YAML, expected_taxonomy_version="v1")

    assert [node.id for node in graph.nodes] == sorted(node.id for node in graph.nodes)
    assert graph.nodes[1].area_tag is AreaTag.LANGUAGE
    assert graph.nodes[1].type_affinity == (TypeTag.CONCEPT, TypeTag.FACT)
    assert graph.topological_node_ids() == (
        "lang.phoneme_system",
        "lang.phoneme_change",
        "lang.pronunciation",
        "lang.related_concept",
    )


def test_loads_explicit_path(tmp_path: Path) -> None:
    graph_path = tmp_path / "loader-fixture.yaml"
    graph_path.write_text(VALID_GRAPH_YAML, encoding="utf-8")

    graph = load_skill_graph(graph_path, expected_taxonomy_version="v1")

    assert graph.meta.graph_version == "0.1.0"


def test_node_index_and_reverse_propagation_edges() -> None:
    graph = load_skill_graph(VALID_GRAPH_YAML, expected_taxonomy_version="v1")

    assert graph.node_index()["lang.phoneme_system"].label == "음운 체계"
    parents = graph.propagation_parents("lang.phoneme_change")
    assert [(edge.from_node, edge.kind) for edge in parents] == [
        ("lang.phoneme_system", EdgeKind.REQUIRES)
    ]
    assert graph.propagation_parents("lang.phoneme_system") == ()


def test_rejects_cycle_in_propagation_edges() -> None:
    source = VALID_GRAPH_YAML + """
  - from: lang.pronunciation
    to: lang.phoneme_system
    kind: requires
    weight: 1.0
"""

    with pytest.raises(GraphCycleError, match="순환"):
        load_skill_graph(source, expected_taxonomy_version="v1")


def test_related_only_cycle_is_allowed() -> None:
    source = VALID_GRAPH_YAML + """
  - from: lang.phoneme_system
    to: lang.related_concept
    kind: related
    weight: 0.5
"""

    graph = load_skill_graph(source, expected_taxonomy_version="v1")

    assert len(graph.edges) == 4


def test_rejects_unknown_area_tag() -> None:
    source = _replace_once("area_tag: language", "area_tag: unknown_area")
    with pytest.raises(GraphSchemaError, match="area_tag"):
        load_skill_graph(source, expected_taxonomy_version="v1")


def test_rejects_unknown_type_affinity() -> None:
    source = _replace_once("type_affinity: [fact]", "type_affinity: [unknown_type]")
    with pytest.raises(GraphSchemaError, match="type_affinity"):
        load_skill_graph(source, expected_taxonomy_version="v1")


def test_rejects_duplicate_node_id() -> None:
    source = _replace_once("lang.related_concept", "lang.phoneme_system")
    with pytest.raises(GraphIntegrityError, match="중복 노드 ID"):
        load_skill_graph(source, expected_taxonomy_version="v1")


def test_rejects_missing_edge_endpoint() -> None:
    source = _replace_once("to: lang.pronunciation", "to: lang.missing")
    with pytest.raises(GraphIntegrityError, match="미존재 노드"):
        load_skill_graph(source, expected_taxonomy_version="v1")


def test_rejects_taxonomy_version_mismatch() -> None:
    with pytest.raises(GraphVersionMismatchError, match="taxonomy_version 불일치"):
        load_skill_graph(VALID_GRAPH_YAML, expected_taxonomy_version="v2")


def test_rejects_empty_node_id() -> None:
    source = _replace_once("id: lang.pronunciation", 'id: ""')
    with pytest.raises(GraphSchemaError, match="nodes.0.id"):
        load_skill_graph(source, expected_taxonomy_version="v1")


@pytest.mark.parametrize("weight", ["0", "1.1"])
def test_rejects_out_of_range_weight(weight: str) -> None:
    source = _replace_once("weight: 0.8", f"weight: {weight}")
    with pytest.raises(GraphSchemaError, match="weight"):
        load_skill_graph(source, expected_taxonomy_version="v1")


def test_rejects_unknown_edge_kind() -> None:
    source = _replace_once("kind: builds_on", "kind: unknown")
    with pytest.raises(GraphSchemaError, match="kind"):
        load_skill_graph(source, expected_taxonomy_version="v1")


def test_rejects_empty_node_list() -> None:
    source = VALID_GRAPH_YAML.replace(
        VALID_GRAPH_YAML[VALID_GRAPH_YAML.index("nodes:") : VALID_GRAPH_YAML.index("edges:")],
        "nodes: []\n",
    )
    with pytest.raises(GraphSchemaError, match="nodes"):
        load_skill_graph(source, expected_taxonomy_version="v1")


def test_rejects_malformed_yaml() -> None:
    with pytest.raises(GraphParseError, match="YAML 구문 오류"):
        load_skill_graph("meta: [", expected_taxonomy_version="v1")


def test_wraps_missing_path_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    with pytest.raises(GraphLoadError, match="읽을 수 없다"):
        load_skill_graph(missing, expected_taxonomy_version="v1")


def test_same_input_serializes_identically() -> None:
    first = load_skill_graph(VALID_GRAPH_YAML, expected_taxonomy_version="v1")
    second = load_skill_graph(VALID_GRAPH_YAML, expected_taxonomy_version="v1")

    assert first.model_dump_json(by_alias=True) == second.model_dump_json(by_alias=True)
