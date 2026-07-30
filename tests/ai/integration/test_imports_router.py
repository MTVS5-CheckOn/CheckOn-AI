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

# 실 redactor 가드레일용 — 비의심 컬럼(비고)에 실명·전화가 섞인 손 작성 픽스처.
_ROSTER_PII = _xlsx(
    {
        "원생명": ["김철수", "이영희"],  # 의심 컬럼 → 샘플에서 값 제외(통계만)
        "반": ["A1", "A1"],
        "등원일": ["2026-03-02", "2026-03-02"],
        "상태": ["재원", "재원"],
        "동의": ["동의", "동의"],
        # 비의심 컬럼 — 마스킹 후 샘플에 들어감
        "비고": ["김철수 어머니 010-1234-5678", "이영희 학생 010-2222-3333"],
    }
)


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


def test_confirm_keeps_preview_ready(client: TestClient) -> None:
    """확정 재검증 통과 — transforming으로 보내지 않는다(전체 행 변환은 백엔드 소유 §4)."""
    job_id = _post(client, "s3://roster.xlsx", "roster.xlsx").json()["data"]["job_id"]
    resp = client.post(
        f"/v1/imports/{job_id}/confirm",
        json={"spec_overrides": []},
        headers={**_HEADERS, "Idempotency-Key": "t1:confirm:1"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "preview_ready"
    assert resp.json()["data"]["mapping_preview"]["blocked"] is False


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


def test_real_redactor_masks_profile_samples_zero_realname() -> None:
    """실 redactor 주입 → profile 샘플 보관 활성 · 원문 잔존 0 · ⟪토큰⟫ 존재(§5.2).

    가드레일은 **profile 직렬화** 기준(§3.1) — 샘플은 서버측 profile에 보관되고(LLM 1-shot 입력용),
    preview 응답 노출은 별개(로직 신설 금지 — 후속). 기대값은 손 작성(엔진 산출 아님).
    """
    import ai.api.routers.imports as imports_router
    from ai.runtime.redaction import redact

    reset_import_stores()
    set_import_stores(
        source_loader=FakeSourceLoader({"s3://pii.xlsx": _ROSTER_PII}),
        redactor=redact,  # 실 redaction 엔진을 라우터 조립부에서 주입
    )
    try:
        resp = TestClient(create_app()).post(
            "/v1/imports",
            json={"source_url": "s3://pii.xlsx", "filename": "pii.xlsx"},
            headers={**_HEADERS, "Idempotency-Key": "t1:pii"},
        )
        assert resp.status_code == 202
        job_id = resp.json()["data"]["job_id"]
        job = imports_router._job_store.get(job_id)
        assert job is not None and job.profile is not None
        samples = job.profile.sheets[0].sample_rows
        blob = repr(job.profile)  # profile 직렬화
        # 샘플 보관 활성 — redactor 주입 시 profile 샘플이 채워진다(미주입이면 () 였다)
        assert samples, "실 redactor 주입 시 profile 샘플이 채워져야 한다"
        # 원문 실명·전화 잔존 0 (손 작성 기대값)
        for leaked in ("김철수", "이영희", "010-1234-5678", "010-2222-3333"):
            assert leaked not in blob, f"원문 잔존: {leaked}"
        # 마스킹 토큰 ⟪…⟫ 존재(비고 컬럼이 마스킹돼 샘플에 들어감)
        assert "⟪" in blob and "⟫" in blob
        # 의심 컬럼(원생명)은 샘플 키에 없다(값 미노출 — 통계만)
        assert all("원생명" not in row for row in samples)
    finally:
        reset_import_stores()
