"""수능 문법 curriculum_graph 실파일의 범위·무결성 검증."""

from pathlib import Path

from ai.contracts.taxonomy import AreaTag
from ai.diagnosis.skill_graph import GraphSourceStage, load_skill_graph

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

    assert len(graph.nodes) == 57
    assert len(graph.topological_node_ids()) == len(graph.nodes)
    assert graph.meta.graph_version == "curriculum-five-area-v1"
    assert graph.meta.review_status == "expert_review_pending"
    assert graph.meta.scope_note is not None
    assert "v1 초안" in graph.meta.scope_note
    assert "전문가 검수 대기" in graph.meta.scope_note


def test_curriculum_graph_covers_every_area_without_grade_split() -> None:
    graph = load_skill_graph(GRAPH_PATH, expected_taxonomy_version="v1")
    forbidden_id_fragments = {"middle", "grade", "school"}

    assert {node.area_tag for node in graph.nodes} == set(AreaTag)
    assert all(
        not any(fragment in node.id.lower() for fragment in forbidden_id_fragments)
        for node in graph.nodes
    )


def test_existing_language_nodes_stay_untouched_and_new_areas_are_bounded() -> None:
    graph = load_skill_graph(GRAPH_PATH, expected_taxonomy_version="v1")
    counts = {
        area: sum(node.area_tag is area for node in graph.nodes) for area in AreaTag
    }

    assert counts[AreaTag.LANGUAGE] == 33
    assert all(
        6 <= counts[area] <= 10
        for area in (
            AreaTag.READING,
            AreaTag.LITERATURE,
            AreaTag.SPEECH_WRITING,
            AreaTag.MEDIA,
        )
    )


def test_new_area_nodes_record_area_specs_provenance() -> None:
    graph = load_skill_graph(GRAPH_PATH, expected_taxonomy_version="v1")
    added_nodes = tuple(
        node for node in graph.nodes if node.area_tag is not AreaTag.LANGUAGE
    )

    assert added_nodes
    assert all(node.source_stage is GraphSourceStage.AREA_SPECS for node in added_nodes)
    assert all(node.source_refs for node in added_nodes)
    assert all(
        any("area_specs.yaml#areas." in ref for ref in node.source_refs)
        for node in added_nodes
    )


def test_every_literature_node_is_backed_by_the_current_pool() -> None:
    graph = load_skill_graph(GRAPH_PATH, expected_taxonomy_version="v1")
    literature_nodes = tuple(
        node for node in graph.nodes if node.area_tag is AreaTag.LITERATURE
    )

    assert len(literature_nodes) == 6
    assert all(
        "src/ai/problem_generation/data/literature_pool/index.json"
        in node.source_refs
        for node in literature_nodes
    )


def test_curriculum_graph_marks_all_educational_content_for_review() -> None:
    graph = load_skill_graph(GRAPH_PATH, expected_taxonomy_version="v1")

    assert all("전문가 검수 대기" in node.desc for node in graph.nodes)
