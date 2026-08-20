"""contracts/diagnosis.py 계약 검증 — 입력·판정·데이터 부족 상태."""

from datetime import UTC, date, datetime

import pytest

from ai.contracts.diagnosis import (
    CellVerdict,
    DiagnosisEvent,
    DiagnosisInput,
    DiagnosisResult,
    DiagnosisStatus,
    NodeVerdict,
    Period,
    PropagatedNode,
    PropagatedVerdict,
    WeaknessCell,
    WeaknessMap,
    WeaknessNode,
)
from ai.contracts.taxonomy import AreaTag, ItemFormat, TypeTag

FIXED_TIME = datetime(2026, 7, 15, 3, 0, tzinfo=UTC)


def _event() -> DiagnosisEvent:
    return DiagnosisEvent(
        event_id="event-1",
        area_tag=AreaTag.LANGUAGE,
        type_tag=TypeTag.CONCEPT,
        item_format=ItemFormat.MCQ,
        skill_node_id="lang.phoneme_change",
        correct=False,
        occurred_at=FIXED_TIME,
        tag_confirmed=True,
    )


def _weakness_map() -> WeaknessMap:
    return WeaknessMap(
        graph_version="0.1.0",
        taxonomy_version="v1",
        config_version="b-defaults-v1",
        snapshot_hash="sha256:abc",
        cells={
            "language×concept": WeaknessCell(
                acc=0.42,
                n=14,
                verdict=CellVerdict.WEAK,
                severity=0.57,
            ),
        },
        nodes={
            "lang.phoneme_change": WeaknessNode(
                verdict=NodeVerdict.SUSPECT,
                basis=("cell:language×concept",),
            ),
        },
        propagated={
            "lang.phoneme_system": PropagatedNode(
                score=0.63,
                verdict=PropagatedVerdict.ROOT_CANDIDATE,
                from_nodes=("lang.phoneme_change",),
            ),
        },
    )


def test_diagnosis_input_roundtrip() -> None:
    payload = DiagnosisInput(
        tenant_id="teacher-alias",
        student_ref="student-alias",
        period=Period(from_date=date(2026, 4, 23), to_date=date(2026, 7, 15)),
        as_of=FIXED_TIME,
        snapshot_hash="sha256:abc",
        events=(_event(),),
    )
    assert DiagnosisInput.model_validate(payload.model_dump(mode="json")) == payload


def test_period_rejects_reverse_range() -> None:
    with pytest.raises(ValueError, match="from_date"):
        Period(from_date=date(2026, 7, 16), to_date=date(2026, 7, 15))


def test_empty_events_are_valid_for_insufficient_path() -> None:
    payload = DiagnosisInput(
        tenant_id="teacher-alias",
        student_ref="student-alias",
        period=Period(from_date=date(2026, 7, 1), to_date=date(2026, 7, 15)),
        as_of=FIXED_TIME,
        snapshot_hash="sha256:empty",
    )
    assert payload.events == ()


def test_event_rejects_unknown_field() -> None:
    data = _event().model_dump(mode="json")
    data["student_name"] = "실명"
    with pytest.raises(ValueError, match="student_name"):
        DiagnosisEvent.model_validate(data)


@pytest.mark.parametrize("chosen_no", [1, 5])
def test_event_accepts_mcq_chosen_no(chosen_no: int) -> None:
    data = _event().model_dump(mode="json")
    data["chosen_no"] = chosen_no

    assert DiagnosisEvent.model_validate(data).chosen_no == chosen_no


def test_chosen_no_is_optional_for_unknown_or_non_mcq_event() -> None:
    assert _event().chosen_no is None


@pytest.mark.parametrize("chosen_no", [0, 6])
def test_event_rejects_chosen_no_outside_mcq_range(chosen_no: int) -> None:
    data = _event().model_dump(mode="json")
    data["chosen_no"] = chosen_no

    with pytest.raises(ValueError, match="chosen_no"):
        DiagnosisEvent.model_validate(data)


# ── passage_ref — 05 [A 확정 통보 2026-08-03] 수신 계약 ──────────────


