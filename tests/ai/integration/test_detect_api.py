"""POST /v1/detect HTTP 통합 — envelope·검증·멱등·결정론 (TestClient).

사양: docs/04_api_contract.md §2.1·§2.2·§2.3 · docs/part_a/09_detect_spec.md §3.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers.detect import reset_detection_store, reset_idempotency_store
from ai.contracts.execution import VersionSet
from ai.evaluation.fake_snapshot import fixture_composite_risk, to_payload

_HEADERS = {
    "X-Tenant-Id": "teacher_alias_001",
    "X-Request-Id": "req-1",
    "Idempotency-Key": "teacher_alias_001:2026-07-13",
}


@pytest.fixture
def client() -> Iterator[TestClient]:
    reset_idempotency_store()  # 테스트 간 저장소 격리
    reset_detection_store()
    yield TestClient(create_app())
    reset_idempotency_store()
    reset_detection_store()


def _payload() -> dict[str, Any]:
    return to_payload(fixture_composite_risk())


def test_success_envelope_shape(client: TestClient) -> None:
    """정상 200 — envelope 형태·versions 키 집합·signals."""
    resp = client.post("/v1/detect", json=_payload(), headers=_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"data", "error", "meta"}
    assert body["error"] is None
    assert set(body["data"]) == {"signals", "stats"}
    assert "execution_id" in body["meta"]
    # meta.versions 키 = VersionSet 필드에서 _version 뗀 집합
    expected_keys = {name.removesuffix("_version") for name in VersionSet.model_fields}
    assert set(body["meta"]["versions"]) == expected_keys
    # detection 실행 — threshold는 config 버전, LLM/B 전용 키는 null
    assert body["meta"]["versions"]["threshold"] == "default-v1"
    assert body["meta"]["versions"]["prompt"] is None
    assert body["meta"]["versions"]["graph"] is None


def test_missing_header_400(client: TestClient) -> None:
    headers = {k: v for k, v in _HEADERS.items() if k != "X-Tenant-Id"}
    resp = client.post("/v1/detect", json=_payload(), headers=headers)
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "INVALID_SCHEMA"
    assert "X-Tenant-Id" in body["error"]["detail"]["missing_headers"]


def test_error_envelope_carries_versions(client: TestClient) -> None:
    """실패 응답에도 meta.versions가 10키 집합으로 실린다 (04 §2.2 A판정 7/22)."""
    headers = {k: v for k, v in _HEADERS.items() if k != "X-Tenant-Id"}
    resp = client.post("/v1/detect", json=_payload(), headers=headers)
    body = resp.json()
    assert body["meta"] is not None, "실패에도 meta는 null이 아니어야 한다"
    expected_keys = {name.removesuffix("_version") for name in VersionSet.model_fields}
    assert set(body["meta"]["versions"]) == expected_keys
    assert body["meta"]["execution_id"] is None  # 실행 전 오류 — execution_id 없음


def test_x_request_id_echoed(client: TestClient) -> None:
    """X-Request-Id를 응답 헤더로 echo (성공·실패 모두)."""
    ok = client.post("/v1/detect", json=_payload(), headers=_HEADERS)
    assert ok.headers.get("X-Request-Id") == _HEADERS["X-Request-Id"]
    bad_headers = {k: v for k, v in _HEADERS.items() if k != "X-Tenant-Id"}
    bad = client.post("/v1/detect", json=_payload(), headers=bad_headers)
    assert bad.headers.get("X-Request-Id") == bad_headers["X-Request-Id"]


def test_extra_field_400_with_path(client: TestClient) -> None:
    payload = _payload()
    payload["snapshot_meta"]["injected"] = "x"
    resp = client.post("/v1/detect", json=payload, headers=_HEADERS)
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "INVALID_SCHEMA"
    fields = {item["field"] for item in body["error"]["detail"]}
    assert any("injected" in f for f in fields)


def test_missing_required_body_field_400(client: TestClient) -> None:
    payload = _payload()
    del payload["snapshot_meta"]["week_start"]
    resp = client.post("/v1/detect", json=payload, headers=_HEADERS)
    assert resp.status_code == 400
    fields = {item["field"] for item in resp.json()["error"]["detail"]}
    assert any("week_start" in f for f in fields)


def test_idempotent_same_body_returns_cached(client: TestClient) -> None:
    """같은 키 + 같은 바디 = 기존 결과 재반환(execution_id까지 동일)."""
    first = client.post("/v1/detect", json=_payload(), headers=_HEADERS).json()
    second = client.post("/v1/detect", json=_payload(), headers=_HEADERS).json()
    assert first == second


def test_idempotent_conflict_409(client: TestClient) -> None:
    """같은 키 + 다른 바디 = 409 IDEMPOTENCY_CONFLICT."""
    client.post("/v1/detect", json=_payload(), headers=_HEADERS)
    other = to_payload(fixture_composite_risk(seed=99))  # 다른 snapshot_hash
    resp = client.post("/v1/detect", json=other, headers=_HEADERS)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_determinism_same_signals_across_keys(client: TestClient) -> None:
    """같은 바디, 다른 Idempotency-Key = 같은 signals, execution_id만 다름."""
    payload = _payload()
    r1 = client.post(
        "/v1/detect", json=payload, headers={**_HEADERS, "Idempotency-Key": "k1"}
    ).json()
    r2 = client.post(
        "/v1/detect", json=payload, headers={**_HEADERS, "Idempotency-Key": "k2"}
    ).json()
    assert r1["data"] == r2["data"]  # signals·stats 동일
    assert r1["meta"]["execution_id"] != r2["meta"]["execution_id"]
