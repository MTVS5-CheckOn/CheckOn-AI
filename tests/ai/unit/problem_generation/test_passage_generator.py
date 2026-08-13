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
from ai.contracts.llm import FieldMissing, ModelRole
from ai.contracts.problem_generation import (
    MediaSourceKind,
    MediaSourceRequest,
    PassageDomain,
    PassageDraft,
    PassageRequest,
    SentenceComplexity,
    SourceMaterialDraft,
    SourceMaterialRequest,
    SpeechWritingSourceKind,
    SpeechWritingSourceRequest,
    TargetSource,
)
from ai.contracts.taxonomy import AreaTag, ItemFormat, TypeTag
from ai.llm.determinism import LLM_SEED
from ai.llm.gateway import LlmGateway
from ai.problem_generation.application.passage_generator import (
    PassageDraftRejected,
    PassageGenerationUnavailable,
    PassageGenerator,
    SourceMaterialDraftRejected,
    SourceMaterialGenerator,
    attach_passage_draft,
    attach_source_material_draft,
    generated_material_ref,
)
from ai.problem_generation.domain.identity import canonical_json, sha256_hex
from ai.problem_generation.infrastructure.config import load_area_specs, load_banned_topics

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


def _source_material_generator(
    step: str,
) -> tuple[SourceMaterialGenerator, FakeProvider]:
    provider = FakeProvider((step,), name="fake-source-material-generator")
    gateway = LlmGateway(
        {ModelRole.GENERATOR: provider},
        transport_retry={ModelRole.GENERATOR: 0},
    )
    return (
        SourceMaterialGenerator(
            gateway,
            banned_topics=load_banned_topics(),
            area_specs=load_area_specs(),
        ),
        provider,
    )


def _material_context(area_tag: AreaTag, skill_node_id: str) -> ContextPack:
    payload = _context_pack().model_dump(mode="python")
    payload["target_skill_node_ids"] = (skill_node_id,)
    payload["locked_fields"] = {
        **payload["locked_fields"],
        "area_tag": area_tag,
        "skill_node_id": skill_node_id,
    }
    return ContextPack.model_validate(payload)


def _empty_context(area_tag: AreaTag, skill_node_id: str) -> ContextPack:
    payload = _material_context(area_tag, skill_node_id).model_dump(mode="python")
    payload["retrieval_trace"] = {
        "allowed_evidence_refs": [],
        "evidence_anchors": [],
    }
    return ContextPack.model_validate(payload)


