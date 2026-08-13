"""진단 설정 파일의 경계 계약.

🔴 **`cell_min_items`가 두 곳에 산다** — 감지 R6(A 소유 threshold 시트)과 진단 셀 판정이
**같은 값을 써야 한다**(part_b/04 §4 "B 임의 값 별도 운영 금지"). capability 경계 때문에
`diagnosis/`가 `detection/`을 import할 수 없어 값을 복제했고, 그 복제가 갈리는 것을 여기서
잡는다 — 문서 규칙으로만 두면 조용히 갈린다.
"""

from __future__ import annotations

from ai.detection.thresholds import default_threshold_config
from ai.diagnosis.config import (
    default_diagnosis_runtime,
    load_curriculum_graph,
    load_diagnosis_config,
)


def test_cell_min_items_matches_the_detection_threshold_sheet() -> None:
    """감지와 진단이 같은 셀을 다르게 판정하면 강사 화면 둘이 서로를 반박한다."""
    assert (
        load_diagnosis_config().params.cell_min_items
        == default_threshold_config().r6.cell_min_items
    ), (
        "진단 설정의 cell_min_items가 A threshold 시트 R6와 갈렸다 — 정본은 R6다"
        "(part_a/04_threshold_config.md · part_b/04_curriculum_graph.md §4)"
    )


def test_defaults_match_the_b_default_sheet() -> None:
    """`part_b/06_quality_gates.md` 부록 'B 기본값 시트'와 1:1."""
    params = load_diagnosis_config().params

    assert params.relative_cut_pp == -15.0
    assert params.decay == 0.7
    assert params.propagate_threshold == 0.5
    assert params.severity_saturation == 0.30
    assert params.suspect_damping == 0.5
    assert params.node_min_items == 6


def test_config_version_is_the_sheet_version_the_weakness_map_will_carry() -> None:
    assert load_diagnosis_config().version == "b-defaults-v1"


def test_packaged_graph_must_match_the_expected_taxonomy_version() -> None:
    """어휘가 갈린 그래프로 판정하면 셀 좌표가 조용히 어긋난다 — 로딩에서 죽어야 한다."""
    document = load_diagnosis_config()
    graph = load_curriculum_graph()

    assert graph.meta.taxonomy_version == document.expected_taxonomy_version


def test_runtime_is_loaded_once_and_shared() -> None:
    """요청마다 YAML을 다시 읽지 않는다 — 같은 객체여야 한다."""
    assert default_diagnosis_runtime() is default_diagnosis_runtime()
