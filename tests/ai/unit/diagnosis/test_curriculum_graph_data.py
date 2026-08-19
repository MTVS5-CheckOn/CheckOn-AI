"""수능 문법 curriculum_graph 실파일의 범위·무결성 검증."""

from pathlib import Path

from ai.contracts.taxonomy import AreaTag
from ai.diagnosis.curriculum_standards import (
    cited_codes,
    load_curriculum_standards,
)
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


def test_new_area_nodes_keep_area_specs_refs_under_official_provenance() -> None:
    #: 성취기준 근거가 붙어 provenance 는 official_public 으로 올라갔지만, 이 노드들이
    #: 애초에 출제 규격에서 파생됐다는 사실은 `source_refs` 에 그대로 남아야 한다.
    graph = load_skill_graph(GRAPH_PATH, expected_taxonomy_version="v1")
    added_nodes = tuple(
        node for node in graph.nodes if node.area_tag is not AreaTag.LANGUAGE
    )

    assert added_nodes
    assert all(
        node.source_stage is GraphSourceStage.OFFICIAL_PUBLIC for node in added_nodes
    )
    assert all(
        any("area_specs.yaml#areas." in ref for ref in node.source_refs)
        for node in added_nodes
    )


def test_every_node_cites_an_existing_2022_achievement_standard() -> None:
    #: 🔴 종전 언어 33노드는 `source_refs` 가 **아예 없었다** — 근거 없는 교육 내용이
    #: 진단 판정의 축이 되고 있었다. 인용은 실재해야 근거다.
    graph = load_skill_graph(GRAPH_PATH, expected_taxonomy_version="v1")
    standards = load_curriculum_standards()

    for node in graph.nodes:
        codes = cited_codes(node.source_refs)
        assert codes, f"{node.id}에 성취기준 근거가 없다"
        unknown = sorted(set(codes) - standards.codes)
        assert not unknown, f"{node.id}가 실재하지 않는 성취기준을 인용한다: {unknown}"


def test_cited_standards_stay_within_the_high_school_scope() -> None:
    #: v1은 수능 대비 고등만 본다(CLAUDE.md §0) — 중등 성취기준 인용은 범위 이탈이다.
    graph = load_skill_graph(GRAPH_PATH, expected_taxonomy_version="v1")
    standards = load_curriculum_standards()
    high_school_prefixes = {subject.prefix for subject in standards.subjects}

    for node in graph.nodes:
        for code in cited_codes(node.source_refs):
            assert any(
                code.startswith(f"{prefix}-") for prefix in high_school_prefixes
            ), f"{node.id}가 고등 범위 밖 성취기준을 인용한다: {code}"


def test_every_area_is_grounded_in_its_own_subject_standards() -> None:
    #: 영역별로 어느 과목을 근거로 삼는지가 뒤섞이면 매핑 검수가 불가능해진다.
    expected_subjects = {
        AreaTag.LANGUAGE: {"공통국어1", "화법과 언어"},
        AreaTag.LITERATURE: {"공통국어1", "공통국어2", "문학"},
        AreaTag.READING: {"공통국어1", "공통국어2", "독서와 작문"},
        AreaTag.SPEECH_WRITING: {"공통국어1", "공통국어2", "화법과 언어", "독서와 작문"},
        AreaTag.MEDIA: {"공통국어1", "공통국어2", "매체 의사소통"},
    }
    graph = load_skill_graph(GRAPH_PATH, expected_taxonomy_version="v1")
    standards = load_curriculum_standards()

    for node in graph.nodes:
        for code in cited_codes(node.source_refs):
            subject = standards.subject_of(code)
            assert subject is not None
            assert subject.name in expected_subjects[node.area_tag], (
                f"{node.id}({node.area_tag.value})가 {subject.name} 성취기준을 인용한다"
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
