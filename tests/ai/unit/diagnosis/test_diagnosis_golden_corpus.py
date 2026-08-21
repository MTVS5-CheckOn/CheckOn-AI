"""B 진단 골든 코퍼스의 판정·병합·전파·결정론 회귀."""

from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict
from test_diagnoser import GRAPH

from ai.contracts.diagnosis import (
    CellVerdict,
    DiagnosisEvent,
    DiagnosisInput,
    DiagnosisResult,
    DiagnosisStatus,
    NodeVerdict,
    PropagatedNode,
)
from ai.diagnosis.diagnoser import DiagnosisConfig, diagnose
from ai.problem_generation.infrastructure.config import load_misconception_tags

GOLDEN_PATH = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "ai"
    / "evaluation"
    / "golden"
    / "diagnosis"
    / "diagnosis_cases.json"
)
_MISCONCEPTION_VOCABULARY = {
    area: frozenset(tag.id for tag in tags)
    for area, tags in load_misconception_tags().areas.items()
}


class _GoldenExpected(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: DiagnosisStatus
    cells: dict[str, CellVerdict]
    nodes: dict[str, NodeVerdict]
    propagated: dict[str, PropagatedNode]
    overall_low: bool
    error: str | None


class _GoldenCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    config: DiagnosisConfig
    input: DiagnosisInput
    expected: _GoldenExpected


class _GoldenCorpus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    cases: tuple[_GoldenCase, ...]


def _run(case: _GoldenCase, events: tuple[DiagnosisEvent, ...]) -> DiagnosisResult:
    return diagnose(
        case.input.model_copy(update={"events": events}),
        GRAPH,
        case.config,
        graph_version="0.1.0",
        taxonomy_version="v1",
        config_version="b-defaults-v1",
        misconception_vocabulary=_MISCONCEPTION_VOCABULARY,
    )


def test_diagnosis_golden_corpus() -> None:
    corpus = _GoldenCorpus.model_validate_json(GOLDEN_PATH.read_text(encoding="utf-8"))

    assert len(corpus.cases) == 6
    assert len({case.case_id for case in corpus.cases}) == len(corpus.cases)
    for case in corpus.cases:
        assert case.expected.error is None
        result = _run(case, case.input.events)
        repeated = _run(case, case.input.events)
        shuffled = _run(case, tuple(reversed(case.input.events)))

        serialized = result.model_dump_json(by_alias=True)
        assert serialized == repeated.model_dump_json(by_alias=True), case.case_id
        assert serialized == shuffled.model_dump_json(by_alias=True), case.case_id
        assert result.status is case.expected.status, case.case_id
        assert result.weakness_map is not None
        weakness_map = result.weakness_map
        assert {
            key: cell.verdict for key, cell in weakness_map.cells.items()
        } == case.expected.cells, case.case_id
        assert {
            key: node.verdict for key, node in weakness_map.nodes.items()
        } == case.expected.nodes, case.case_id
        assert weakness_map.overall_low is case.expected.overall_low, case.case_id

        expected_propagated = case.expected.propagated
        assert set(weakness_map.propagated) == set(expected_propagated), case.case_id
        for node_id, expected in expected_propagated.items():
            actual = weakness_map.propagated[node_id]
            assert actual.verdict is expected.verdict, case.case_id
            assert actual.from_nodes == expected.from_nodes, case.case_id
            assert actual.score == pytest.approx(expected.score), case.case_id

        confirmed_event_ids = {
            event.event_id for event in case.input.events if event.tag_confirmed
        }
        assert sum(cell.n for cell in weakness_map.cells.values()) == len(
            confirmed_event_ids
        ), case.case_id
