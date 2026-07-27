"""수능 문법 curriculum_graph 실파일의 범위·무결성 검증."""

from pathlib import Path

from ai.contracts.taxonomy import AreaTag
from ai.diagnosis.skill_graph import load_skill_graph

GRAPH_PATH = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "ai"
    / "diagnosis"
    / "data"
    / "curriculum_graph.yaml"
)


def test_curriculum_graph_file_is_single_valid_dag() -> None:
    graph = load_skill_graph(GRAPH_PATH, expected_taxonomy_version="v1")

    assert 25 <= len(graph.nodes) <= 40
    assert len(graph.topological_node_ids()) == len(graph.nodes)
    assert graph.meta.graph_version == "curriculum-grammar-v1"


def test_curriculum_graph_is_csAT_language_only_without_grade_split() -> None:
    graph = load_skill_graph(GRAPH_PATH, expected_taxonomy_version="v1")
    forbidden_id_fragments = {"middle", "grade", "school"}

    assert {node.area_tag for node in graph.nodes} == {AreaTag.LANGUAGE}
    assert all(
        not any(fragment in node.id.lower() for fragment in forbidden_id_fragments)
        for node in graph.nodes
    )


def test_curriculum_graph_marks_all_educational_content_for_review() -> None:
    graph = load_skill_graph(GRAPH_PATH, expected_taxonomy_version="v1")

    assert all("전문가 검수 대기" in node.desc for node in graph.nodes)
