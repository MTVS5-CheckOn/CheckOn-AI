"""FakeSnapshot을 문제출제 진단 호출로 연결하는 테스트 전용 어댑터."""

from __future__ import annotations

from ai.contracts.detection import DetectRequest
from ai.contracts.diagnosis import (
    CellVerdict,
    DiagnosisResult,
    DiagnosisStatus,
    NodeVerdict,
    WeaknessCell,
    WeaknessMap,
    WeaknessNode,
)
from ai.contracts.problem_generation import ProblemRequest


class SnapshotDiagnosisFake:
    """docs/05 요청 모양의 FakeSnapshot에서 고정 약점 노드를 반환한다."""

    def __init__(
        self,
        *,
        snapshot: DetectRequest,
        skill_node_id: str,
        graph_version: str,
        taxonomy_version: str,
        config_version: str,
    ) -> None:
        self._snapshot = snapshot
        self._skill_node_id = skill_node_id
        self._graph_version = graph_version
        self._taxonomy_version = taxonomy_version
        self._config_version = config_version
        self.requests: list[ProblemRequest] = []

    async def __call__(self, request: ProblemRequest) -> DiagnosisResult:
        self.requests.append(request)
        if request.snapshot_hash != self._snapshot.snapshot_meta.snapshot_hash:
            return DiagnosisResult(
                status=DiagnosisStatus.REJECTED_INSUFFICIENT,
                status_reason="FakeSnapshot과 문제 요청의 snapshot_hash가 다름",
            )
        return DiagnosisResult(
            status=DiagnosisStatus.GENERATED,
            weakness_map=WeaknessMap(
                graph_version=self._graph_version,
                taxonomy_version=self._taxonomy_version,
                config_version=self._config_version,
                snapshot_hash=request.snapshot_hash,
                cells={
                    "reading×infer": WeaknessCell(
                        acc=0.4,
                        n=10,
                        verdict=CellVerdict.WEAK,
                        severity=0.8,
                    )
                },
                nodes={
                    self._skill_node_id: WeaknessNode(
                        verdict=NodeVerdict.WEAK_CONFIRMED,
                        basis=("cell:reading×infer",),
                    )
                },
            ),
        )


__all__ = ["SnapshotDiagnosisFake"]
