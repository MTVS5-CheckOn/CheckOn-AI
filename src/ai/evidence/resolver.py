"""근거 해소 — 앵커 → 실제 저장 근거 대조 (A 단독 소유 · **양자 아님**).

사양: `part_b/09_integration_proposals.md` §2-12-②(B 요구 조건 5개) ·
`part_b/11_graphrag_knowledge_layer.md` §4.2·§5 · `part_b/06_quality_gates.md` §3.

**역할 경계 (겹치지 않는다):**

- `GraphContextService.verify_evidence_paths`(**B 소유**, `contracts/graphrag.py`)
  = **그래프 내부 검증** — anchor가 그래프에 실존하는가, `graph_path_edge_ids`가
  유효한가. 반환 `EvidencePathResult`는 `valid`·`checked_anchor_ids`·
  `invalid_anchor_ids` 3필드뿐이며 **해소된 본문을 담지 않는다.**
- 이 모듈(**A 소유**) = **앵커 → 실제 근거 텍스트 해소** — `quote`·`quote_hash`·
  `source_content_hash` 대조, 권리 이중 확인, 실패 시 fail-closed 신호.
- 게이트 ① **R-1은 둘 다 통과해야 pass**(`11` §5).

`EvidencePathResult`와 필드명·의미를 겹치지 않는다 — 이름 재사용 금지(A-4에서
`graph_version` 재사용을 금지한 것과 같은 이유).

**조건 5개 대응:**

1. **fail-closed** — 해소 0건이면 `EvidenceResolutionFailed`를 올린다. 빈 결과를
   성공으로 반환하지 않는다(`ResolvedEvidence.resolved`가 `min_length=1`로 구조 강제).
2. **권리 게이트** — `rights_status != approved`는 제외하고 그 사실을 `excluded`에
   드러낸다(조용한 누락 금지). `EvidencePackAnchor.rights_status`가 이미
   `Literal["approved"]`라 Pack 생성 시점에 1차 차단되므로 여기서는 **이중 확인**이다
   (`11` §4.2 불변식 ①).
3. **결정론** — 시계·난수를 이 계층에서 만들지 않는다. `EvidenceRef.id`도 입력에서
   파생한다(`models._derive_id`). 필요하면 주입한다(03 §1).
4. **예산 불변** — 내부 재시도 루프를 두지 않는다. 문항당 `item_attempt` 총 3회 공통
   예산을 그대로 소모한다(FIX-06). `tenacity`를 쓰지 않는다.
5. **blind 무오염** — 반환값이 verifier 페이로드로 흘러들지 않는다(`05` §4.3).
   특히 `quote`는 근거 인용문이라 사실상 정답 힌트다 — 회귀는
   `tests/ai/contract/test_evidence_blind_isolation.py`가 고정한다.

**부분 제외 vs 전부 실패 (헷갈리지 말 것):**

- 일부 앵커가 제외됐지만 **하나라도 해소되면** → `ResolvedEvidence` 반환(`excluded`에 사유).
- **해소 0건**(전부 제외·전부 실패) → `EvidenceResolutionFailed` 예외.
  상위 워크플로가 `verification_unavailable`로 수렴시킨다(`06` §3).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ai.contracts.graphrag import (
    EvidencePack,
    EvidencePackAnchor,
    NonEmptyStr,
    Sha256Hash,
)
from ai.evidence.models import EvidenceOwnerKind, EvidenceRef
from ai.runtime.errors import DomainException

_APPROVED = "approved"


class EvidenceResolutionFailed(DomainException):
    """근거를 하나도 해소하지 못했다 — **fail-closed 신호**(조건 1).

    상위(문항 워크플로)는 이 예외를 `verification_unavailable`로 수렴시킨다(`06` §3).
    빈 결과를 조용한 성공으로 반환하지 않기 위한 신호다. 사전에 없는 코드는 만들지 않고
    `INTERNAL`로 매핑한다(`error_codes` §1 · `LedgerWriteFailed` 선례).
    """

    code = "INTERNAL"
    http_status = 500


class ExclusionReason(StrEnum):
    """앵커가 해소 대상에서 빠진 사유 — 조용한 누락을 막는 구조(조건 2)."""

    RIGHTS_NOT_APPROVED = "rights_not_approved"
    QUOTE_MISMATCH = "quote_mismatch"
    CONTENT_HASH_MISMATCH = "content_hash_mismatch"
    NOT_FOUND = "not_found"


class ExcludedAnchor(BaseModel):
    """제외된 앵커 1건 — 무엇이 왜 빠졌는지."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    anchor_id: NonEmptyStr
    reason: ExclusionReason


class ResolvedAnchor(BaseModel):
    """해소에 성공한 앵커 1건 — 대조를 통과한 근거."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    anchor_id: NonEmptyStr
    evidence_ref: EvidenceRef
    source_content_hash: Sha256Hash
    quote: str | None = Field(default=None, min_length=1)
    """검증을 통과한 인용문. **verifier 페이로드로 넘기지 않는다**(조건 5)."""


class ResolvedEvidence(BaseModel):
    """해소 결과 — A 단독 소유 타입(양자 아님).

    `resolved`가 `min_length=1`이라 **빈 성공 결과를 만들 수 없다**(조건 1을 타입으로 강제).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_pack_id: UUID
    resolved: tuple[ResolvedAnchor, ...] = Field(min_length=1)
    excluded: tuple[ExcludedAnchor, ...] = ()


