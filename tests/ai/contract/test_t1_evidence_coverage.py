"""T1(language) 노드가 실제 정본 근거를 반환하는지 고정한다."""

from __future__ import annotations

import asyncio

import pytest

from ai.contracts.graphrag import ContextLockedFields, GraphContextRequest
from ai.contracts.problem_generation import TargetSource
from ai.contracts.taxonomy import AreaTag, ItemFormat, TypeTag
from ai.diagnosis.config import load_curriculum_graph
from ai.problem_generation.domain.policy import (
    requires_reference_before_source_procurement,
)
from ai.problem_generation.domain.rules import has_reference_data
from ai.problem_generation.infrastructure.grammar_norm import (
    GrammarNormCorpus,
    GrammarNormNodeMap,
    count_node_matches,
    load_grammar_norm_corpus,
)
from ai.problem_generation.infrastructure.graph_context import (
    GrammarNormGraphContextService,
)


def _language_node_ids() -> tuple[str, ...]:
    graph = load_curriculum_graph()
    return tuple(node.id for node in graph.nodes if node.area_tag is AreaTag.LANGUAGE)


_LANGUAGE_NODE_IDS = _language_node_ids()
_MAPPED_NODE_ID = "language.grammar.phonological_change"


def _request(skill_node_id: str) -> GraphContextRequest:
    return GraphContextRequest(
        tenant_id="tenant-t1-evidence-coverage",
        target_source=TargetSource.TEACHER_MANUAL,
        target_skill_node_ids=(skill_node_id,),
        locked_fields=ContextLockedFields(
            target_ref="student-t1-evidence-coverage",
            area_tag=AreaTag.LANGUAGE,
            type_tags=(TypeTag.CONCEPT,),
            skill_node_id=skill_node_id,
            item_format=ItemFormat.MCQ,
        ),
        policy_constraints={"evidence_required": True},
    )


def _corpus_with_node(node: GrammarNormNodeMap) -> GrammarNormCorpus:
    corpus = load_grammar_norm_corpus()
    mapping = corpus.mapping.model_copy(update={"nodes": {_MAPPED_NODE_ID: node}})
    return GrammarNormCorpus(
        version=corpus.version,
        mapping=mapping,
        rows=corpus.rows,
        attribution=corpus.attribution,
        eligible_count=corpus.eligible_count,
        duplicate_count=corpus.duplicate_count,
    )


def test_t1_catalog_contains_33_language_nodes() -> None:
    assert len(_LANGUAGE_NODE_IDS) == 33
    assert len(_LANGUAGE_NODE_IDS) == len(set(_LANGUAGE_NODE_IDS))


@pytest.mark.parametrize(
    "node_id",
    _LANGUAGE_NODE_IDS,
    ids=_LANGUAGE_NODE_IDS,
)
def test_every_t1_node_has_allowed_evidence_refs(node_id: str) -> None:
    context = asyncio.run(
        GrammarNormGraphContextService().resolve_generation_context(_request(node_id))
    )

    refs = context.retrieval_trace["allowed_evidence_refs"]
    assert has_reference_data(context), f"T1 정본 근거가 없는 노드: {node_id}"
    assert isinstance(refs, list) and refs, f"허용 근거 ref가 없는 노드: {node_id}"


def test_unknown_regulation_code_cannot_produce_evidence() -> None:
    corpus = _corpus_with_node(
        GrammarNormNodeMap(
            label="존재하지 않는 규정코드",
            codes=("존재하지-않는-규정코드",),
            keywords=("음운",),
        )
    )

    assert count_node_matches(corpus, _MAPPED_NODE_ID) == 0
    context = asyncio.run(
        GrammarNormGraphContextService(corpus).resolve_generation_context(_request(_MAPPED_NODE_ID))
    )
    assert not has_reference_data(context)


def test_unmatched_keyword_cannot_produce_evidence() -> None:
    corpus = _corpus_with_node(
        GrammarNormNodeMap(
            label="일치하지 않는 주제어",
            codes=("한글 맞춤법",),
            keywords=("존재하지-않는-주제어",),
        )
    )

    assert count_node_matches(corpus, _MAPPED_NODE_ID) == 0
    context = asyncio.run(
        GrammarNormGraphContextService(corpus).resolve_generation_context(_request(_MAPPED_NODE_ID))
    )
    assert not has_reference_data(context)


def test_t1_anchor_order_and_refs_are_deterministic() -> None:
    service = GrammarNormGraphContextService()

    first = asyncio.run(service.resolve_generation_context(_request(_MAPPED_NODE_ID)))
    second = asyncio.run(service.resolve_generation_context(_request(_MAPPED_NODE_ID)))

    assert first.retrieval_trace["evidence_anchors"] == second.retrieval_trace["evidence_anchors"]
    assert (
        first.retrieval_trace["allowed_evidence_refs"]
        == second.retrieval_trace["allowed_evidence_refs"]
    )
    assert first.context_pack_hash == second.context_pack_hash


def test_empty_anchor_is_fail_closed_before_source_procurement() -> None:
    node_id = "language.grammar.unmapped"
    context = asyncio.run(
        GrammarNormGraphContextService().resolve_generation_context(_request(node_id))
    )

    assert requires_reference_before_source_procurement(AreaTag.LANGUAGE)
    assert not has_reference_data(context)
    assert context.retrieval_trace["allowed_evidence_refs"] == []
    assert context.retrieval_trace["evidence_anchors"] == []
