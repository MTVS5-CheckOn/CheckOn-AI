"""T2 PassageGenerator의 프롬프트·구조화 출력·실패 닫힘."""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from fake_provider import FakeProvider

from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.graphrag import (
    ContextLockedFields,
    ContextPack,
    GraphContextOperation,
)
from ai.contracts.llm import ModelRole
from ai.contracts.problem_generation import (
    PassageDomain,
    PassageDraft,
    PassageRequest,
    SentenceComplexity,
    TargetSource,
)
from ai.contracts.taxonomy import AreaTag, ItemFormat, TypeTag
from ai.llm.determinism import DETERMINISTIC_TEMPERATURE, LLM_SEED
from ai.llm.gateway import LlmGateway
from ai.problem_generation.application.passage_generator import (
    PassageDraftRejected,
    PassageGenerationUnavailable,
    PassageGenerator,
    attach_passage_draft,
)
from ai.problem_generation.domain.identity import canonical_json, sha256_hex
from ai.problem_generation.infrastructure.config import load_banned_topics

_ANCHOR_REF = "reading:source-1"


def _context_pack() -> ContextPack:
    return ContextPack(
        context_pack_id=UUID("11111111-1111-4111-8111-111111111111"),
        operation=GraphContextOperation.GENERATE,
        tenant_id="tenant-a",
        target_source=TargetSource.TEACHER_MANUAL,
        target_skill_node_ids=("reading.infer",),
        locked_fields=ContextLockedFields(
            target_ref="student-a",
            area_tag=AreaTag.READING,
            type_tags=(TypeTag.INFER,),
            skill_node_id="reading.infer",
            item_format=ItemFormat.MCQ,
        ),
        pedagogy_paths=("path:reading.infer",),
        evidence_pack_id=UUID("22222222-2222-4222-8222-222222222222"),
        policy_constraints={"evidence_required": True},
        retrieval_trace={"allowed_evidence_refs": [_ANCHOR_REF]},
        context_pack_hash=f"sha256:{'a' * 64}",
    )


def _passage_request() -> PassageRequest:
    return PassageRequest(
        domain=PassageDomain.SCIENCE,
        topic_hint="생태계의 상호 작용",
        word_count=500,
        sentence_complexity=SentenceComplexity.STANDARD,
        paragraph_count=2,
        banned_topics_version="pg-banned-v1",
    )


def _draft() -> PassageDraft:
    return PassageDraft(
        passage_text="생태계의 구성 요소는 서로 영향을 주고받는다.",
        paragraph_count=2,
        evidence_anchor_ids=(_ANCHOR_REF,),
    )


def _execution_context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=UUID("33333333-3333-4333-8333-333333333333"),
        tenant_id="tenant-a",
        capability=Capability.PROBLEM_GENERATION,
        input_snapshot_hash="snapshot-a",
        versions=VersionSet(
            pipeline_version="pipeline-v1",
            engine_version="engine-v1",
            schema_version="schema-v1",
            contract_version="contract-v1",
        ),
    )


def _generator(step: str) -> tuple[PassageGenerator, FakeProvider]:
    provider = FakeProvider((step,), name="fake-passage-generator")
    gateway = LlmGateway(
        {ModelRole.GENERATOR: provider},
        transport_retry={ModelRole.GENERATOR: 0},
    )
    return (
        PassageGenerator(gateway, banned_topics=load_banned_topics()),
        provider,
    )


def test_passage_generator_renders_prompt_and_parses_draft() -> None:
    expected = _draft()
    generator, provider = _generator(expected.model_dump_json())

    actual = asyncio.run(
        generator.generate(
            passage_request=_passage_request(),
            context_pack=_context_pack(),
            execution_context=_execution_context(),
        )
    )

    assert actual == expected
    request = provider.requests[0]
    assert request.prompt_id == "pg.passage.v1"
    assert request.response_schema_name == "PassageDraft"
    assert '"paragraph_count":2' in request.prompt
    assert _ANCHOR_REF in request.prompt
    assert "pg-banned-v1" in request.prompt
    assert "금칙어 우회" in request.prompt
    assert request.generation_params is not None
    assert request.generation_params.temperature == DETERMINISTIC_TEMPERATURE
    assert request.generation_params.seed == LLM_SEED


@pytest.mark.parametrize(
    "response_text",
    ["generation_unavailable", '"generation_unavailable"'],
)
def test_passage_generator_rejects_generation_unavailable(
    response_text: str,
) -> None:
    generator, provider = _generator(response_text)

    with pytest.raises(PassageGenerationUnavailable):
        asyncio.run(
            generator.generate(
                passage_request=_passage_request(),
                context_pack=_context_pack(),
                execution_context=_execution_context(),
            )
        )

    assert len(provider.requests) == 1


def test_passage_generator_rejects_paragraph_count_mismatch() -> None:
    mismatched = _draft().model_copy(update={"paragraph_count": 3})
    generator, _provider = _generator(mismatched.model_dump_json())

    with pytest.raises(PassageDraftRejected, match="paragraph_count"):
        asyncio.run(
            generator.generate(
                passage_request=_passage_request(),
                context_pack=_context_pack(),
                execution_context=_execution_context(),
            )
        )


def test_passage_generator_rejects_unknown_evidence_anchor() -> None:
    unknown = _draft().model_copy(
        update={"evidence_anchor_ids": ("reading:unknown",)}
    )
    generator, _provider = _generator(unknown.model_dump_json())

    with pytest.raises(PassageDraftRejected, match="승인되지 않은 근거"):
        asyncio.run(
            generator.generate(
                passage_request=_passage_request(),
                context_pack=_context_pack(),
                execution_context=_execution_context(),
            )
        )


def test_attach_passage_draft_derives_stable_context_identity() -> None:
    context_pack = _context_pack()
    draft = _draft()

    first = attach_passage_draft(context_pack, draft)
    second = attach_passage_draft(context_pack, draft)

    assert first == second
    assert first.context_pack_id != context_pack.context_pack_id
    assert first.context_pack_hash != context_pack.context_pack_hash
    assert PassageDraft.model_validate(first.retrieval_trace["passage_draft"]) == draft
    payload = first.model_dump(mode="json")
    context_hash = payload.pop("context_pack_hash")
    assert context_hash == sha256_hex(canonical_json(payload))
