"""어문규범 CSV로 생성용 ContextPack을 만드는 최소 GraphContext 구현."""

from __future__ import annotations

import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

from ai.contracts.graphrag import (
    ContextPack,
    EvidencePack,
    EvidencePackAnchor,
    EvidencePackAnchorKind,
    EvidencePathResult,
    GraphContextOperation,
    GraphContextRequest,
)
from ai.problem_generation.infrastructure.grammar_norm import (
    GrammarNormCorpus,
    GrammarNormRow,
    load_grammar_norm_corpus,
    select_node_rows,
)

_SOURCE_ID = "nikl-kornorms"
_LICENSE_REF = "KOGL-1"


class GrammarNormGraphContextService:
    """벡터 검색 없이 승인된 어문규범 행만 제공하는 MVP 근거 서비스."""

    def __init__(self, corpus: GrammarNormCorpus | None = None) -> None:
        self._corpus = corpus or load_grammar_norm_corpus()
        self._known_refs = frozenset(
            _reference(row)
            for node_id in self._corpus.mapping.nodes
            for row in select_node_rows(self._corpus, node_id)
        )

    async def resolve_generation_context(
        self, request: GraphContextRequest
    ) -> ContextPack:
        rows = select_node_rows(self._corpus, request.locked_fields.skill_node_id)
        anchors = tuple(_anchor(row, self._corpus.version) for row in rows)
        trace = {
            "allowed_evidence_refs": [anchor.ref for anchor in anchors],
            "evidence_anchors": [
                {
                    **anchor.model_dump(mode="json"),
                    "title": row.title,
                }
                for anchor, row in zip(anchors, rows, strict=True)
            ],
            "attribution": self._corpus.attribution,
            "mapping_version": self._corpus.mapping.version,
        }
        canonical = _canonical(
            {
                "operation": GraphContextOperation.GENERATE.value,
                "request": request.model_dump(mode="json"),
                "trace": trace,
            }
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return ContextPack(
            context_pack_id=uuid5(NAMESPACE_URL, f"context:{digest}"),
            operation=GraphContextOperation.GENERATE,
            tenant_id=request.tenant_id,
            target_source=request.target_source,
            weakness_map_id=request.weakness_map_id,
            target_skill_node_ids=request.target_skill_node_ids,
            locked_fields=request.locked_fields,
            pedagogy_paths=(f"skill:{request.locked_fields.skill_node_id}",),
            evidence_pack_id=uuid5(NAMESPACE_URL, f"evidence:{digest}"),
            current_item_snapshot=request.current_item_snapshot,
            redacted_instruction=request.redacted_instruction,
            policy_constraints=request.policy_constraints,
            retrieval_trace=trace,
            context_pack_hash=f"sha256:{digest}",
        )

    async def resolve_revision_context(self, request: GraphContextRequest) -> ContextPack:
        del request
        raise NotImplementedError("MVP는 문항 수정 근거 조회를 지원하지 않는다")

    async def resolve_verification_context(
        self, request: GraphContextRequest
    ) -> ContextPack:
        del request
        raise NotImplementedError("MVP는 별도 검증 근거 조회를 지원하지 않는다")

    async def resolve_replacement_context(
        self, request: GraphContextRequest
    ) -> ContextPack:
        del request
        raise NotImplementedError("MVP는 문항 교체 근거 조회를 지원하지 않는다")

    async def verify_evidence_paths(
        self, evidence_pack: EvidencePack
    ) -> EvidencePathResult:
        checked = tuple(anchor.anchor_id for anchor in evidence_pack.anchors)
        invalid = tuple(
            anchor.anchor_id
            for anchor in evidence_pack.anchors
            if anchor.kind is not EvidencePackAnchorKind.GRAMMAR_RULE
            or anchor.ref not in self._known_refs
        )
        return EvidencePathResult(
            valid=not invalid,
            checked_anchor_ids=checked,
            invalid_anchor_ids=invalid,
        )


def _anchor(row: GrammarNormRow, version: str) -> EvidencePackAnchor:
    ref = _reference(row)
    quote_hash = _sha256(row.keyword)
    return EvidencePackAnchor(
        anchor_id=ref,
        kind=EvidencePackAnchorKind.GRAMMAR_RULE,
        ref=ref,
        source_id=_SOURCE_ID,
        source_version=version,
        source_content_hash=quote_hash,
        quote=row.keyword,
        quote_hash=quote_hash,
        license_ref=_LICENSE_REF,
    )


def _reference(row: GrammarNormRow) -> str:
    return f"kornorms:{row.regulation_code}:{row.regulation_no}"


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = ["GrammarNormGraphContextService"]
