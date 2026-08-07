"""결정론 GraphContextService 테스트 대역."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from uuid import NAMESPACE_URL, uuid5

from ai.contracts.graphrag import (
    ContextPack,
    EvidencePack,
    EvidencePathResult,
    GraphContextOperation,
    GraphContextRequest,
)

type FakeGraphStep = tuple[str, ...] | ContextPack | Exception


class FakeGraphContextService:
    """🔴 이 대역은 실 구현이 하지 않는 것을 한다 — 방어 검증용이지 재현이 아니다.

    이 대역이 만든 조건을 '프로덕션에서 일어난다'로 읽지 마라.
    요청 순서대로 근거 참조 또는 예외를 소비한다.
    """

    def __init__(
        self,
        scenario: Sequence[FakeGraphStep] = (("grammar:rule-1",),),
    ) -> None:
        if not scenario:
            raise ValueError("FakeGraphContextService 시나리오는 비어 있을 수 없다")
        self._scenario = tuple(scenario)
        self._position = 0
        self.requests: list[GraphContextRequest] = []

    async def resolve_generation_context(
        self,
        request: GraphContextRequest,
    ) -> ContextPack:
        return self._resolve(request, GraphContextOperation.GENERATE)

    async def resolve_revision_context(
        self,
        request: GraphContextRequest,
    ) -> ContextPack:
        return self._resolve(request, GraphContextOperation.REFINE)

    async def resolve_verification_context(
        self,
        request: GraphContextRequest,
    ) -> ContextPack:
        return self._resolve(request, GraphContextOperation.VERIFY)

    async def resolve_replacement_context(
        self,
        request: GraphContextRequest,
    ) -> ContextPack:
        return self._resolve(request, GraphContextOperation.REPLACE)

    async def verify_evidence_paths(
        self,
        evidence_pack: EvidencePack,
    ) -> EvidencePathResult:
        anchor_ids = tuple(anchor.anchor_id for anchor in evidence_pack.anchors)
        return EvidencePathResult(
            valid=not evidence_pack.missing_requirements,
            checked_anchor_ids=anchor_ids,
            invalid_anchor_ids=(
                () if not evidence_pack.missing_requirements else anchor_ids
            ),
        )

    def _resolve(
        self,
        request: GraphContextRequest,
        operation: GraphContextOperation,
    ) -> ContextPack:
        self.requests.append(request)
        if self._position < len(self._scenario):
            step = self._scenario[self._position]
            self._position += 1
        else:
            step = self._scenario[-1]

        if isinstance(step, Exception):
            raise step
        if isinstance(step, ContextPack):
            return step

        canonical = json.dumps(
            request.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(
            f"{operation.value}:{canonical}:{self._position}".encode()
        ).hexdigest()
        context_pack_id = uuid5(NAMESPACE_URL, f"context:{digest}")
        return ContextPack(
            context_pack_id=context_pack_id,
            operation=operation,
            tenant_id=request.tenant_id,
            target_source=request.target_source,
            weakness_map_id=request.weakness_map_id,
            target_skill_node_ids=request.target_skill_node_ids,
            locked_fields=request.locked_fields,
            pedagogy_paths=(f"path:{request.locked_fields.skill_node_id}",),
            evidence_pack_id=uuid5(NAMESPACE_URL, f"evidence:{digest}"),
            current_item_snapshot=request.current_item_snapshot,
            redacted_instruction=request.redacted_instruction,
            policy_constraints=request.policy_constraints,
            retrieval_trace={"allowed_evidence_refs": list(step)},
            context_pack_hash=f"sha256:{digest}",
        )


__all__ = ["FakeGraphContextService", "FakeGraphStep"]
