"""`POST /v1/classify` 라우터 — 계약면 (04 §3.5).

CI 기본은 Fake provider라 실 LLM 호출 0이다.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ai.api.app import create_app

_HEADERS = {"X-Tenant-Id": "t1", "X-Request-Id": "rq-classify-1"}
_REQUEST: dict[str, Any] = {
    "inquiry_ref": "iq_204",
    "body_text": "여름방학 특강 시간표가 궁금합니다",
}

_RESULT_FIELDS = {
    "inquiry_ref",
    "topic",
    "sentiment",
    "urgency",
    "confidence",
    "classified",
    "fallback_reason",
}


#: 🔴 앱을 띄우면 counsel 라우터의 기동 가드가 함께 돈다 — provider는 **기본값이 없다**.
#: 종전 기본값이 `FakeCounselProvider()`였고 그게 프로덕션에서 답하던 것이 결함이었다.
#: 이 테스트는 counsel과 무관하지만 `create_app()`을 쓰므로 조립 루트 몫을 대신 한다.
def _wire_counsel_provider() -> None:
    from ai.api.routers.counsel import set_counsel_provider
    from ai.composition.counsel.provider import FakeCounselProvider

    set_counsel_provider(FakeCounselProvider())


@pytest.fixture
def client() -> Iterator[TestClient]:
    _wire_counsel_provider()
    with TestClient(create_app()) as test_client:
        yield test_client


def test_post_returns_200_with_three_axes(client: TestClient) -> None:
    response = client.post("/v1/classify", json=_REQUEST, headers=_HEADERS)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert set(data) == _RESULT_FIELDS
    assert set(data["confidence"]) == {"topic", "sentiment", "urgency"}


def test_prompt_version_is_not_null(client: TestClient) -> None:
    """🔴 counsel이 `prompt=null`로 나갔던 관측 잔여(B-5)를 반복하지 않는다."""
    response = client.post("/v1/classify", json=_REQUEST, headers=_HEADERS)
    assert response.json()["meta"]["versions"]["prompt"] == "v1"


def test_idempotency_key_is_not_required(client: TestClient) -> None:
    """부작용 없는 동기 호출이라 멱등 키를 요구하지 않는다(04 §3.5).

    BE가 관리할 상태를 늘리지 않는다 — 같은 본문은 결정론 설정으로 같은 결과다.
    """
    assert "Idempotency-Key" not in _HEADERS
    assert client.post("/v1/classify", json=_REQUEST, headers=_HEADERS).status_code == 200


@pytest.mark.parametrize("missing", ["X-Tenant-Id", "X-Request-Id"])
def test_missing_required_header_is_400(client: TestClient, missing: str) -> None:
    headers = {k: v for k, v in _HEADERS.items() if k != missing}
    response = client.post("/v1/classify", json=_REQUEST, headers=headers)
    assert response.status_code == 400


def test_schema_violation_is_400(client: TestClient) -> None:
    response = client.post(
        "/v1/classify", json={"inquiry_ref": "iq_1"}, headers=_HEADERS
    )
    assert response.status_code == 400


def test_error_detail_never_carries_the_body(client: TestClient) -> None:
    """🔴 `body_text`는 **원문**이라 검증 에러 detail에 값이 실리면 유출이다(04 §2.3)."""
    secret = "김민준 010-1234-5678"
    response = client.post(
        "/v1/classify",
        json={"inquiry_ref": "", "body_text": secret},
        headers=_HEADERS,
    )
    assert response.status_code == 400
    assert secret not in response.text
    assert "김민준" not in response.text


def test_openapi_exposes_the_path(client: TestClient) -> None:
    assert "/v1/classify" in create_app().openapi()["paths"]
