"""진단 라우터 계약 — Step 1 area×type 그리드가 HTTP로 나가는지.

이 엔드포인트가 없어서 강사 화면 Step 1이 막혀 있었다(계산은 있었고 노출 경로가 0건).
여기서 잠그는 것은 **판정 축**과 **빈 칸의 의미** 둘이다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers import diagnosis as diagnosis_router
from ai.contracts.taxonomy import V1_TYPE_TAGS, AreaTag, TypeTag
from ai.db.repositories.run_store import InMemoryRunStore
from ai.diagnosis.config import load_diagnosis_config
from ai.diagnosis.diagnoser import diagnose as original_diagnose

_HEADERS = {
    "X-Tenant-Id": "tenant-diagnosis",
    "X-Request-Id": "request-diagnosis",
    "Idempotency-Key": "idem-diagnosis",
}
_NOW = datetime(2026, 7, 15, 9, 0, tzinfo=UTC)
_NODE_ID = "language.grammar.phoneme.system"


def _events(
    *,
    area_tag: AreaTag,
    type_tag: TypeTag,
    total: int,
    correct: int,
    prefix: str,
    skill_node_id: str | None = None,
) -> list[dict[str, Any]]:
    return [
        {
            "event_id": f"{prefix}-{index:03d}",
            "area_tag": area_tag.value,
            "type_tag": type_tag.value,
            "correct": index < correct,
            "occurred_at": _NOW.isoformat(),
            "tag_confirmed": True,
            "skill_node_id": skill_node_id,
        }
        for index in range(total)
    ]


def _body(events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "student_ref": "st_diagnosis_alias",
        "period": {"from_date": "2026-07-01", "to_date": "2026-07-15"},
        "as_of": _NOW.isoformat(),
        "snapshot_hash": "sha256:diagnosis-snapshot",
        "events": events,
    }


def _mixed_events() -> list[dict[str, Any]]:
    """한 셀은 판정 가능(n≥10)하고 다른 셀은 표본 부족(n<10)인 입력."""

    return [
        *_events(
            area_tag=AreaTag.LANGUAGE,
            type_tag=TypeTag.CONCEPT,
            total=20,
            correct=18,
            prefix="strong",
            skill_node_id=_NODE_ID,
        ),
        *_events(
            area_tag=AreaTag.LANGUAGE,
            type_tag=TypeTag.INFER,
            total=20,
            correct=4,
            prefix="weak",
        ),
        *_events(
            area_tag=AreaTag.READING,
            type_tag=TypeTag.FACT,
            total=3,
            correct=1,
            prefix="thin",
        ),
    ]


def _prepare() -> InMemoryRunStore:
    diagnosis_router.reset_diagnosis_router()
    run_store = InMemoryRunStore()
    diagnosis_router.set_diagnosis_run_store(run_store)
    return run_store


def test_grid_covers_every_axis_coordinate_not_only_the_observed_ones() -> None:
    """화면은 5×4를 통째로 그린다 — 관측 안 된 칸도 자리를 갖는다."""
    _prepare()

    with TestClient(create_app()) as client:
        response = client.post("/v1/diagnosis", headers=_HEADERS, json=_body(_mixed_events()))

    assert response.status_code == 200
    grid = response.json()["data"]["grid"]
    assert len(grid["cells"]) == len(AreaTag) * len(V1_TYPE_TAGS) == 20
    assert grid["areas"] == [area.value for area in AreaTag]
    # 🔴 예약 태그는 축에 없다 — 영원히 빈 열을 그리게 두지 않는다.
    assert "apply" not in grid["types"]


def test_empty_cell_is_not_reported_as_zero_percent() -> None:
    """🔴 표본 0에 `acc=0.0`을 채우면 **정답률 0%로 읽힌다.**

    `no_data`(제출 0건)와 `unknown`(1건 이상이지만 최소 표본 미달)은 강사의 다음 행동이
    다르다 — 전자는 "출제해서 재보자", 후자는 "조금 더 모으자"다.
    """
    _prepare()

    with TestClient(create_app()) as client:
        response = client.post("/v1/diagnosis", headers=_HEADERS, json=_body(_mixed_events()))

    cells = {cell["key"]: cell for cell in response.json()["data"]["grid"]["cells"]}

    no_data = cells["media×fact"]
    assert no_data["n"] == 0
    assert no_data["acc"] is None
    assert no_data["verdict"] is None

    thin = cells["reading×fact"]
    assert thin["n"] == 3
    assert thin["acc"] is not None
    assert thin["verdict"] == "unknown"


def test_grid_verdicts_are_the_cell_axis_and_nodes_stay_separate() -> None:
    """그리드는 셀 축(ok·weak·unknown)이고 노드 축은 별도 목록이다."""
    _prepare()

    with TestClient(create_app()) as client:
        response = client.post("/v1/diagnosis", headers=_HEADERS, json=_body(_mixed_events()))

    data = response.json()["data"]
    cells = {cell["key"]: cell for cell in data["grid"]["cells"]}

    assert cells["language×infer"]["verdict"] == "weak"
    assert cells["language×infer"]["severity"] is not None
    assert cells["language×concept"]["verdict"] == "ok"
    assert {cell["verdict"] for cell in data["grid"]["cells"]} <= {
        "ok",
        "weak",
        "unknown",
        None,
    }, "노드 축 값(suspect·weak_confirmed·root_candidate)이 그리드에 섞이면 안 된다"

    node_verdicts = {node["verdict"] for node in data["weakness_map"]["nodes"].values()}
    assert node_verdicts <= {"suspect", "weak_confirmed", "ok"}


def test_grid_carries_the_minimum_sample_threshold_it_judged_with() -> None:
    """화면의 「최소 N문항」 문구가 설정과 갈리지 않게 값을 같이 싣는다."""
    _prepare()
    expected = load_diagnosis_config().params.cell_min_items

    with TestClient(create_app()) as client:
        response = client.post("/v1/diagnosis", headers=_HEADERS, json=_body(_mixed_events()))

    assert response.json()["data"]["grid"]["cell_min_items"] == expected


def test_weakness_map_labels_the_config_version_it_used() -> None:
    """재현 키 — 같은 스냅숏이라도 설정 버전이 다르면 다른 지도다(불변식 8)."""
    _prepare()
    document = load_diagnosis_config()

    with TestClient(create_app()) as client:
        response = client.post("/v1/diagnosis", headers=_HEADERS, json=_body(_mixed_events()))

    weakness_map = response.json()["data"]["weakness_map"]
    assert weakness_map["config_version"] == document.version
    assert weakness_map["snapshot_hash"] == "sha256:diagnosis-snapshot"
    versions = response.json()["meta"]["versions"]
    assert versions["graph"] == "curriculum-five-area-v1"
    assert versions["taxonomy"] == document.expected_taxonomy_version


def test_misconception_report_is_separate_from_weakness_map() -> None:
    _prepare()
    events = _mixed_events()
    wrong_events = [event for event in events if not event["correct"]]
    for event in wrong_events[:2]:
        event.update(
            chosen_no=2,
            misconception_tag="application_target_substitution",
        )
    wrong_events[2]["chosen_no"] = 3

    with TestClient(create_app()) as client:
        response = client.post("/v1/diagnosis", headers=_HEADERS, json=_body(events))

    assert response.status_code == 200
    data = response.json()["data"]
    assert "misconceptions" not in data["weakness_map"]
    assert data["misconceptions"]["by_area"] == {
        "language": {"application_target_substitution": 2}
    }
    assert data["misconceptions"]["excluded_missing_chosen_no"] == 17
    assert data["misconceptions"]["excluded_missing_misconception_tag"] == 1


def test_insufficient_data_is_a_200_status_not_an_error() -> None:
    """데이터 부족은 오류가 아니다 — 게이트 거부와 같은 처리(불변식 4)."""
    _prepare()

    with TestClient(create_app()) as client:
        response = client.post("/v1/diagnosis", headers=_HEADERS, json=_body([]))

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "rejected_insufficient"
    assert data["weakness_map"] is None
    assert data["status_reason"]
    # 🔴 지도가 없어도 그리드 틀은 나간다 — 화면이 빈 표를 그릴 수 있어야 한다.
    assert len(data["grid"]["cells"]) == 20
    assert all(cell["verdict"] is None for cell in data["grid"]["cells"])


def test_report_inputs_cross_the_http_boundary_into_diagnosis_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BE가 보낸 참고치·개입이 요청 모델에서 조용히 버려지지 않는다."""

    _prepare()
    captured: dict[str, Any] = {}

    def recording_diagnose(diagnosis_input: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        captured["input"] = diagnosis_input
        return original_diagnose(diagnosis_input, *args, **kwargs)

    monkeypatch.setattr(diagnosis_router, "diagnose", recording_diagnose)
    body = {
        **_body(_mixed_events()),
        "national_percentile": {
            "value": 68.0,
            "source": "전국 모의평가 표준화 집계",
            "as_of": "2026-06-30",
            "population_size": 125000,
        },
        "interventions": [
            {
                "event_id": "intervention-1",
                "kind": "supplement",
                "occurred_at": "2026-07-01T10:00:00+09:00",
            },
            {
                "event_id": "intervention-2",
                "kind": "counsel",
                "occurred_at": "2026-07-08T10:00:00+09:00",
            },
        ],
    }

    with TestClient(create_app()) as client:
        response = client.post("/v1/diagnosis", headers=_HEADERS, json=body)

    assert response.status_code == 200
    diagnosis_input = captured["input"]
    assert diagnosis_input.national_percentile.value == 68.0
    assert [event.kind.value for event in diagnosis_input.interventions] == [
        "supplement",
        "counsel",
    ]


def test_unknown_skill_node_is_a_400_the_caller_can_fix() -> None:
    """그래프에 없는 노드 참조는 호출자가 고칠 요청이다 — 500으로 올리면 BE가 재시도한다."""
    _prepare()
    events = _events(
        area_tag=AreaTag.LANGUAGE,
        type_tag=TypeTag.CONCEPT,
        total=12,
        correct=6,
        prefix="ghost",
        skill_node_id="language.grammar.does-not-exist",
    )

    with TestClient(create_app()) as client:
        response = client.post("/v1/diagnosis", headers=_HEADERS, json=_body(events))

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_SCHEMA"


def test_tenant_id_must_come_from_the_header_not_the_body() -> None:
    """헤더 파생 필드를 바디로 받으면 두 값이 갈릴 때 어느 쪽이 정본인지 없다."""
    _prepare()
    body = {**_body(_mixed_events()), "tenant_id": "tenant-spoofed"}

    with TestClient(create_app()) as client:
        response = client.post("/v1/diagnosis", headers=_HEADERS, json=body)

    assert response.status_code == 400
    assert response.json()["error"]["detail"] == {"fields": ["tenant_id"]}


def test_same_key_same_body_replays_and_different_body_conflicts() -> None:
    _prepare()

    with TestClient(create_app()) as client:
        first = client.post("/v1/diagnosis", headers=_HEADERS, json=_body(_mixed_events()))
        replay = client.post("/v1/diagnosis", headers=_HEADERS, json=_body(_mixed_events()))
        other = {**_body(_mixed_events()), "snapshot_hash": "sha256:another-snapshot"}
        conflict = client.post("/v1/diagnosis", headers=_HEADERS, json=other)

    assert first.status_code == 200
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_run_ledger_records_the_execution_with_no_llm_usage() -> None:
    """결정론 경로라 호출이 0건이다 — 사용 축(model_*)은 비고 선언 축(버전)은 찬다."""
    run_store = _prepare()

    with TestClient(create_app()) as client:
        response = client.post("/v1/diagnosis", headers=_HEADERS, json=_body(_mixed_events()))

    assert len(run_store.runs) == 1
    run = next(iter(run_store.runs.values()))
    assert str(run.execution_id) == response.json()["meta"]["execution_id"]
    assert run.capability.value == "diagnosis"
    assert run.model_provider is None
    assert run.model_name is None
    assert run.prompt_version is None
    assert run.input_snapshot_hash == "sha256:diagnosis-snapshot"
