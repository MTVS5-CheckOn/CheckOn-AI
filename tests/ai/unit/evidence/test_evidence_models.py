"""EvidenceRef — ERD 1:1 · 앵커 좌표 왕복 · 구분자 안전성 (⚠ 양자 스키마 초안).

정본은 `docs/06_erd.md`의 `EVIDENCE_ITEM` 블록이다. 필드가 ERD 컬럼과 어긋나면 여기서
깨진다 — 근거 없는 개명을 막는 값 대조다(`test_probe_stores.py` ORM 1:1 선례).
"""

from __future__ import annotations

from uuid import UUID

import pytest

from ai.db.models import EvidenceItem
from ai.evidence.models import (
    ANCHOR_RECORD_ID_SEPARATOR,
    EVIDENCE_PACK_ANCHOR,
    EvidenceOwnerKind,
    EvidenceRef,
    decode_anchor_record_id,
    encode_anchor_record_id,
)

_OWNER = UUID("00000000-0000-4000-8000-00000000000a")
_PACK = UUID("00000000-0000-4000-8000-00000000000b")


def _orm_columns(model: type) -> set[str]:
    return set(model.__table__.columns.keys())  # type: ignore[attr-defined]


def test_evidence_ref_is_1to1_with_erd_columns() -> None:
    """필드 집합 == EVIDENCE_ITEM 컬럼 집합. 추가 필드도 누락도 없다."""
    assert set(EvidenceRef.model_fields) == _orm_columns(EvidenceItem)


def test_owner_kind_matches_erd_three_values() -> None:
    """ERD `signal|draft_block|problem_item` — A-2 승인으로 problem_item 편입(#35)."""
    assert {kind.value for kind in EvidenceOwnerKind} == {
        "signal",
        "draft_block",
        "problem_item",
    }


def test_no_realname_fields() -> None:
    """실명·연락처 필드 금지(불변식 3) — alias·논리 참조만."""
    forbidden = {"name", "student_name", "guardian_name", "phone", "guardian_phone", "email"}
    assert not forbidden & set(EvidenceRef.model_fields)


# ── (b) GraphRAG 경로 — 인코딩·디코딩 한 쌍의 왕복 ────────────────


@pytest.mark.parametrize(
    "anchor_id",
    ["a1", "grammar:rule-1", "span/12-34", "한글앵커", "a" * 200],
)
def test_anchor_record_id_round_trip(anchor_id: str) -> None:
    """encode ↔ decode 왕복이 pack·anchor를 그대로 보존한다(property)."""
    record_id = encode_anchor_record_id(_PACK, anchor_id)
    coordinate = decode_anchor_record_id(record_id)
    assert coordinate.evidence_pack_id == _PACK
    assert coordinate.anchor_id == anchor_id


def test_from_anchor_round_trips_through_ref() -> None:
    """EvidenceRef를 거친 왕복도 동일 — 호출부가 f-string으로 조립할 이유가 없다."""
    ref = EvidenceRef.from_anchor(
        tenant_id="t1",
        owner_kind=EvidenceOwnerKind.PROBLEM_ITEM,
        owner_id=_OWNER,
        evidence_pack_id=_PACK,
        anchor_id="grammar:rule-1",
        summary="한글 어문 규정 12항",
    )
    assert ref.source_table == EVIDENCE_PACK_ANCHOR
    coordinate = ref.anchor_coordinate()
    assert coordinate.evidence_pack_id == _PACK
    assert coordinate.anchor_id == "grammar:rule-1"


def test_separator_in_anchor_id_is_rejected() -> None:
    """구분자가 섞이면 생성 시점에 거부 — 조용히 깨진 record_id를 만들지 않는다."""
    with pytest.raises(ValueError, match="구분자"):
        encode_anchor_record_id(_PACK, f"a{ANCHOR_RECORD_ID_SEPARATOR}b")


def test_empty_anchor_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="비어 있을 수 없다"):
        encode_anchor_record_id(_PACK, "")


def test_decode_rejects_non_anchor_record_id() -> None:
    """A 경로 record_id를 앵커로 디코딩하려 하면 실패한다(형식 위반 조용한 통과 금지)."""
    with pytest.raises(ValueError, match="앵커 record_id가 아니다"):
        decode_anchor_record_id("le_1029")


def test_anchor_coordinate_rejects_record_path() -> None:
    ref = EvidenceRef.from_record(
        tenant_id="t1",
        owner_kind=EvidenceOwnerKind.SIGNAL,
        owner_id=_OWNER,
        source_table="learning_events",
        record_id="le_1029",
        summary="지난주 과제 미제출",
    )
    with pytest.raises(ValueError, match="앵커 참조가 아니다"):
        ref.anchor_coordinate()


# ── (a) 기존 A 경로 · 결정론 PK ───────────────────────────────────


def test_from_record_keeps_backend_db_logical_reference() -> None:
    """감지·상담 근거는 백엔드 DB 테이블명 + 원본 PK를 그대로 논리 참조한다."""
    ref = EvidenceRef.from_record(
        tenant_id="t1",
        owner_kind=EvidenceOwnerKind.SIGNAL,
        owner_id=_OWNER,
        source_table="learning_events",
        record_id="le_1029",
        summary="지난주 과제 미제출",
    )
    assert (ref.source_table, ref.record_id) == ("learning_events", "le_1029")


def _anchor_ref(anchor_id: str) -> EvidenceRef:
    return EvidenceRef.from_anchor(
        tenant_id="t1",
        owner_kind=EvidenceOwnerKind.PROBLEM_ITEM,
        owner_id=_OWNER,
        evidence_pack_id=_PACK,
        anchor_id=anchor_id,
        summary="근거 한 줄",
    )


def test_id_is_derived_deterministically() -> None:
    """같은 입력 → 같은 PK. 시계·난수를 쓰지 않는다(조건 3 · 멱등 적재)."""
    assert _anchor_ref("a1").id == _anchor_ref("a1").id


def test_different_anchor_yields_different_id() -> None:
    assert _anchor_ref("a1").id != _anchor_ref("a2").id


def test_ref_is_frozen_and_forbids_extra() -> None:
    ref = EvidenceRef.from_record(
        tenant_id="t1",
        owner_kind=EvidenceOwnerKind.SIGNAL,
        owner_id=_OWNER,
        source_table="learning_events",
        record_id="le_1029",
        summary="지난주 과제 미제출",
    )
    with pytest.raises(ValueError, match="frozen|Instance is frozen"):
        ref.tenant_id = "t2"  # type: ignore[misc]
