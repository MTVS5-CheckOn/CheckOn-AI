"""실 어문규범 CSV 기반 최소 GraphContext 계약."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from ai.contracts.graphrag import ContextLockedFields, GraphContextRequest
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
    context = asyncio.run(
        GrammarNormGraphContextService().resolve_generation_context(_request())
    )

    assert has_reference_data(context)
    anchors = context.retrieval_trace["evidence_anchors"]
    assert isinstance(anchors, list) and anchors
    refs = context.retrieval_trace["allowed_evidence_refs"]
    assert isinstance(refs, list)
    assert all(
        isinstance(ref, str) and ref.startswith("kornorms:") for ref in refs
    )
    assert any(
        isinstance(anchor, dict)
        and "‘ㄷ, ㅌ’ 받침 뒤에" in str(anchor.get("quote"))
        for anchor in anchors
    )
    assert context.retrieval_trace["attribution"] == _ATTRIBUTION


def test_unmapped_node_returns_context_without_reference_data() -> None:
    context = asyncio.run(
        GrammarNormGraphContextService().resolve_generation_context(
            _request("language.grammar.unmapped")
        )
    )

    assert not has_reference_data(context)
    assert context.retrieval_trace["allowed_evidence_refs"] == []
    assert context.retrieval_trace["evidence_anchors"] == []


def test_generator_hydrates_omitted_quote_from_approved_context() -> None:
    context = asyncio.run(
        GrammarNormGraphContextService().resolve_generation_context(_request())
    )
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
    assert first.eligible_count == 1546
    assert first.duplicate_count == 1028
    assert count_node_matches(first, _NODE) == 15
    assert len(select_node_rows(first, _NODE)) == 8
    load_grammar_norm_corpus.cache_clear()