def test_event_accepts_passage_ref() -> None:
    """감지·진단이 같은 learning_events를 읽으므로 진단도 이 필드를 받아야 한다.

    extra="forbid"라 필드가 없으면 백엔드가 보내는 순간 파싱이 깨진다.
    """
    data = _event().model_dump(mode="json")
    data["passage_ref"] = "ps_4471"
    assert DiagnosisEvent.model_validate(data).passage_ref == "ps_4471"


def test_passage_ref_is_optional() -> None:
    """지문 없는 문항·묶음 개념이 없는 학원은 비운다 — 비워도 안전하다."""
    assert _event().passage_ref is None
    assert DiagnosisEvent(**{**_event().model_dump(), "passage_ref": None}).passage_ref is None


def test_passage_ref_rejects_empty_string() -> None:
    """빈 문자열은 그룹 키가 될 수 없다 — 없으면 null이어야 한다."""
    data = _event().model_dump(mode="json")
    data["passage_ref"] = ""
    with pytest.raises(ValueError, match="passage_ref"):
        DiagnosisEvent.model_validate(data)


def test_cell_verdict_values_frozen() -> None:
    assert {item.value for item in CellVerdict} == {"unknown", "weak", "ok"}


def test_node_verdict_values_frozen() -> None:
    assert {item.value for item in NodeVerdict} == {"suspect", "weak_confirmed", "ok"}


def test_weak_cell_requires_severity() -> None:
    with pytest.raises(ValueError, match="severity"):
        WeaknessCell(acc=0.4, n=10, verdict=CellVerdict.WEAK)


def test_non_weak_cell_rejects_severity() -> None:
    with pytest.raises(ValueError, match="severity"):
        WeaknessCell(acc=0.8, n=10, verdict=CellVerdict.OK, severity=0.1)


def test_cell_rejects_invalid_accuracy() -> None:
    with pytest.raises(ValueError, match="acc"):
        WeaknessCell(acc=1.1, n=10, verdict=CellVerdict.OK)


def test_node_requires_basis() -> None:
    with pytest.raises(ValueError, match="basis"):
        WeaknessNode(verdict=NodeVerdict.SUSPECT, basis=())


def test_node_rejects_empty_basis_reference() -> None:
    with pytest.raises(ValueError, match="basis"):
        WeaknessNode(verdict=NodeVerdict.SUSPECT, basis=("",))


def test_weakness_map_roundtrip_preserves_from_alias() -> None:
    weakness_map = _weakness_map()
    dumped = weakness_map.model_dump(mode="json", by_alias=True)
    assert dumped["propagated"]["lang.phoneme_system"]["from"] == ["lang.phoneme_change"]
    assert WeaknessMap.model_validate(dumped) == weakness_map


def test_weakness_map_rejects_empty_cell_key() -> None:
    data = _weakness_map().model_dump(mode="json", by_alias=True)
    data["cells"][""] = data["cells"].pop("language×concept")
    with pytest.raises(ValueError, match="키"):
        WeaknessMap.model_validate(data)


def test_generated_result_requires_map() -> None:
    with pytest.raises(ValueError, match="weakness_map"):
        DiagnosisResult(status=DiagnosisStatus.GENERATED)


def test_generated_result_roundtrip() -> None:
    result = DiagnosisResult(status=DiagnosisStatus.GENERATED, weakness_map=_weakness_map())
    assert DiagnosisResult.model_validate(result.model_dump(mode="json")) == result


def test_insufficient_result_requires_reason() -> None:
    with pytest.raises(ValueError, match="status_reason"):
        DiagnosisResult(status=DiagnosisStatus.REJECTED_INSUFFICIENT)


def test_insufficient_result_rejects_map() -> None:
    with pytest.raises(ValueError, match="weakness_map"):
        DiagnosisResult(
            status=DiagnosisStatus.REJECTED_INSUFFICIENT,
            weakness_map=_weakness_map(),
            status_reason="all_cells_unknown",
        )


def test_same_input_serializes_identically() -> None:
    first = DiagnosisInput(
        tenant_id="teacher-alias",
        student_ref="student-alias",
        period=Period(from_date=date(2026, 7, 1), to_date=date(2026, 7, 15)),
        as_of=FIXED_TIME,
        snapshot_hash="sha256:abc",
        events=(_event(),),
    )
    second = DiagnosisInput.model_validate(first.model_dump(mode="json"))
    assert first.model_dump_json() == second.model_dump_json()
