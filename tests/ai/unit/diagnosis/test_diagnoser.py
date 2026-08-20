"""결정론 약점 진단기의 판정·병합·전파 회귀 테스트."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from ai.contracts.diagnosis import (
    CellVerdict,
    DiagnosisEvent,
    DiagnosisInput,
    DiagnosisResult,
    DiagnosisStatus,
    NodeVerdict,
    Period,
)
from ai.contracts.taxonomy import AreaTag, TypeTag
from ai.diagnosis.diagnoser import (
    DiagnosisConfig,
    DiagnosisGraphReferenceError,
    DiagnosisInputConflictError,
    DiagnosisVersionMismatchError,
    diagnose,
)
from ai.diagnosis.skill_graph import load_skill_graph

GRAPH_YAML = """
meta:
  graph_version: "0.1.0"
  taxonomy_version: "v1"
  updated: 2026-07-15
nodes:
  - id: lang.root
    label: "언어 루트"
    area_tag: language
    type_affinity: [concept]
    level: 1
    desc: "테스트용 선수 노드"
  - id: lang.middle
    label: "언어 중간"
    area_tag: language
    type_affinity: [concept, fact]
    level: 2
    desc: "테스트용 중간 노드"
  - id: lang.leaf
    label: "언어 말단"
    area_tag: language
    type_affinity: [fact]
    level: 3
    desc: "테스트용 후행 노드"
  - id: lang.related
    label: "언어 관련"
    area_tag: language
    type_affinity: [critic]
    level: 1
    desc: "테스트용 관련 노드"
edges:
  - from: lang.root
    to: lang.middle
    kind: requires
    weight: 1.0
  - from: lang.middle
    to: lang.leaf
    kind: builds_on
    weight: 0.8
  - from: lang.related
    to: lang.leaf
    kind: related
    weight: 1.0
