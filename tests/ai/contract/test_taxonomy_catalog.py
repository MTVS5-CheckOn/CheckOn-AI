"""스킬 노드 카탈로그 — BE·adapter가 `area×type` 셀을 목표 노드로 바꿀 때 보는 표.

**왜 필요한가(2026-08-13 BE 명세 대조 §1-2):** `POST /v1/problems`의
`target_source="teacher_manual"`은 `manual_targets`가 **필수**이고 그 원소는
`curriculum_graph.yaml`의 `skill_node_id`다. 그런데 스튜디오 화면은 `areaTag × typeTag`
셀만 받으므로 **BE에는 그 식별자를 만들 재료가 없다.** 필드를 채우려 해도 채울 값이
없어서 전 요청이 400으로 떨어진다.

🔴 **그래서 AI가 카탈로그를 내고, 셀→노드 매핑의 소유는 adapter로 둔다.** 우리가 매핑까지
가지면 BE의 화면 축(영역×유형)이 AI 계약으로 새어 들어온다.

⚠ **이 파일은 목록만 만든다.** "어느 노드가 지금 실제로 문항을 낼 수 있는가"는 **적지
않는다** — 근거 자료 배선에 따라 바뀌는 값이라 카탈로그에 박으면 곧 거짓이 된다.

**재생성:** 그래프가 개정되면 `WRITE_TAXONOMY_CATALOG=1 uv run pytest
tests/ai/contract/test_taxonomy_catalog.py`로 다시 쓰고 **diff를 리뷰**한다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Final

from ai.contracts.taxonomy import V1_TYPE_TAGS, AreaTag, TypeTag
from ai.diagnosis.config import default_diagnosis_runtime

FIXTURE_PATH: Final = (
    Path(__file__).parent / "fixtures" / "taxonomy" / "curriculum_nodes.json"
)
_WRITE: Final = os.environ.get("WRITE_TAXONOMY_CATALOG") == "1"


def build_catalog() -> dict[str, Any]:
    """패키지 동봉 그래프에서 카탈로그를 만든다 — 순수 변환, 손으로 적지 않는다."""

    graph, document = default_diagnosis_runtime()
    return {
        "graph_version": graph.meta.graph_version,
        "taxonomy_version": graph.meta.taxonomy_version,
        "config_version": document.version,
        "node_count": len(graph.nodes),
        "areas": [area.value for area in AreaTag],
        # ⚠ 예약 태그(`apply`)는 뺀다 — 그 축으로 요청하면 400이다.
        "types": [tag.value for tag in TypeTag if tag in V1_TYPE_TAGS],
        "nodes": [
            {
                "id": node.id,
                "label": node.label,
                "area_tag": node.area_tag.value,
                # 🔴 **이 필드가 셀→노드 매핑의 키다.** 어느 유형으로 물을 수 있는 노드인지.
                "type_affinity": [tag.value for tag in node.type_affinity],
                "level": node.level,
            }
            for node in graph.nodes
        ],
    }


def _catalog_fixture(payload: dict[str, Any]) -> None:
    if _WRITE:
        FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return
    assert FIXTURE_PATH.exists(), (
        f"카탈로그 픽스처가 없다: {FIXTURE_PATH}\n"
        "WRITE_TAXONOMY_CATALOG=1로 한 번 돌려 만들고 diff를 리뷰하라."
    )
    stored: object = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert stored == payload, (
        f"카탈로그가 그래프와 갈렸다: {FIXTURE_PATH}\n"
        "🔴 BE·adapter가 이 표로 셀→노드 매핑을 만들었다 — 노드를 지우거나 id를 바꿨다면 "
        "재생성 전에 통보가 선행이다(사라진 id로 오는 요청은 근거 부족으로 실패한다)."
    )


def test_catalog_matches_the_packaged_graph() -> None:
    """카탈로그는 그래프에서 유도한다 — 손으로 적은 목록을 주지 않는다."""
    _catalog_fixture(build_catalog())


def test_every_node_declares_at_least_one_v1_type() -> None:
    """🔴 v1 유형 축이 하나도 없는 노드는 **셀에서 도달할 수 없다.**

    adapter는 `area×type` 셀로만 노드를 고르므로, 예약 태그(`apply`)만 가진 노드가 있으면
    그 노드는 카탈로그에 있는데 **아무 셀로도 선택되지 않는다** — 목록에 있으니 BE는
    쓸 수 있다고 읽는다.
    """
    catalog = build_catalog()
    unreachable = [
        node["id"]
        for node in catalog["nodes"]
        if not set(node["type_affinity"]) & {tag.value for tag in V1_TYPE_TAGS}
    ]

    assert not unreachable, (
        f"v1 유형 축이 없어 어떤 셀로도 도달할 수 없는 노드: {unreachable}"
    )


def test_every_area_has_at_least_one_node() -> None:
    """영역 하나가 통째로 비면 그 행의 출제 요청이 목표 없이 나간다."""
    catalog = build_catalog()
    covered = {node["area_tag"] for node in catalog["nodes"]}

    assert covered == set(catalog["areas"]), (
        f"노드가 없는 영역: {sorted(set(catalog['areas']) - covered)}"
    )


def test_node_ids_are_unique_and_stable_shape() -> None:
    """id는 adapter 매핑 테이블의 키다 — 중복이면 매핑이 갈린다."""
    nodes = build_catalog()["nodes"]
    ids = [node["id"] for node in nodes]

    assert len(ids) == len(set(ids))
    assert all(node["label"] for node in nodes), "라벨 없는 노드는 화면에 못 그린다"
