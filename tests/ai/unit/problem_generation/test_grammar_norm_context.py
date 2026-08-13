"""실 어문규범 CSV 기반 최소 GraphContext 계약."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from ai.contracts.graphrag import (
    ContextLockedFields,
    GraphContextOperation,
    GraphContextRequest,
)
from ai.contracts.problem_generation import (
    Answer,
    Choice,
    EvidenceAnchor,
    EvidenceKind,
    GeneratedItem,
    TargetSource,
)
from ai.contracts.taxonomy import AreaTag, ItemFormat, TypeTag
from ai.problem_generation.application.generator import hydrate_evidence_quotes
from ai.problem_generation.domain.rules import has_reference_data
from ai.problem_generation.infrastructure.grammar_norm import (
    count_node_matches,
    load_grammar_norm_corpus,
    select_node_rows,
)
from ai.problem_generation.infrastructure.graph_context import (
    AreaDelegatingGraphContextService,
    GrammarNormGraphContextService,
)

_NODE = "language.grammar.phonological_change"
_ATTRIBUTION = (
    "문화체육관광부 국립국어원, 「한국어 어문규범 규정 정보」(2025), "
    "https://www.data.go.kr/data/15122678/fileData.do, 공공누리 제1유형"
)


def _request(skill_node_id: str = _NODE) -> GraphContextRequest:
    return GraphContextRequest(
        tenant_id="tenant-grammar-norm",
        target_source=TargetSource.TEACHER_MANUAL,
        target_skill_node_ids=(skill_node_id,),
        locked_fields=ContextLockedFields(
            target_ref="student-grammar-norm",
            area_tag=AreaTag.LANGUAGE,
            type_tags=(TypeTag.INFER,),
            skill_node_id=skill_node_id,
            item_format=ItemFormat.MCQ,
        ),
        policy_constraints={"evidence_required": True},
    )


def test_phonological_change_context_uses_real_quotes_and_stable_refs() -> None:
    context = asyncio.run(GrammarNormGraphContextService().resolve_generation_context(_request()))

    assert has_reference_data(context)
    anchors = context.retrieval_trace["evidence_anchors"]
    assert isinstance(anchors, list) and anchors
    refs = context.retrieval_trace["allowed_evidence_refs"]
    assert isinstance(refs, list)
    assert all(isinstance(ref, str) and ref.startswith("kornorms:") for ref in refs)
    assert any(
        isinstance(anchor, dict) and "‘ㄷ, ㅌ’ 받침 뒤에" in str(anchor.get("quote"))
        for anchor in anchors
    )
    assert context.retrieval_trace["attribution"] == _ATTRIBUTION


def test_t1_smoke_node_is_mapped_before_real_llm_call() -> None:
    corpus = load_grammar_norm_corpus()

    assert _NODE in corpus.mapping.nodes
    assert len(select_node_rows(corpus, _NODE)) >= 1

    context = asyncio.run(
        GrammarNormGraphContextService(corpus).resolve_generation_context(_request())
    )
    refs = context.retrieval_trace["allowed_evidence_refs"]

    assert isinstance(refs, list) and refs


@pytest.mark.parametrize(
    ("area_tag", "skill_node_id"),
    [
        (AreaTag.READING, "reading.comprehension.main_idea"),
        (AreaTag.SPEECH_WRITING, "speech_writing.writing.material"),
        (AreaTag.MEDIA, "media.reception.credibility"),
    ],
)
def test_area_delegate_keeps_generated_source_base_empty(
    area_tag: AreaTag,
    skill_node_id: str,
) -> None:
    request = _request(skill_node_id).model_copy(
        update={
            "locked_fields": _request(skill_node_id).locked_fields.model_copy(
                update={"area_tag": area_tag}
            ),
            "policy_constraints": {
                "evidence_required": True,
                "taxonomy_version": "v1",
                "verify_config_version": "verify-config.v1",
                "source_request": {"area_tag": area_tag.value},
            },
        }
    )

    context = asyncio.run(AreaDelegatingGraphContextService().resolve_generation_context(request))

    assert not has_reference_data(context)
    assert context.retrieval_trace["allowed_evidence_refs"] == []
    assert context.retrieval_trace["evidence_anchors"] == []
    assert context.policy_constraints == request.policy_constraints


def test_area_delegate_preserves_t1_grammar_context() -> None:
    context = asyncio.run(
        AreaDelegatingGraphContextService().resolve_generation_context(_request())
    )

    assert has_reference_data(context)


def test_area_delegate_uses_neutral_base_for_literature() -> None:
    skill_node_id = "literature.structure.composition"
    request = _request(skill_node_id).model_copy(
        update={
            "locked_fields": _request(skill_node_id).locked_fields.model_copy(
                update={"area_tag": AreaTag.LITERATURE}
            ),
            "policy_constraints": {
                "evidence_required": True,
                "taxonomy_version": "v1",
                "verify_config_version": "verify-config.v1",
                "work_selection": {"genre": "modern_novel"},
            },
        }
    )

    context = asyncio.run(AreaDelegatingGraphContextService().resolve_generation_context(request))

    assert context.retrieval_trace == {
        "allowed_evidence_refs": [],
        "evidence_anchors": [],
    }


def test_unmapped_node_returns_context_without_reference_data() -> None:
    context = asyncio.run(
        GrammarNormGraphContextService().resolve_generation_context(
            _request("language.grammar.unmapped")
        )
    )

    assert not has_reference_data(context)
    assert context.retrieval_trace["allowed_evidence_refs"] == []
    assert context.retrieval_trace["evidence_anchors"] == []


def test_revision_context_reuses_approved_grammar_evidence_and_locked_inputs() -> None:
    request = _request().model_copy(
        update={
            "current_item_snapshot": {"stem": "기존 문항"},
            "redacted_instruction": "발문을 더 명확하게 바꿔 주세요.",
        }
    )

    context = asyncio.run(GrammarNormGraphContextService().resolve_revision_context(request))

    assert context.operation is GraphContextOperation.REFINE
    assert context.current_item_snapshot == request.current_item_snapshot
    assert context.redacted_instruction == request.redacted_instruction
    assert context.locked_fields == request.locked_fields
    assert has_reference_data(context)


def test_revision_context_rejects_missing_snapshot_or_instruction() -> None:
    service = GrammarNormGraphContextService()

    with pytest.raises(ValueError, match="현재 문항과 마스킹된 지시"):
        asyncio.run(service.resolve_revision_context(_request()))


def test_generator_hydrates_omitted_quote_from_approved_context() -> None:
    context = asyncio.run(GrammarNormGraphContextService().resolve_generation_context(_request()))
    refs = context.retrieval_trace["allowed_evidence_refs"]
    assert isinstance(refs, list)
    ref = refs[4]
    assert isinstance(ref, str)
    item = GeneratedItem(
        area_tag=AreaTag.LANGUAGE,
        type_tag=TypeTag.INFER,
        item_format=ItemFormat.MCQ,
        skill_node_id=_NODE,
        stem="음운 변동에 대한 설명으로 옳은 것을 고르시오.",
        choices=tuple(
            Choice(
                no=no,
                text=f"선택지 {no}",
                why_wrong=None if no == 1 else "근거와 다르다.",
            )
            for no in range(1, 6)
        ),
        answer=Answer(correct_no=1),
        rationale="승인된 규정에 따른다.",
        evidence=(EvidenceAnchor(kind=EvidenceKind.GRAMMAR_RULE, ref=ref),),
    )

    hydrated = hydrate_evidence_quotes(item, context)

    quote = hydrated.evidence[0].quote
    assert quote is not None
    assert "‘ㄷ, ㅌ’ 받침 뒤에" in quote


def test_generator_replaces_model_quote_with_approved_context_quote() -> None:
    context = asyncio.run(GrammarNormGraphContextService().resolve_generation_context(_request()))
    refs = context.retrieval_trace["allowed_evidence_refs"]
    anchors = context.retrieval_trace["evidence_anchors"]
    assert isinstance(refs, list)
    assert isinstance(anchors, list)
    ref = refs[4]
    assert isinstance(ref, str)
    canonical_quote = next(
        anchor["quote"]
        for anchor in anchors
        if isinstance(anchor, dict) and anchor.get("ref") == ref
    )
    model_quote = "모델이 ContextPack과 다르게 작성한 인용"
    item = GeneratedItem(
        area_tag=AreaTag.LANGUAGE,
        type_tag=TypeTag.INFER,
        item_format=ItemFormat.MCQ,
        skill_node_id=_NODE,
        stem="음운 변동에 대한 설명으로 옳은 것을 고르시오.",
        choices=tuple(
            Choice(
                no=no,
                text=f"선택지 {no}",
                why_wrong=None if no == 1 else "근거와 다르다.",
            )
            for no in range(1, 6)
        ),
        answer=Answer(correct_no=1),
        rationale="승인된 규정에 따른다.",
        evidence=(
            EvidenceAnchor(
                kind=EvidenceKind.GRAMMAR_RULE,
                ref=ref,
                quote=model_quote,
            ),
        ),
    )

    hydrated = hydrate_evidence_quotes(item, context)

    assert hydrated.evidence[0].quote == canonical_quote
    assert hydrated.evidence[0].quote != model_quote


def test_loader_uses_one_process_wide_csv_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_open = Path.open
    csv_reads = 0

    def counting_open(path: Path, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        nonlocal csv_reads
        if path.name == "최신고지규정정보.csv":
            csv_reads += 1
        return original_open(path, *args, **kwargs)

    load_grammar_norm_corpus.cache_clear()
    monkeypatch.setattr(Path, "open", counting_open)
    first = load_grammar_norm_corpus()
    second = load_grammar_norm_corpus()

    assert first is second
    assert csv_reads == 1
    assert first.eligible_count == 2134
    assert first.eligible_count - 1546 == 588
    assert first.duplicate_count == 1437
    assert count_node_matches(first, _NODE) == 41
    assert len(select_node_rows(first, _NODE)) == 8
    load_grammar_norm_corpus.cache_clear()