"""

GRAPH = load_skill_graph(GRAPH_YAML, expected_taxonomy_version="v1")
NOW = datetime(2026, 7, 15, tzinfo=UTC)


class _SeedExpected(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: DiagnosisStatus | None
    cells: dict[str, CellVerdict]
    nodes: dict[str, NodeVerdict]
    overall_low: bool | None
    error: str | None


class _SeedCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    config: DiagnosisConfig
    input: DiagnosisInput
    expected: _SeedExpected


class _SeedCorpus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    cases: tuple[_SeedCase, ...]


def _config(**changes: float | int) -> DiagnosisConfig:
    values: dict[str, float | int] = {
        "cell_min_items": 10,
        "relative_cut_pp": -15,
        "severity_saturation": 0.30,
        "decay": 0.7,
        "propagate_threshold": 0.0,
        "suspect_damping": 0.5,
        "node_min_items": 6,
    }
    values.update(changes)
    return DiagnosisConfig.model_validate(values)


def _events(
    prefix: str,
    *,
    area_tag: AreaTag,
    type_tag: TypeTag,
    total: int,
    correct: int,
    skill_node_id: str | None = None,
    confirmed: bool = True,
) -> tuple[DiagnosisEvent, ...]:
    return tuple(
        DiagnosisEvent(
            event_id=f"{prefix}-{index:03d}",
            area_tag=area_tag,
            type_tag=type_tag,
            correct=index < correct,
            occurred_at=NOW,
            tag_confirmed=confirmed,
            skill_node_id=skill_node_id,
        )
        for index in range(total)
    )


def _input(*events: DiagnosisEvent) -> DiagnosisInput:
    return DiagnosisInput(
        tenant_id="tenant-1",
        student_ref="student-alias-1",
        period=Period(from_date=date(2026, 7, 1), to_date=date(2026, 7, 15)),
        as_of=NOW,
        snapshot_hash="sha256:test-snapshot",
        events=events,
    )


def _diagnose(
    *events: DiagnosisEvent,
    config: DiagnosisConfig | None = None,
) -> DiagnosisResult:
    return diagnose(
        _input(*events),
        GRAPH,
        config or _config(),
        graph_version="0.1.0",
        taxonomy_version="v1",
        config_version="b-defaults-v1",
    )


def _boundary_events(
    *,
    target_correct: int = 6,
    target_total: int = 10,
    other_correct: int = 9,
    other_total: int = 10,
    skill_node_id: str | None = None,
) -> tuple[DiagnosisEvent, ...]:
    return (
        *_events(
            "target",
            area_tag=AreaTag.LANGUAGE,
            type_tag=TypeTag.CONCEPT,
            total=target_total,
            correct=target_correct,
            skill_node_id=skill_node_id,
        ),
        *_events(
            "other",
            area_tag=AreaTag.READING,
            type_tag=TypeTag.FACT,
            total=other_total,
            correct=other_correct,
        ),
    )


def test_exactly_minus_15pp_is_weak_for_cell_and_direct_node() -> None:
    result = _diagnose(*_boundary_events(skill_node_id="lang.root"))

    assert result.weakness_map is not None
    assert result.weakness_map.cells["language×concept"].verdict is CellVerdict.WEAK
    assert result.weakness_map.nodes["lang.root"].verdict is NodeVerdict.WEAK_CONFIRMED


def test_minus_14pp_is_ok() -> None:
    events = _boundary_events(
        target_correct=61,
        target_total=100,
        other_correct=89,
        other_total=100,
    )
    result = _diagnose(*events)

    assert result.weakness_map is not None
    assert result.weakness_map.cells["language×concept"].verdict is CellVerdict.OK


def test_accuracy_above_overall_is_ok_for_cell_and_direct_node() -> None:
    events = (
        *_events(
            "high",
            area_tag=AreaTag.LANGUAGE,
            type_tag=TypeTag.CONCEPT,
            total=10,
            correct=8,
            skill_node_id="lang.root",
        ),
        *_events(
            "low",
            area_tag=AreaTag.READING,
            type_tag=TypeTag.FACT,
            total=10,
            correct=7,
        ),
    )
    result = _diagnose(*events)

    assert result.weakness_map is not None
    assert result.weakness_map.cells["language×concept"].verdict is CellVerdict.OK
    assert result.weakness_map.nodes["lang.root"].verdict is NodeVerdict.OK


def test_relative_cut_is_injected_and_changes_the_verdict() -> None:
    events = _boundary_events()

    default_result = _diagnose(*events)
    strict_result = _diagnose(*events, config=_config(relative_cut_pp=-20))

    assert default_result.weakness_map is not None
    assert strict_result.weakness_map is not None
    assert default_result.weakness_map.cells["language×concept"].verdict is CellVerdict.WEAK
    assert strict_result.weakness_map.cells["language×concept"].verdict is CellVerdict.OK


def test_direct_severity_uses_ratio_gap_not_percentage_points() -> None:
    events = (
        *_events(
            "node",
            area_tag=AreaTag.LANGUAGE,
            type_tag=TypeTag.FACT,
            total=10,
            correct=5,
            skill_node_id="lang.leaf",
        ),
        *_events(
            "overall",
            area_tag=AreaTag.READING,
            type_tag=TypeTag.FACT,
            total=30,
            correct=30,
        ),
    )
    result = _diagnose(*events, config=_config(decay=1.0))

    assert result.weakness_map is not None
    assert result.weakness_map.nodes["lang.leaf"].verdict is NodeVerdict.WEAK_CONFIRMED
    assert result.weakness_map.propagated["lang.middle"].score == pytest.approx(0.8)


def test_passage_ref_does_not_change_v1_verdicts() -> None:
    """v1 판정 축은 원시 정답률뿐 — `passage_ref`는 수신만 하고 판정에 쓰지 않는다.

    기대치 잔차로의 이관은 `09` §3 W13이다. 잔차를 도입하면 이 테스트가 먼저 깨지므로
    이관 시점이 조용히 지나가지 않는다.
    """
    events = _events(
        "read",
        area_tag=AreaTag.READING,
        type_tag=TypeTag.INFER,
        total=12,
        correct=4,
    )
    baseline = _diagnose(*events)
    tagged = _diagnose(
        *(
            event.model_copy(update={"passage_ref": f"ps_{index:03d}"})
            for index, event in enumerate(events)
        )
    )

    assert baseline.weakness_map is not None
    assert tagged.weakness_map == baseline.weakness_map


def test_chosen_no_does_not_change_v1_verdicts() -> None:
    """chosen_no는 P4 수신 필드일 뿐 v1 셀·노드 판정 축이 아니다."""

    events = _events(
        "chosen",
        area_tag=AreaTag.LANGUAGE,
        type_tag=TypeTag.CONCEPT,
        total=12,
        correct=4,
    )
    baseline = _diagnose(*events)
    tagged = _diagnose(
        *(
            event.model_copy(update={"chosen_no": 2})
            if not event.correct
            else event
            for event in events
        )
    )

    assert baseline.weakness_map is not None
    assert tagged.weakness_map == baseline.weakness_map


def test_non_weak_cell_has_no_severity() -> None:
    result = _diagnose(*_boundary_events())

    assert result.weakness_map is not None
    assert result.weakness_map.cells["reading×fact"].verdict is CellVerdict.OK
    assert result.weakness_map.cells["reading×fact"].severity is None


def test_below_cell_minimum_is_unknown() -> None:
    events = (
        *_events(
            "small",
            area_tag=AreaTag.LANGUAGE,
            type_tag=TypeTag.CONCEPT,
            total=9,
            correct=0,
        ),
        *_events(
            "known",
            area_tag=AreaTag.READING,
            type_tag=TypeTag.FACT,
            total=10,
            correct=10,
        ),
    )
    result = _diagnose(*events)

    assert result.weakness_map is not None
    assert result.weakness_map.cells["language×concept"].verdict is CellVerdict.UNKNOWN


def test_no_judgeable_cell_is_rejected_without_exception() -> None:
    events = _events(
        "small",
        area_tag=AreaTag.LANGUAGE,
        type_tag=TypeTag.CONCEPT,
        total=9,
        correct=4,
    )
    result = _diagnose(*events)

    assert result.status is DiagnosisStatus.REJECTED_INSUFFICIENT
    assert result.weakness_map is None
    assert result.status_reason == "판정 가능한 셀이 없다"


def test_empty_events_are_rejected_without_exception() -> None:
    result = _diagnose()

    assert result.status is DiagnosisStatus.REJECTED_INSUFFICIENT
    assert result.weakness_map is None


def test_overall_low_ignores_unknown_cells() -> None:
    events = (
        *_events(
            "weak",
            area_tag=AreaTag.LANGUAGE,
            type_tag=TypeTag.CONCEPT,
            total=10,
            correct=1,
        ),
        *_events(
            "unknown",
            area_tag=AreaTag.READING,
            type_tag=TypeTag.FACT,
            total=9,
            correct=9,
        ),
    )
    result = _diagnose(*events)

    assert result.weakness_map is not None
    assert result.weakness_map.cells["language×concept"].verdict is CellVerdict.WEAK
    assert result.weakness_map.cells["reading×fact"].verdict is CellVerdict.UNKNOWN
    assert result.weakness_map.overall_low is True


def test_any_ok_cell_makes_overall_low_false() -> None:
    result = _diagnose(*_boundary_events())

    assert result.weakness_map is not None
    assert result.weakness_map.overall_low is False


def test_suspect_uses_maximum_connected_cell_severity() -> None:
    events = (
        *_events(
            "concept",
            area_tag=AreaTag.LANGUAGE,
            type_tag=TypeTag.CONCEPT,
            total=10,
            correct=3,
        ),
        *_events(
            "fact",
            area_tag=AreaTag.LANGUAGE,
            type_tag=TypeTag.FACT,
            total=10,
            correct=5,
        ),
        *_events(
            "high",
            area_tag=AreaTag.READING,
            type_tag=TypeTag.FACT,
            total=20,
            correct=20,
        ),
    )
    result = _diagnose(*events)

    assert result.weakness_map is not None
    assert result.weakness_map.nodes["lang.middle"].verdict is NodeVerdict.SUSPECT
    assert result.weakness_map.propagated["lang.root"].score == pytest.approx(
        0.35 + (2 / 3) * 0.5 * 0.8 * 0.7**2
    )


def test_suspect_without_direct_data_stays_suspect() -> None:
    result = _diagnose(*_boundary_events())

    assert result.weakness_map is not None
    assert result.weakness_map.nodes["lang.root"].verdict is NodeVerdict.SUSPECT


def test_suspect_with_direct_weak_becomes_confirmed() -> None:
    result = _diagnose(*_boundary_events(skill_node_id="lang.root"))

    assert result.weakness_map is not None
    assert result.weakness_map.nodes["lang.root"].verdict is NodeVerdict.WEAK_CONFIRMED


def test_suspect_with_direct_ok_becomes_ok() -> None:
    target = (
        *_events(
            "direct",
            area_tag=AreaTag.LANGUAGE,
            type_tag=TypeTag.CONCEPT,
            total=6,
            correct=6,
            skill_node_id="lang.root",
        ),
        *_events(
            "cell-only",
            area_tag=AreaTag.LANGUAGE,
            type_tag=TypeTag.CONCEPT,
            total=4,
            correct=0,
        ),
        *_events(
            "other",
            area_tag=AreaTag.READING,
            type_tag=TypeTag.FACT,
            total=10,
            correct=9,
        ),
    )
    result = _diagnose(*target)

    assert result.weakness_map is not None
    assert result.weakness_map.cells["language×concept"].verdict is CellVerdict.WEAK
    assert result.weakness_map.nodes["lang.root"].verdict is NodeVerdict.OK


def test_direct_weak_without_indirect_weak_is_confirmed() -> None:
    events = (
        *_events(
            "direct",
            area_tag=AreaTag.LANGUAGE,
            type_tag=TypeTag.FACT,
            total=6,
            correct=0,
            skill_node_id="lang.leaf",
        ),
        *_events(
            "other",
            area_tag=AreaTag.READING,
            type_tag=TypeTag.FACT,
            total=10,
            correct=10,
        ),
    )
    result = _diagnose(*events)

    assert result.weakness_map is not None
    assert result.weakness_map.cells["language×fact"].verdict is CellVerdict.UNKNOWN
    assert result.weakness_map.nodes["lang.leaf"].verdict is NodeVerdict.WEAK_CONFIRMED


def test_direct_sample_below_minimum_preserves_suspect() -> None:
    target = list(_boundary_events())
    target[:5] = [event.model_copy(update={"skill_node_id": "lang.root"}) for event in target[:5]]
    result = _diagnose(*target)

    assert result.weakness_map is not None
    assert result.weakness_map.nodes["lang.root"].verdict is NodeVerdict.SUSPECT


def test_reverse_propagation_applies_distance_weight_and_suspect_damping() -> None:
    events = (
        *_events(
            "weak-leaf",
            area_tag=AreaTag.LANGUAGE,
            type_tag=TypeTag.FACT,
            total=10,
            correct=0,
        ),
        *_events(
            "high",
            area_tag=AreaTag.READING,
            type_tag=TypeTag.FACT,
            total=10,
            correct=10,
        ),
    )
    result = _diagnose(*events)

    assert result.weakness_map is not None
    assert result.weakness_map.propagated["lang.middle"].score == pytest.approx(0.28)
    assert result.weakness_map.propagated["lang.root"].score == pytest.approx(0.35 + 0.196)
    assert result.weakness_map.propagated["lang.root"].from_nodes == (
        "lang.leaf",
        "lang.middle",
    )
    assert "lang.related" not in result.weakness_map.propagated


def test_direct_ok_blocks_incoming_propagation_and_further_traversal() -> None:
    events = (
        *_events(
            "weak-leaf",
            area_tag=AreaTag.LANGUAGE,
            type_tag=TypeTag.FACT,
            total=10,
            correct=0,
        ),
        *_events(
            "ok-middle",
            area_tag=AreaTag.LANGUAGE,
            type_tag=TypeTag.CONCEPT,
            total=6,
            correct=6,
            skill_node_id="lang.middle",
        ),
        *_events(
            "high",
            area_tag=AreaTag.READING,
            type_tag=TypeTag.FACT,
            total=10,
            correct=10,
        ),
    )
    result = _diagnose(*events)

    assert result.weakness_map is not None
    assert result.weakness_map.nodes["lang.middle"].verdict is NodeVerdict.OK
    assert "lang.middle" not in result.weakness_map.propagated
    assert "lang.root" not in result.weakness_map.propagated


def test_root_weakness_does_not_propagate_forward() -> None:
    result = _diagnose(*_boundary_events())

    assert result.weakness_map is not None
    assert result.weakness_map.nodes["lang.root"].verdict is NodeVerdict.SUSPECT
    assert "lang.middle" not in result.weakness_map.propagated
    assert "lang.leaf" not in result.weakness_map.propagated


def test_identical_event_id_is_deduplicated() -> None:
    events = _boundary_events()
    result = _diagnose(*events, events[0])

    assert result.weakness_map is not None
    assert result.weakness_map.cells["language×concept"].n == 10


def test_conflicting_event_id_aborts_without_partial_result() -> None:
    event = _boundary_events()[0]
    conflicting = event.model_copy(update={"correct": not event.correct})

    with pytest.raises(DiagnosisInputConflictError, match=event.event_id):
        _diagnose(event, conflicting)


def test_unconfirmed_events_are_excluded() -> None:
    events = (
        *_boundary_events(),
        *_events(
            "unconfirmed",
            area_tag=AreaTag.LANGUAGE,
            type_tag=TypeTag.CONCEPT,
            total=20,
            correct=20,
            confirmed=False,
        ),
    )
    result = _diagnose(*events)

    assert result.weakness_map is not None
    assert result.weakness_map.cells["language×concept"].n == 10
    assert result.weakness_map.cells["language×concept"].verdict is CellVerdict.WEAK


def test_same_input_and_shuffled_events_serialize_identically() -> None:
    events = _boundary_events(skill_node_id="lang.root")
    first = _diagnose(*events)
    second = _diagnose(*reversed(events))

    assert first.model_dump_json(by_alias=True) == second.model_dump_json(by_alias=True)


def test_unknown_skill_node_fails_closed() -> None:
    events = _boundary_events(skill_node_id="lang.missing")

    with pytest.raises(DiagnosisGraphReferenceError, match="lang.missing"):
        _diagnose(*events)


def test_execution_version_mismatch_is_rejected() -> None:
    with pytest.raises(DiagnosisVersionMismatchError, match="graph_version 불일치"):
        diagnose(
            _input(*_boundary_events()),
            GRAPH,
            _config(),
            graph_version="0.2.0",
            taxonomy_version="v1",
            config_version="b-defaults-v1",
        )


def test_json_golden_seed_cases() -> None:
    seed_path = Path(__file__).with_name("diagnosis_seed_cases.json")
    corpus = _SeedCorpus.model_validate_json(seed_path.read_text(encoding="utf-8"))

    assert len(corpus.cases) == 5
    for case in corpus.cases:
        if case.expected.error == "DiagnosisInputConflictError":
            with pytest.raises(DiagnosisInputConflictError):
                diagnose(
                    case.input,
                    GRAPH,
                    case.config,
                    graph_version="0.1.0",
                    taxonomy_version="v1",
                    config_version="b-defaults-v1",
                )
            continue

        result = diagnose(
            case.input,
            GRAPH,
            case.config,
            graph_version="0.1.0",
            taxonomy_version="v1",
            config_version="b-defaults-v1",
        )
        assert result.status is case.expected.status, case.case_id
        if result.weakness_map is None:
            assert case.expected.cells == {}
            assert case.expected.nodes == {}
            assert case.expected.overall_low is None
            continue
        assert {
            key: cell.verdict for key, cell in result.weakness_map.cells.items()
        } == case.expected.cells, case.case_id
        assert {
            key: node.verdict for key, node in result.weakness_map.nodes.items()
        } == case.expected.nodes, case.case_id
        assert result.weakness_map.overall_low is case.expected.overall_low, case.case_id
