"""GraphRAG ContextPack·EvidencePack·서비스 경계 계약 검증."""

from uuid import UUID

import pytest

from ai.contracts.graphrag import (
    ContextLockedFields,
    ContextPack,
    EvidenceCoverage,
    EvidencePack,
    EvidencePackAnchor,
    EvidencePackAnchorKind,
    EvidencePathResult,
    EvidenceRetrieval,
    EvidenceRetrievalMode,
    GraphContextOperation,
    GraphContextRequest,
    GraphContextService,
)
from ai.contracts.problem_generation import TargetSource
from ai.contracts.taxonomy import AreaTag, ItemFormat, TypeTag

CONTEXT_PACK_ID = UUID("00000000-0000-4000-8000-000000000201")
EVIDENCE_PACK_ID = UUID("00000000-0000-4000-8000-000000000202")
WEAKNESS_MAP_ID = UUID("00000000-0000-4000-8000-000000000203")
HASH_A = f"sha256:{'a' * 64}"
HASH_B = f"sha256:{'b' * 64}"
HASH_C = f"sha256:{'c' * 64}"


def _locked_fields() -> ContextLockedFields:
    return ContextLockedFields(
        target_ref="student-alias",
        area_tag=AreaTag.LANGUAGE,
        type_tags=(TypeTag.CONCEPT,),
        skill_node_id="language.grammar.sound_change",
        item_format=ItemFormat.MCQ,
    )


def _request() -> GraphContextRequest:
    return GraphContextRequest(
        tenant_id="tenant-1",
        target_source=TargetSource.WEAKNESS_AUTO,
        weakness_map_id=WEAKNESS_MAP_ID,
        target_skill_node_ids=("language.grammar.sound_change",),
        locked_fields=_locked_fields(),
        policy_constraints={"banned_topics_version": "pg-banned-v1"},
    )


def _anchor() -> EvidencePackAnchor:
    return EvidencePackAnchor(
        anchor_id="grammar-anchor-1",
        kind=EvidencePackAnchorKind.GRAMMAR_RULE,
        ref="grammar.rule.sound-change.1",
        source_id="grammar-source",
        source_version="v1",
        source_content_hash=HASH_A,
        quote="전문가 검수 대기 규칙 발췌",
        quote_hash=HASH_B,
        graph_path_edge_ids=("edge-1",),
        license_ref="license-1",
        rights_status="approved",
    )


def _evidence_pack() -> EvidencePack:
    return EvidencePack(
        evidence_pack_id=EVIDENCE_PACK_ID,
        graph_snapshot_id="graph-snapshot-v1",
        anchors=(_anchor(),),
        coverage=(
            EvidenceCoverage(
                item_field="answer.correct_no",
                supporting_anchor_ids=("grammar-anchor-1",),
            ),
        ),
        retrieval=EvidenceRetrieval(
            mode=EvidenceRetrievalMode.LOCAL,
            query_hash=HASH_C,
            returned_node_ids=("grammar-node-1",),
            returned_path_ids=("path-1",),
            scores=(1.0,),
        ),
        evidence_pack_hash=HASH_B,
    )


def _context_pack() -> ContextPack:
    return ContextPack(
        context_pack_id=CONTEXT_PACK_ID,
        operation=GraphContextOperation.GENERATE,
        tenant_id="tenant-1",
        target_source=TargetSource.WEAKNESS_AUTO,
        weakness_map_id=WEAKNESS_MAP_ID,
        target_skill_node_ids=("language.grammar.sound_change",),
        locked_fields=_locked_fields(),
        pedagogy_paths=("path-1",),
        evidence_pack_id=EVIDENCE_PACK_ID,
        policy_constraints={"banned_topics_version": "pg-banned-v1"},
        retrieval_trace={
            "query_hash": HASH_C,
            "returned_node_ids": ["grammar-node-1"],
        },
        context_pack_hash=HASH_A,
    )


def test_context_pack_roundtrip_preserves_fixed_schema() -> None:
    pack = _context_pack()

    restored = ContextPack.model_validate(pack.model_dump(mode="json"))

    assert restored == pack
    assert restored.context_pack_schema_version == "ctx-1"
    assert restored.operation is GraphContextOperation.GENERATE


def test_context_request_rejects_duplicate_target_nodes() -> None:
    with pytest.raises(ValueError, match="target_skill_node_ids"):
        GraphContextRequest(
            tenant_id="tenant-1",
            target_source=TargetSource.WEAKNESS_AUTO,
            weakness_map_id=WEAKNESS_MAP_ID,
            target_skill_node_ids=("grammar-node-1", "grammar-node-1"),
            locked_fields=_locked_fields(),
            policy_constraints={},
        )


def test_evidence_pack_roundtrip_preserves_approved_evidence() -> None:
    pack = _evidence_pack()

    restored = EvidencePack.model_validate(pack.model_dump(mode="json"))

    assert restored == pack
    assert restored.evidence_pack_schema_version == "evp-1"
    assert restored.anchors[0].rights_status == "approved"


def test_evidence_anchor_rejects_unapproved_rights() -> None:
    payload = _anchor().model_dump(mode="json")
    payload["rights_status"] = "expired"

    with pytest.raises(ValueError, match="rights_status"):
        EvidencePackAnchor.model_validate(payload)


def test_evidence_anchor_requires_quote_hash_pair() -> None:
    payload = _anchor().model_dump(mode="json")
    payload["quote_hash"] = None

    with pytest.raises(ValueError, match="quote"):
        EvidencePackAnchor.model_validate(payload)


def test_evidence_pack_rejects_unknown_coverage_anchor() -> None:
    payload = _evidence_pack().model_dump(mode="json")
    payload["coverage"][0]["supporting_anchor_ids"] = ["missing-anchor"]

    with pytest.raises(ValueError, match="존재하지 않는 anchor_id"):
        EvidencePack.model_validate(payload)


def test_evidence_path_result_requires_invalid_ids_when_not_valid() -> None:
    with pytest.raises(ValueError, match="valid"):
        EvidencePathResult(valid=False, checked_anchor_ids=("grammar-anchor-1",))


class _GraphContextServiceStub:
    async def resolve_generation_context(
        self,
        request: GraphContextRequest,
    ) -> ContextPack:
        del request
        return _context_pack()

    async def resolve_revision_context(
        self,
        request: GraphContextRequest,
    ) -> ContextPack:
        del request
        return _context_pack()

    async def resolve_verification_context(
        self,
        request: GraphContextRequest,
    ) -> ContextPack:
        del request
        return _context_pack()

    async def resolve_replacement_context(
        self,
        request: GraphContextRequest,
    ) -> ContextPack:
        del request
        return _context_pack()

    async def verify_evidence_paths(
        self,
        evidence_pack: EvidencePack,
    ) -> EvidencePathResult:
        return EvidencePathResult(
            valid=True,
            checked_anchor_ids=tuple(
                anchor.anchor_id for anchor in evidence_pack.anchors
            ),
        )


def test_graph_context_service_protocol_is_structural() -> None:
    assert isinstance(_GraphContextServiceStub(), GraphContextService)


def test_context_contract_forbids_reserved_item_formats() -> None:
    payload = _locked_fields().model_dump(mode="json")
    payload["item_format"] = "short"

    with pytest.raises(ValueError, match="item_format"):
        ContextLockedFields.model_validate(payload)


def test_context_request_roundtrip() -> None:
    request = _request()
    assert GraphContextRequest.model_validate(request.model_dump(mode="json")) == request
