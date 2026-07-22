"""영속 계층 투명성 — 저장 계층 도입이 감지 응답을 바꾸지 않는다 (D-② 커밋⑥).

원장·멱등 적재는 산출물을 남길 뿐 응답을 바꾸면 안 된다(엔진은 순수·요청 구동). 이 검사는
① API 경로(dedupe→엔진→적재) 응답 data == 순수 엔진 data, ② 데모 예시 JSON의 data 불변,
③ 멱등 재호출 동일을 고정한다 — 회귀 시 골든/데모가 조용히 흔들리는 것을 막는다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers.detect import reset_detection_store, reset_idempotency_store
from ai.detection.engine import detect
from ai.evaluation.demo_snapshot import run_demo
from ai.evaluation.fake_snapshot import fixture_composite_risk, to_payload

_HEADERS = {
    "X-Tenant-Id": "teacher_alias_001",
    "X-Request-Id": "req-1",
    "Idempotency-Key": "teacher_alias_001:2026-07-13",
}

_DEMO_RESPONSE = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "part_a"
    / "examples"
    / "detect_demo_response.json"
)


@pytest.fixture(autouse=True)
def _isolate() -> None:
    reset_idempotency_store()
    reset_detection_store()


def test_api_data_equals_pure_engine() -> None:
    """API 경로 응답 data == 순수 detect() data — 적재는 응답을 바꾸지 않는다."""
    request = fixture_composite_risk()
    pure = detect(request).model_dump(mode="json")
    resp = TestClient(create_app()).post(
        "/v1/detect", json=to_payload(request), headers=_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == pure


def test_api_data_equals_pure_engine_multi_seed() -> None:
    """여러 시드에서도 투명 — 적재 경로가 특정 입력에만 개입하지 않음을 확인."""
    client = TestClient(create_app())
    for seed in (1, 7, 42):
        request = fixture_composite_risk(seed=seed)
        pure = detect(request).model_dump(mode="json")
        resp = client.post(
            "/v1/detect",
            json=to_payload(request),
            headers={**_HEADERS, "Idempotency-Key": f"k-{seed}"},
        )
        assert resp.json()["data"] == pure, f"seed={seed} 응답이 순수 엔진과 다르다"


def test_demo_example_data_unchanged() -> None:
    """커밋된 데모 응답 JSON의 data가 현재 실행과 동일(meta.execution_id는 매 실행 uuid라 제외)."""
    committed = json.loads(_DEMO_RESPONSE.read_text(encoding="utf-8"))
    _payload, body, replay = run_demo()
    assert body["data"] == committed["data"], "데모 응답 data가 커밋본과 다르다"
    assert replay == body, "멱등 재호출이 첫 응답과 다르다"


def _assert_versions(body: dict[str, Any]) -> None:
    assert set(body["meta"]["versions"]) >= {"pipeline", "engine", "schema", "contract"}


def test_demo_versions_present() -> None:
    """데모 응답도 meta.versions를 싣는다(재현성 — 실패 응답도 싣는 계약)."""
    _payload, body, _replay = run_demo()
    _assert_versions(body)
