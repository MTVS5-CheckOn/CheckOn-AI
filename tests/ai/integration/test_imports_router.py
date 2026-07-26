"""Import 라우터 통합 — POST(202)→GET→confirm 흐름·멱등·404·blocked (10_import_spec §1·§2).

FakeSourceLoader로 스토리지 fetch를 결정론화(스텁 대체). FakeMappingProvider는 기본 주입분.
"""

from __future__ import annotations

from collections.abc import Iterator
from io import BytesIO

import httpx
import pandas as pd  # type: ignore[import-untyped]
import pytest
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers.imports import reset_import_stores, set_import_stores

_HEADERS = {
    "X-Tenant-Id": "t1",
    "X-Request-Id": "r1",
    "Idempotency-Key": "t1:import:1",
}


def _xlsx(columns: dict[str, list[object]]) -> bytes:
    buffer = BytesIO()
    pd.DataFrame(columns).to_excel(buffer, index=False)
    return buffer.getvalue()


_ROSTER = _xlsx(
    {
        "원생명": ["김철수", "이영희"],
        "반": ["A1", "A1"],
        "등원일": ["2026-03-02", "2026-03-02"],
        "상태": ["재원", "재원"],
        "동의": ["동의", "동의"],
    }
)
_LEARNING_INCOMPLETE = _xlsx({"점수": [88, 91]})


class FakeSourceLoader:
    """url → 고정 바이트. 스토리지 fetch 스텁 대체(테스트 결정론)."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self._files = files

    def load(self, source_url: str) -> bytes:
        return self._files[source_url]


@pytest.fixture
def client() -> Iterator[TestClient]:
    reset_import_stores()
    set_import_stores(
        source_loader=FakeSourceLoader(
            {"s3://roster.xlsx": _ROSTER, "s3://learn.xlsx": _LEARNING_INCOMPLETE}
        )
    )
    yield TestClient(create_app())
    reset_import_stores()


def _post(
    client: TestClient, url: str, filename: str, key: str = "t1:import:1"
) -> httpx.Response:
    resp: httpx.Response = client.post(
        "/v1/imports",
        json={"source_url": url, "filename": filename},
        headers={**_HEADERS, "Idempotency-Key": key},
    )
    return resp


def test_post_profiles_and_returns_202_preview_ready(client: TestClient) -> None:
    resp = _post(client, "s3://roster.xlsx", "roster.xlsx")
    assert resp.status_code == 202
    data = resp.json()["data"]
    assert data["status"] == "preview_ready"
    assert data["job_id"]
    preview = data["mapping_preview"]
    assert preview["reused"] is False
    # 가드레일 — redactor 미주입이라 샘플 비어 있음(§5.2)
    assert preview["sample_rows"] == []
    # 실명이 응답 어디에도 없다
    assert "김철수" not in resp.text


def test_get_returns_stored_preview(client: TestClient) -> None:
    job_id = _post(client, "s3://roster.xlsx", "roster.xlsx").json()["data"]["job_id"]
    resp = client.get(f"/v1/imports/{job_id}", headers={"X-Tenant-Id": "t1", "X-Request-Id": "r"})
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "preview_ready"


def test_idempotent_repost_returns_same_job(client: TestClient) -> None:
    first = _post(client, "s3://roster.xlsx", "roster.xlsx").json()["data"]["job_id"]
    again = _post(client, "s3://roster.xlsx", "roster.xlsx").json()["data"]["job_id"]
    assert first == again


def test_idempotency_conflict_on_different_body(client: TestClient) -> None:
    _post(client, "s3://roster.xlsx", "roster.xlsx")
    resp = _post(client, "s3://learn.xlsx", "learn.xlsx")  # 같은 키, 다른 바디
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_missing_required_is_blocked(client: TestClient) -> None:
    resp = _post(client, "s3://learn.xlsx", "learn.xlsx", key="t1:import:learn")
    data = resp.json()["data"]
    assert data["status"] == "blocked"
    assert data["mapping_preview"]["blocked"] is True


def test_get_unknown_job_is_404(client: TestClient) -> None:
    resp = client.get("/v1/imports/nope", headers={"X-Tenant-Id": "t1", "X-Request-Id": "r"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_tenant_isolation_hides_other_tenant_job(client: TestClient) -> None:
    job_id = _post(client, "s3://roster.xlsx", "roster.xlsx").json()["data"]["job_id"]
    resp = client.get(f"/v1/imports/{job_id}", headers={"X-Tenant-Id": "t2", "X-Request-Id": "r"})
    assert resp.status_code == 404  # 존재 은닉


def test_confirm_transitions_to_transforming(client: TestClient) -> None:
    job_id = _post(client, "s3://roster.xlsx", "roster.xlsx").json()["data"]["job_id"]
    resp = client.post(
        f"/v1/imports/{job_id}/confirm",
        json={"spec_overrides": []},
        headers={**_HEADERS, "Idempotency-Key": "t1:confirm:1"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "transforming"


def test_confirm_rejects_non_standard_target_field(client: TestClient) -> None:
    job_id = _post(client, "s3://roster.xlsx", "roster.xlsx").json()["data"]["job_id"]
    resp = client.post(
        f"/v1/imports/{job_id}/confirm",
        json={"spec_overrides": [{"source_column": "점수A", "target_field": "made_up_field"}]},
        headers={**_HEADERS, "Idempotency-Key": "t1:confirm:2"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_SCHEMA"


def test_missing_header_is_400(client: TestClient) -> None:
    resp = client.post(
        "/v1/imports",
        json={"source_url": "s3://roster.xlsx", "filename": "roster.xlsx"},
        headers={"X-Tenant-Id": "t1"},  # X-Request-Id·Idempotency-Key 누락
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_SCHEMA"


def test_meta_versions_always_present(client: TestClient) -> None:
    resp = _post(client, "s3://roster.xlsx", "roster.xlsx")
    versions = resp.json()["meta"]["versions"]
    assert {"pipeline", "engine", "schema", "contract"} <= set(versions)