def _material_draft() -> SourceMaterialDraft:
    return SourceMaterialDraft(
        material_text="학생 A가 발표 자료를 활용해 청중에게 핵심 내용을 설명했다.",
        evidence_anchor_ids=(_ANCHOR_REF,),
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
    #: 🔴 **(8/13) `temperature`는 안 실린다** — `gpt-5.6-luna`가 기본값 외 값을 400으로
    #:   거부해 어댑터가 「값이 없으면 안 보낸다」로 흡수했다(99 #51). 종전 이 줄은
    #:   `== DETERMINISTIC_TEMPERATURE`로 **결정론 온도가 실린다**를 지키고 있었다.
    #:   재현 축은 이제 `seed` 하나다(8/4 실측 — 재현을 만든 것은 seed였다).
    assert request.generation_params.temperature is None
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


def test_generated_passage_becomes_the_only_approved_anchor() -> None:
    base = _empty_context(AreaTag.READING, "reading.infer")
    raw = _draft().model_copy(update={"evidence_anchor_ids": ("model-placeholder",)})
    generator, _provider = _generator(raw.model_dump_json())

    grounded = asyncio.run(
        generator.generate(
            passage_request=_passage_request(),
            context_pack=base,
            execution_context=_execution_context(),
        )
    )
    attached = attach_passage_draft(base, grounded)
    expected_ref = generated_material_ref(
        kind="passage_span",
        text=grounded.passage_text,
    )

    assert grounded.evidence_anchor_ids == (expected_ref,)
    assert attached.retrieval_trace["allowed_evidence_refs"] == [expected_ref]
    assert attached.retrieval_trace["evidence_anchors"] == [
        {
            "kind": "passage_span",
            "ref": expected_ref,
            "quote": grounded.passage_text,
            "content_sha256": sha256_hex(grounded.passage_text),
            "start": 0,
            "end": len(grounded.passage_text),
        }
    ]
    assert attach_passage_draft(attached, grounded) == attached


@pytest.mark.parametrize(
    ("source_request", "skill_node_id", "label"),
    [
        (
            SpeechWritingSourceRequest(
                source_kind=SpeechWritingSourceKind.PRESENTATION,
                topic_hint="교내 자원 절약",
                banned_topics_version="pg-banned-v1",
            ),
            "speech_writing.speech.strategy",
            "화법과작문",
        ),
        (
            MediaSourceRequest(
                source_kind=MediaSourceKind.PAIRED,
                topic_hint="온라인 정보 검증",
                banned_topics_version="pg-banned-v1",
            ),
            "media.reception.credibility",
            "매체",
        ),
    ],
)
def test_source_material_generator_runs_each_new_area(
    source_request: SourceMaterialRequest,
    skill_node_id: str,
    label: str,
) -> None:
    expected = _material_draft()
    generator, provider = _source_material_generator(expected.model_dump_json())

    actual = asyncio.run(
        generator.generate_source_material(
            source_request=source_request,
            context_pack=_material_context(source_request.area_tag, skill_node_id),
            execution_context=_execution_context(),
        )
    )

    assert actual == expected
    request = provider.requests[0]
    assert request.prompt_id == "pg.source_material.v1"
    assert request.response_schema_name == "SourceMaterialDraft"
    assert f"영역: {label}" in request.prompt
    assert source_request.source_kind.value in request.prompt


def test_source_material_generator_rejects_empty_evidence() -> None:
    generator, _provider = _source_material_generator(
        '{"material_text":"근거 없는 자료","evidence_anchor_ids":[]}'
    )
    source_request = MediaSourceRequest(
        source_kind=MediaSourceKind.SINGLE,
        banned_topics_version="pg-banned-v1",
    )

    with pytest.raises(FieldMissing):
        asyncio.run(
            generator.generate_source_material(
                source_request=source_request,
                context_pack=_material_context(
                    AreaTag.MEDIA, "media.reception.information"
                ),
                execution_context=_execution_context(),
            )
        )


def test_source_material_generator_rejects_unknown_evidence() -> None:
    unknown = _material_draft().model_copy(
        update={"evidence_anchor_ids": ("media:unknown",)}
    )
    generator, _provider = _source_material_generator(unknown.model_dump_json())
    source_request = MediaSourceRequest(
        source_kind=MediaSourceKind.SINGLE,
        banned_topics_version="pg-banned-v1",
    )

    with pytest.raises(SourceMaterialDraftRejected, match="승인되지 않은 근거"):
        asyncio.run(
            generator.generate_source_material(
                source_request=source_request,
                context_pack=_material_context(
                    AreaTag.MEDIA, "media.reception.information"
                ),
                execution_context=_execution_context(),
            )
        )


def test_attach_source_material_draft_derives_stable_context_identity() -> None:
    context_pack = _material_context(AreaTag.MEDIA, "media.reception.information")
    draft = _material_draft()

    first = attach_source_material_draft(context_pack, draft)
    second = attach_source_material_draft(context_pack, draft)

    assert first == second
    assert first.context_pack_id != context_pack.context_pack_id
    assert SourceMaterialDraft.model_validate(
        first.retrieval_trace["source_material_draft"]
    ) == draft


@pytest.mark.parametrize(
    ("area_tag", "skill_node_id", "source_request"),
    [
        (
            AreaTag.SPEECH_WRITING,
            "speech_writing.writing.material",
            SpeechWritingSourceRequest(
                source_kind=SpeechWritingSourceKind.WRITING_SOURCES,
                banned_topics_version="pg-banned-v1",
            ),
        ),
        (
            AreaTag.MEDIA,
            "media.reception.credibility",
            MediaSourceRequest(
                source_kind=MediaSourceKind.PAIRED,
                banned_topics_version="pg-banned-v1",
            ),
        ),
    ],
)
def test_generated_source_material_becomes_source_claim_anchor(
    area_tag: AreaTag,
    skill_node_id: str,
    source_request: SourceMaterialRequest,
) -> None:
    base = _empty_context(area_tag, skill_node_id)
    raw = _material_draft().model_copy(
        update={"evidence_anchor_ids": ("model-placeholder",)}
    )
    generator, _provider = _source_material_generator(raw.model_dump_json())

    grounded = asyncio.run(
        generator.generate_source_material(
            source_request=source_request,
            context_pack=base,
            execution_context=_execution_context(),
        )
    )
    attached = attach_source_material_draft(base, grounded)
    expected_ref = generated_material_ref(
        kind="source_claim",
        text=grounded.material_text,
    )

    assert grounded.evidence_anchor_ids == (expected_ref,)
    assert attached.retrieval_trace["allowed_evidence_refs"] == [expected_ref]
    anchors = attached.retrieval_trace["evidence_anchors"]
    assert isinstance(anchors, list)
    anchor = anchors[0]
    assert anchor == {
        "kind": "source_claim",
        "ref": expected_ref,
        "quote": grounded.material_text,
        "content_sha256": sha256_hex(grounded.material_text),
        "start": 0,
        "end": len(grounded.material_text),
    }
    assert attach_source_material_draft(attached, grounded) == attached
