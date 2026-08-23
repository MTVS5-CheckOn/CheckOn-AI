"""WeaknessMap 근본 원인을 학부모용 리포트 지표로 바꾸는 결정론 변환."""

from __future__ import annotations

from hashlib import sha256

from ai.contracts.diagnosis import NodeVerdict, WeaknessMap
from ai.contracts.report import ReportAudience, ReportEvidenceRef, ReportMetricInput
from ai.diagnosis.config import default_diagnosis_runtime
from ai.diagnosis.skill_graph import GraphNode, SkillGraph
from ai.report.vocabulary import (
    ReportRootCauseVocabulary,
    RootCauseMetricKind,
    default_report_root_cause_vocabulary,
)


def build_root_cause_metrics(
    weakness_map: WeaknessMap,
    *,
    graph: SkillGraph,
    vocabulary: ReportRootCauseVocabulary,
) -> tuple[ReportMetricInput, ...]:
    """출제 workflow와 같은 확정→의심→전파 순서로 근본 원인 지표를 만든다."""

    node_index = graph.node_index()
    aliases = {node_id: _node_alias(node_id) for node_id in node_index}
    metrics: list[ReportMetricInput] = []
    confirmed_ids = tuple(
        node_id
        for node_id, node in sorted(weakness_map.nodes.items())
        if node.verdict is NodeVerdict.WEAK_CONFIRMED
    )
    suspect_ids = tuple(
        node_id
        for node_id, node in sorted(weakness_map.nodes.items())
        if node.verdict is NodeVerdict.SUSPECT
    )

    for kind, node_ids in (
        (RootCauseMetricKind.CONFIRMED, confirmed_ids),
        (RootCauseMetricKind.SUSPECT, suspect_ids),
    ):
        for node_id in node_ids:
            node = weakness_map.nodes[node_id]
            graph_node = node_index.get(node_id)
            if graph_node is None or not node.basis:
                continue
            display = _display_name(graph_node, vocabulary)
            evidence = tuple(
                ReportEvidenceRef(
                    source_table="weakness_node_basis",
                    record_id=_safe_reference(reference, aliases),
                    summary=f"{display}의 {_kind_label(kind)} 근거 {position}",
                )
                for position, reference in enumerate(node.basis, start=1)
                if reference
            )
            if not evidence:
                continue
            metrics.append(
                ReportMetricInput(
                    metric_key=vocabulary.metric_key_for(kind),
                    value=float(len(evidence)),
                    audience=ReportAudience.GUARDIAN,
                    evidence=evidence,
                )
            )

    known = {*confirmed_ids, *suspect_ids}
    for node_id, propagated in sorted(weakness_map.propagated.items()):
        if node_id in known or not propagated.from_nodes:
            continue
        graph_node = node_index.get(node_id)
        source_nodes = tuple(node_index.get(source_id) for source_id in propagated.from_nodes)
        if graph_node is None or any(source_node is None for source_node in source_nodes):
            continue
        target_display = _display_name(graph_node, vocabulary)
        evidence = tuple(
            ReportEvidenceRef(
                source_table="weakness_propagation",
                record_id=aliases[source_id],
                summary=(
                    f"{target_display} 근본 후보의 출발: "
                    f"{_display_name(source_node, vocabulary)}"
                ),
            )
            for source_id, source_node in zip(propagated.from_nodes, source_nodes, strict=True)
            if source_node is not None
        )
        if not evidence:
            continue
        metrics.append(
            ReportMetricInput(
                metric_key=vocabulary.metric_key_for(RootCauseMetricKind.PROPAGATED),
                value=propagated.score,
                audience=ReportAudience.GUARDIAN,
                evidence=evidence,
            )
        )

    # overall_low에는 독립 evidence가 없으므로 근본 원인처럼 꾸며 내지 않는다.
    return tuple(metrics)


def build_default_root_cause_metrics(weakness_map: WeaknessMap) -> tuple[ReportMetricInput, ...]:
    """검증·캐시된 기본 그래프와 어휘를 순수 변환에 주입한다."""

    graph, _ = default_diagnosis_runtime()
    return build_root_cause_metrics(
        weakness_map,
        graph=graph,
        vocabulary=default_report_root_cause_vocabulary(),
    )


def _display_name(node: GraphNode, vocabulary: ReportRootCauseVocabulary) -> str:
    types = "·".join(vocabulary.type_label_for(type_tag) for type_tag in node.type_affinity)
    return f"{vocabulary.area_label_for(node.area_tag)} × {types}"


def _safe_reference(reference: str, aliases: dict[str, str]) -> str:
    safe = reference
    for node_id, alias in aliases.items():
        safe = safe.replace(node_id, alias)
    return safe


def _node_alias(node_id: str) -> str:
    return f"graph-node-{sha256(node_id.encode('utf-8')).hexdigest()[:16]}"


def _kind_label(kind: RootCauseMetricKind) -> str:
    return {
        RootCauseMetricKind.CONFIRMED: "확정",
        RootCauseMetricKind.SUSPECT: "의심",
        RootCauseMetricKind.PROPAGATED: "전파",
    }[kind]


__all__ = ["build_default_root_cause_metrics", "build_root_cause_metrics"]
