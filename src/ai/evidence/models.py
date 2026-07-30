"""EvidenceRef — 산출물이 인용한 근거 1건의 논리 참조.

⚠ **양자 승인 파일**(02_ownership §4 — `evidence/models.py`의 `EvidenceRef` 스키마).
이 커밋은 **A 초안이며 B 승인 대기**다.

정본은 `docs/06_erd.md`의 `EVIDENCE_ITEM` 블록이다. 필드는 ERD 7컬럼과 **1:1**이며
이름을 바꾸지 않는다(근거 없는 개명 금지). DB 스키마·마이그레이션은 이 계층이 만들지
않는다 — `EVIDENCE_ITEM`·`owner_kind` 확장은 #35에서 끝났다.

**두 근거 경로를 한 타입으로 담는다.** `EVIDENCE_ITEM`이 이미 `owner_kind`+`owner_id`
다형 참조 패턴을 쓰므로(`db/models.py` `EvidenceItem` — "다형 소유 — 물리 FK 없음")
근거 축도 같은 패턴으로 가는 것이 테이블 내 일관성이다.

- **(a) 기존 A 경로**(감지·상담) — `source_table`=MySQL 테이블명 · `record_id`=원본 PK
- **(b) GraphRAG 경로**(문항) — `source_table`=`EVIDENCE_PACK_ANCHOR` ·
  `record_id`=`encode_anchor_record_id(evidence_pack_id, anchor_id)`

`…_ref`는 전부 **논리 참조**이고 물리 FK가 아니다. 실명·연락처 필드는 두지 않는다(불변식 3).
GraphRAG 좌표의 인코딩·디코딩은 **이 모듈의 함수 한 쌍**에만 둔다 — 호출부에서 f-string으로
조립하지 않는다(왕복 테스트가 강제).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final, Self
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict

from ai.contracts.graphrag import NonEmptyStr

#: `source_table` 값 — GraphRAG 앵커 경로. 리터럴을 흩뿌리면 오타로 조용히 갈라지므로
#: 이 상수 하나만 쓴다(A 경로의 MySQL 테이블명은 열린 집합이라 enum으로 닫지 않는다).
EVIDENCE_PACK_ANCHOR: Final = "evidence_pack_anchor"

#: `record_id` 안에서 `evidence_pack_id`와 `anchor_id`를 가르는 구분자.
#: `:`는 ref 형태 식별자에 흔해(예: `grammar:rule-1`) 충돌 위험이 있어 `|`를 쓴다.
ANCHOR_RECORD_ID_SEPARATOR: Final = "|"

#: 파생 PK의 uuid5 네임스페이스 접두 — 같은 근거는 같은 id(멱등 적재).
_ID_PREFIX: Final = "checkon:evidence"


class EvidenceOwnerKind(StrEnum):
    """`EVIDENCE_ITEM.owner_kind` — ERD 정본 3값(A-2 승인으로 `problem_item` 편입, #35)."""

    SIGNAL = "signal"
    DRAFT_BLOCK = "draft_block"
    PROBLEM_ITEM = "problem_item"


class AnchorCoordinate(BaseModel):
    """GraphRAG 근거 좌표 — `record_id` 디코딩 결과."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_pack_id: UUID
    anchor_id: NonEmptyStr


def encode_anchor_record_id(evidence_pack_id: UUID, anchor_id: str) -> str:
    """(pack, anchor) → `record_id`. 디코더와 한 쌍이며 왕복이 보존된다.

    구분자가 값에 섞이면 **조용히 깨진 record_id**가 생기므로 여기서 거부한다.
    `evidence_pack_id`는 UUID라 구분자를 가질 수 없지만 대칭을 위해 함께 검사한다.
    """
    if not anchor_id:
        raise ValueError("anchor_id는 비어 있을 수 없다")
    pack_raw = str(evidence_pack_id)
    for label, value in (("evidence_pack_id", pack_raw), ("anchor_id", anchor_id)):
        if ANCHOR_RECORD_ID_SEPARATOR in value:
            raise ValueError(
                f"{label}에 구분자 {ANCHOR_RECORD_ID_SEPARATOR!r}가 포함될 수 없다: {value!r}"
            )
    return f"{pack_raw}{ANCHOR_RECORD_ID_SEPARATOR}{anchor_id}"


def decode_anchor_record_id(record_id: str) -> AnchorCoordinate:
    """`record_id` → (pack, anchor). 형식 위반은 ValueError(조용한 통과 금지)."""
    pack_raw, separator, anchor_id = record_id.partition(ANCHOR_RECORD_ID_SEPARATOR)
    if not separator:
        raise ValueError(
            f"앵커 record_id가 아니다(구분자 {ANCHOR_RECORD_ID_SEPARATOR!r} 없음): {record_id!r}"
        )
    return AnchorCoordinate(evidence_pack_id=UUID(pack_raw), anchor_id=anchor_id)


def _derive_id(owner_id: UUID, source_table: str, record_id: str) -> UUID:
    """PK를 입력에서 파생 — 같은 소유자·같은 근거면 같은 id(결정론·멱등 적재).

    시계·난수를 쓰지 않으므로 resolver의 결정론 조건(09 §2-12-② 조건 3)을 깨지 않는다.
    """
    return uuid5(NAMESPACE_URL, f"{_ID_PREFIX}:{owner_id}:{source_table}:{record_id}")


class EvidenceRef(BaseModel):
    """`EVIDENCE_ITEM` 한 행과 1:1인 근거 참조 (⚠ 양자 — B 승인 대기).

    필드는 ERD 7컬럼 그대로다: `id`·`tenant_id`·`owner_kind`·`owner_id`·
    `source_table`·`record_id`·`summary`. 추가 필드를 두지 않는다 — GraphRAG 좌표는
    `source_table`+`record_id` 규약으로 표현한다(모듈 docstring 참조).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    tenant_id: NonEmptyStr
    owner_kind: EvidenceOwnerKind
    owner_id: UUID
    source_table: NonEmptyStr
    record_id: NonEmptyStr
    summary: NonEmptyStr

    @classmethod
    def from_record(
        cls,
        *,
        tenant_id: str,
        owner_kind: EvidenceOwnerKind,
        owner_id: UUID,
        source_table: str,
        record_id: str,
        summary: str,
    ) -> Self:
        """(a) 기존 A 경로 — MySQL 원본 record_id 논리 참조(감지·상담 근거)."""
        return cls(
            id=_derive_id(owner_id, source_table, record_id),
            tenant_id=tenant_id,
            owner_kind=owner_kind,
            owner_id=owner_id,
            source_table=source_table,
            record_id=record_id,
            summary=summary,
        )

    @classmethod
    def from_anchor(
        cls,
        *,
        tenant_id: str,
        owner_kind: EvidenceOwnerKind,
        owner_id: UUID,
        evidence_pack_id: UUID,
        anchor_id: str,
        summary: str,
    ) -> Self:
        """(b) GraphRAG 경로 — `EvidencePackAnchor` 좌표를 논리 참조로 인코딩한다."""
        record_id = encode_anchor_record_id(evidence_pack_id, anchor_id)
        return cls(
            id=_derive_id(owner_id, EVIDENCE_PACK_ANCHOR, record_id),
            tenant_id=tenant_id,
            owner_kind=owner_kind,
            owner_id=owner_id,
            source_table=EVIDENCE_PACK_ANCHOR,
            record_id=record_id,
            summary=summary,
        )

    def anchor_coordinate(self) -> AnchorCoordinate:
        """GraphRAG 경로일 때만 좌표를 되돌린다. A 경로면 ValueError."""
        if self.source_table != EVIDENCE_PACK_ANCHOR:
            raise ValueError(
                f"GraphRAG 앵커 참조가 아니다(source_table={self.source_table!r})"
            )
        return decode_anchor_record_id(self.record_id)


__all__ = [
    "ANCHOR_RECORD_ID_SEPARATOR",
    "EVIDENCE_PACK_ANCHOR",
    "AnchorCoordinate",
    "EvidenceOwnerKind",
    "EvidenceRef",
    "decode_anchor_record_id",
    "encode_anchor_record_id",
]