@runtime_checkable
class EvidenceResolver(Protocol):
    """앵커를 실제 근거로 해소한다. 저장소 조회·대조만 하며 LLM을 호출하지 않는다."""

    async def resolve(
        self,
        *,
        pack: EvidencePack,
        anchor_ids: Sequence[str],
        tenant_id: str,
        owner_kind: EvidenceOwnerKind,
        owner_id: UUID,
    ) -> ResolvedEvidence:
        """해소 성공분을 돌려준다. 하나도 못 하면 `EvidenceResolutionFailed`."""
        ...


class StoredEvidence(BaseModel):
    """저장소가 보관 중인 근거 1건 — 대조 대상(해소의 '정답지')."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_content_hash: Sha256Hash
    summary: NonEmptyStr
    quote: str | None = Field(default=None, min_length=1)


def classify_anchor(
    anchor: EvidencePackAnchor,
    stored: StoredEvidence | None,
) -> ExclusionReason | None:
    """앵커 1건의 대조 판정 — 순수 함수. 통과면 None, 아니면 제외 사유.

    계산과 I/O를 분리한다(03 §2) — 저장소 조회는 호출자가 하고 판정만 여기서 한다.
    검사 순서는 고정이라 같은 입력에 같은 사유가 나온다(조건 3).
    """
    if anchor.rights_status != _APPROVED:  # 이중 확인 — 타입상 도달 불가(조건 2)
        return ExclusionReason.RIGHTS_NOT_APPROVED
    if stored is None:
        return ExclusionReason.NOT_FOUND
    if anchor.source_content_hash != stored.source_content_hash:
        return ExclusionReason.CONTENT_HASH_MISMATCH
    if anchor.quote is not None and anchor.quote != stored.quote:
        return ExclusionReason.QUOTE_MISMATCH
    return None


class FakeEvidenceResolver:
    """결정론 Fake — 시나리오 주입식. 테스트·CI 기본값.

    `store`가 저장소 대역이며, 실제 대조 판정(`classify_anchor`)을 그대로 태운다.
    시계·난수를 쓰지 않고 `anchor_ids` 순서대로 처리하므로 같은 입력에 같은 결과다.
    재시도 루프가 없다(조건 4).
    """

    def __init__(self, store: Mapping[str, StoredEvidence] | None = None) -> None:
        self._store = dict(store or {})
        self.calls: list[tuple[UUID, tuple[str, ...]]] = []

    async def resolve(
        self,
        *,
        pack: EvidencePack,
        anchor_ids: Sequence[str],
        tenant_id: str,
        owner_kind: EvidenceOwnerKind,
        owner_id: UUID,
    ) -> ResolvedEvidence:
        self.calls.append((pack.evidence_pack_id, tuple(anchor_ids)))
        by_id = {anchor.anchor_id: anchor for anchor in pack.anchors}

        resolved: list[ResolvedAnchor] = []
        excluded: list[ExcludedAnchor] = []
        for anchor_id in anchor_ids:
            anchor = by_id.get(anchor_id)
            if anchor is None:  # Pack에 없는 앵커 — 조용히 무시하지 않는다
                excluded.append(
                    ExcludedAnchor(anchor_id=anchor_id, reason=ExclusionReason.NOT_FOUND)
                )
                continue
            stored = self._store.get(anchor_id)
            reason = classify_anchor(anchor, stored)
            if reason is not None or stored is None:
                excluded.append(
                    ExcludedAnchor(
                        anchor_id=anchor_id,
                        reason=reason or ExclusionReason.NOT_FOUND,
                    )
                )
                continue
            resolved.append(
                ResolvedAnchor(
                    anchor_id=anchor_id,
                    evidence_ref=EvidenceRef.from_anchor(
                        tenant_id=tenant_id,
                        owner_kind=owner_kind,
                        owner_id=owner_id,
                        evidence_pack_id=pack.evidence_pack_id,
                        anchor_id=anchor_id,
                        summary=stored.summary,
                    ),
                    source_content_hash=stored.source_content_hash,
                    quote=stored.quote,
                )
            )

        if not resolved:  # fail-closed — 빈 결과를 성공으로 돌려주지 않는다(조건 1)
            raise EvidenceResolutionFailed(
                "해소된 근거가 없다",
                {
                    "evidence_pack_id": str(pack.evidence_pack_id),
                    "excluded": [item.model_dump(mode="json") for item in excluded],
                },
            )
        return ResolvedEvidence(
            evidence_pack_id=pack.evidence_pack_id,
            resolved=tuple(resolved),
            excluded=tuple(excluded),
        )


__all__ = [
    "EvidenceResolutionFailed",
    "EvidenceResolver",
    "ExcludedAnchor",
    "ExclusionReason",
    "FakeEvidenceResolver",
    "ResolvedAnchor",
    "ResolvedEvidence",
    "StoredEvidence",
    "classify_anchor",
]
