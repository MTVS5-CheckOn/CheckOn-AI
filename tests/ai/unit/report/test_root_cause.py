"""WeaknessMap 근본 원인 지표의 순수 변환 불변식."""

from ai.contracts.diagnosis import (
    CellVerdict,
    NodeVerdict,
    PropagatedNode,
    WeaknessCell,
    WeaknessMap,
    WeaknessNode,
)
from ai.contracts.report import ReportMetricInput
from ai.diagnosis.config import default_diagnosis_runtime
from ai.report.root_cause import build_root_cause_metrics
from ai.report.vocabulary import RootCauseMetricKind, default_report_root_cause_vocabulary

CONFIRMED_ID = "language.grammar.fortition"
SUSPECT_ID = "language.grammar.phoneme.system"
OK_ID = "language.grammar.syllable.structure"
PROPAGATED_ID = "language.grammar.phonological_change"


def _map(
    *,
    nodes: dict[str, WeaknessNode] | None = None,
    propagated: dict[str, PropagatedNode] | None = None,
    overall_low: bool = False,
) -> WeaknessMap:
    return WeaknessMap(
        graph_version="curriculum-five-area-v1",
        taxonomy_version="v1",
        config_version="diagnosis-v1",
        snapshot_hash="sha256:root-cause",
        cells={"language×concept": WeaknessCell(acc=0.5, n=10, verdict=CellVerdict.OK)},
        nodes=nodes or {},
        propagated=propagated or {},
        overall_low=overall_low,
    )


def _build(weakness_map: WeaknessMap) -> tuple[ReportMetricInput, ...]:
    graph, _ = default_diagnosis_runtime()
    return build_root_cause_metrics(
        weakness_map,
        graph=graph,
        vocabulary=default_report_root_cause_vocabulary(),
    )


def test_builds_direct_and_propagated_root_cause_metrics() -> None:
    metrics = _build(
        _map(
            nodes={
                CONFIRMED_ID: WeaknessNode(
                    verdict=NodeVerdict.WEAK_CONFIRMED,
                    basis=("event:1", "cell:language×concept"),
                ),
                SUSPECT_ID: WeaknessNode(verdict=NodeVerdict.SUSPECT, basis=("event:2",)),
            },
            propagated={
                PROPAGATED_ID: PropagatedNode(score=0.625, from_nodes=(CONFIRMED_ID,))
            },
        )
    )
    vocabulary = default_report_root_cause_vocabulary()

    assert [metric.metric_key for metric in metrics] == [
        vocabulary.metric_key_for(RootCauseMetricKind.CONFIRMED),
        vocabulary.metric_key_for(RootCauseMetricKind.SUSPECT),
        vocabulary.metric_key_for(RootCauseMetricKind.PROPAGATED),
    ]
    assert [metric.value for metric in metrics] == [2.0, 1.0, 0.625]
    assert [len(metric.evidence) for metric in metrics] == [2, 1, 1]


def test_boundary_keeps_single_basis_as_one_evidence() -> None:
    metrics = _build(
        _map(
            nodes={
                CONFIRMED_ID: WeaknessNode(
                    verdict=NodeVerdict.WEAK_CONFIRMED,
                    basis=("event:only",),
                )
            }
        )
    )

    assert len(metrics) == 1
    assert metrics[0].value == 1.0
    assert metrics[0].evidence[0].record_id == "event:only"


def test_boundary_with_no_propagated_nodes_keeps_direct_nodes_only() -> None:
    metrics = _build(
        _map(nodes={SUSPECT_ID: WeaknessNode(verdict=NodeVerdict.SUSPECT, basis=("cell:1",))})
    )

    assert len(metrics) == 1
    assert metrics[0].metric_key == default_report_root_cause_vocabulary().metric_key_for(
        RootCauseMetricKind.SUSPECT
    )


def test_boundary_with_empty_nodes_returns_no_metrics() -> None:
    assert _build(_map()) == ()


def test_omits_item_when_evidence_is_empty() -> None:
    invalid_node = WeaknessNode.model_construct(
        verdict=NodeVerdict.WEAK_CONFIRMED,
        basis=(),
    )
    invalid_propagated = PropagatedNode.model_construct(
        score=0.5,
        from_nodes=(),
    )

    assert (
        _build(
            _map(
                nodes={CONFIRMED_ID: invalid_node},
                propagated={PROPAGATED_ID: invalid_propagated},
            )
        )
        == ()
    )


def test_selection_matches_problem_workflow_priority_and_dedup() -> None:
    metrics = _build(
        _map(
            nodes={
                SUSPECT_ID: WeaknessNode(verdict=NodeVerdict.SUSPECT, basis=("event:s",)),
                CONFIRMED_ID: WeaknessNode(
                    verdict=NodeVerdict.WEAK_CONFIRMED,
                    basis=("event:c",),
                ),
                OK_ID: WeaknessNode(verdict=NodeVerdict.OK, basis=("event:o",)),
            },
            propagated={
                CONFIRMED_ID: PropagatedNode(score=0.9, from_nodes=(SUSPECT_ID,)),
                PROPAGATED_ID: PropagatedNode(score=0.4, from_nodes=(CONFIRMED_ID,)),
            },
            overall_low=True,
        )
    )
    vocabulary = default_report_root_cause_vocabulary()

    assert [metric.metric_key for metric in metrics] == [
        vocabulary.metric_key_for(RootCauseMetricKind.CONFIRMED),
        vocabulary.metric_key_for(RootCauseMetricKind.SUSPECT),
        vocabulary.metric_key_for(RootCauseMetricKind.PROPAGATED),
    ]
    assert len(metrics) == 3
