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
from ai.contracts.taxonomy import AreaTag
from ai.problem_generation.domain.policy import (
    uses_generated_source_base,
    uses_selected_work_source_base,
)
from ai.problem_generation.infrastructure.grammar_norm import (
    GrammarNormCorpus,
    GrammarNormRow,
    load_grammar_norm_corpus,
    select_node_rows,
)
from ai.problem_generation.infrastructure.lexicon_index import (
    LexiconIndex,
    LexiconIndexEntry,
    load_lexicon_index,
    select_node_entries,
)

_SOURCE_ID = "nikl-kornorms"
_LICENSE_REF = "KOGL-1"
_LEXICON_SOURCE_ID = "stdict"
_LEXICON_LICENSE_REF = "CC BY-SA 2.0 KR"
_LEXICON_ATTRIBUTION = "국립국어원 표준국어대사전"


class GrammarNormGraphContextService:
    """벡터 검색 없이 승인된 어문규범 행만 제공하는 MVP 근거 서비스."""

    def __init__(
        self,
        corpus: GrammarNormCorpus | None = None,
        lexicon_index: LexiconIndex | None = None,
    ) -> None:
        self._corpus = corpus or load_grammar_norm_corpus()
        self._lexicon_index = lexicon_index or load_lexicon_index()
        self._known_grammar_refs = frozenset(
            _reference(row)
            for node_id in self._corpus.mapping.nodes
            for row in select_node_rows(self._corpus, node_id)
        )
        self._known_lexicon_refs = frozenset(
            _lexicon_reference(entry)
            for entries in self._lexicon_index.nodes.values()
            for entry in entries
        )

    async def resolve_generation_context(
        self, request: GraphContextRequest
    ) -> ContextPack:
        return self._resolve(request, GraphContextOperation.GENERATE)

    async def resolve_revision_context(self, request: GraphContextRequest) -> ContextPack:
        if request.current_item_snapshot is None or request.redacted_instruction is None:
            raise ValueError("문항 수정 컨텍스트에는 현재 문항과 마스킹된 지시가 필요하다")
        if request.locked_fields.area_tag is not AreaTag.LANGUAGE:
            raise NotImplementedError("어문규범 수정 컨텍스트는 language만 지원한다")
        return self._resolve(request, GraphContextOperation.REFINE)

    def _resolve(
        self, request: GraphContextRequest, operation: GraphContextOperation
    ) -> ContextPack:
        rows = select_node_rows(self._corpus, request.locked_fields.skill_node_id)
        if rows:
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
        else:
            entries = select_node_entries(
                self._lexicon_index,
                request.locked_fields.skill_node_id,
            )
            anchors = tuple(_lexicon_anchor(entry) for entry in entries)
            trace = {
                "allowed_evidence_refs": [anchor.ref for anchor in anchors],
                "evidence_anchors": [
                    {
                        **anchor.model_dump(mode="json"),
                        "word": entry.word,
                        "pos": entry.pos,
                        "cat": entry.cat,
                        "word_type": entry.word_type,
                        "sense_code": entry.sense_code,
                        "source_revision": entry.source_revision,
                    }
                    for anchor, entry in zip(anchors, entries, strict=True)
                ],
                "attribution": _LEXICON_ATTRIBUTION,
                "mapping_version": self._lexicon_index.version,
            }
        canonical = _canonical(
            {
                "operation": operation.value,
                "request": request.model_dump(mode="json"),
                "trace": trace,
            }
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return ContextPack(
            context_pack_id=uuid5(NAMESPACE_URL, f"context:{digest}"),
            operation=operation,
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
            if not (
                (
                    anchor.kind is EvidencePackAnchorKind.GRAMMAR_RULE
                    and anchor.ref in self._known_grammar_refs
                )
                or (
                    anchor.kind is EvidencePackAnchorKind.DICT_ENTRY
                    and anchor.ref in self._known_lexicon_refs
                )
            )
        )
        return EvidencePathResult(
            valid=not invalid,
            checked_anchor_ids=checked,
            invalid_anchor_ids=invalid,
        )


class AreaDelegatingGraphContextService:
    """T1 정본과 생성·저작물 트랙의 빈 base ContextPack을 한 경계에서 위임한다."""

    def __init__(
        self,
        grammar: GrammarNormGraphContextService | None = None,
    ) -> None:
        self._grammar = grammar or GrammarNormGraphContextService()

    async def resolve_generation_context(
        self,
        request: GraphContextRequest,
    ) -> ContextPack:
        area_tag = request.locked_fields.area_tag
        if uses_generated_source_base(area_tag) or uses_selected_work_source_base(
            area_tag
        ):
            return _empty_base_context(request)
        return await self._grammar.resolve_generation_context(request)

    async def resolve_revision_context(self, request: GraphContextRequest) -> ContextPack:
        # 🔴 **생성 경로와 같은 축으로 가른다.** 종전에는 무조건 문법 서비스로 위임해
        #   비-language 문항이 `NotImplementedError` 로 막혔다 — 출제는 5영역이 열렸는데
        #   **수정은 언어 하나만 되는** 구멍이었다.
        area_tag = request.locked_fields.area_tag
        if uses_generated_source_base(area_tag) or uses_selected_work_source_base(
            area_tag
        ):
            if (
                request.current_item_snapshot is None
                or request.redacted_instruction is None
            ):
                raise ValueError("문항 수정 컨텍스트에는 현재 문항과 마스킹된 지시가 필요하다")
            return _reused_evidence_revision_context(request)
        return await self._grammar.resolve_revision_context(request)

    async def resolve_verification_context(
        self,
        request: GraphContextRequest,
    ) -> ContextPack:
        return await self._grammar.resolve_verification_context(request)

    async def resolve_replacement_context(
        self,
        request: GraphContextRequest,
    ) -> ContextPack:
        return await self._grammar.resolve_replacement_context(request)

    async def verify_evidence_paths(
        self,
        evidence_pack: EvidencePack,
    ) -> EvidencePathResult:
        return await self._grammar.verify_evidence_paths(evidence_pack)


def _empty_base_context(request: GraphContextRequest) -> ContextPack:
    trace: dict[str, object] = {
        "allowed_evidence_refs": [],
        "evidence_anchors": [],
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


def _reused_evidence_revision_context(request: GraphContextRequest) -> ContextPack:
    """생성 트랙·저작물 트랙 문항의 **수정** 컨텍스트 — 근거를 새로 만들지 않는다.

    🔴 **수정은 근거를 새로 조달하는 자리가 아니다.** 원 문항의 evidence 는 생성 때 이미
    R-1 을 통과한 승인분이고, 그 자료(생성 지문·매체 자료·만료 작품 발췌)는 세트에 고정돼
    있다. 그러므로 수정본이 인용할 수 있는 것은 **그 앵커 그대로**다.

    ⚠ 여기서 `_empty_base_context` 를 쓰면 안 된다 — 허용 근거가 0건이 돼 수정본이
    `R-1:기준_자료_없음` 으로 전량 폐기된다. 생성 때는 자료를 **뒤이어 만들어 붙이지만**
    수정 때는 붙일 자료가 이미 있다.
    ⚠ 새 ref 를 지어내는 것은 여전히 막힌다 — 허용 집합이 원 문항의 것으로 닫혀 있어
    `R-1:근거_참조_불일치` 가 잡는다(불변식 2).
    """
    snapshot = request.current_item_snapshot or {}
    raw = snapshot.get("evidence")
    anchors: list[dict[str, object]] = [
        {"ref": entry["ref"], "kind": entry.get("kind"), "quote": entry.get("quote")}
        for entry in (raw if isinstance(raw, list) else [])
        if isinstance(entry, dict) and entry.get("ref")
    ]
    trace: dict[str, object] = {
        "allowed_evidence_refs": [anchor["ref"] for anchor in anchors],
        "evidence_anchors": anchors,
    }
    canonical = _canonical(
        {
            "operation": GraphContextOperation.REFINE.value,
            "request": request.model_dump(mode="json"),
            "trace": trace,
        }
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return ContextPack(
        context_pack_id=uuid5(NAMESPACE_URL, f"context:{digest}"),
        operation=GraphContextOperation.REFINE,
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


def _lexicon_anchor(entry: LexiconIndexEntry) -> EvidencePackAnchor:
    ref = _lexicon_reference(entry)
    quote_hash = _sha256(entry.word)
    return EvidencePackAnchor(
        anchor_id=ref,
        kind=EvidencePackAnchorKind.DICT_ENTRY,
        ref=ref,
        source_id=_LEXICON_SOURCE_ID,
        source_version=entry.source_revision,
        source_content_hash=entry.content_hash,
        quote=entry.word,
        quote_hash=quote_hash,
        license_ref=_LEXICON_LICENSE_REF,
    )


def _lexicon_reference(entry: LexiconIndexEntry) -> str:
    return f"stdict:{entry.sense_code}"


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = ["GrammarNormGraphContextService"]
