"""counsel 초안 라우터 — 인박스 계약 v1 §4 왕복 (POST 202 → GET envelope).

**기대값의 출처는 계약 문서다** — `_REQUEST`는 §4-① 예시를 손으로 옮긴 것이고,
`_RESULT_FIELDS`는 §4-③ result 필드 전수다. 엔진 산출로 기대값을 만들지 않는다.

CI 기본은 Fake provider다 — 실 LLM 호출 0(`imports` 라우터 선례와 같은 규약).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers.counsel import reset_counsel_stores
from ai.api.routers.counsel import router as counsel_router

_HEADERS = {
    "X-Tenant-Id": "t1",
    "X-Request-Id": "rq-counsel-1",
    "Idempotency-Key": "t1:counsel:1",
}

#: 계약 §4-① 예시 — 단 `labels`는 4축 정본 표기로 옮겼다(05 §7-4 · 계약 예시의
#: `anxiety_sensitive`는 enum에 없는 표기라 v1 범위 정정 통보 대상이다).
_REQUEST: dict[str, Any] = {
    "inquiry": {
        "inquiry_ref": "iq_884",
        "topic": "complaint",
        "urgency": "immediate",
        "received_at": "2026-07-31T14:20:00+09:00",
        "text_masked": "요즘 아이가 힘들어하는 것 같은데…",
    },
    "student_ref": "st_8f2a",
    "parent_ref": "pa_9c1d",
    "class_ref": "cl_a1",
    "labels": ["narrative", "attitude", "anxious", "frequent"],
    "dismissed_suggestions": [{"axis": "frequency", "value": "monthly"}],
    "context": {
        "snapshot_hash": "sha256:" + "a" * 64,
        "period_label": "2026년 7월",
        "facts": [
            {"record_id": "le_2041", "summary": "6월 지문 42개·312문항"},
            {"record_id": "le_2077", "summary": "제출률 100% (4주)"},
        ],
    },
}

_RESULT_FIELDS = {
    "draft_status",
    "text",
    "citations",
    "labels_applied",
    "label_suggestions",
    "status_reason",
    "generated_at",
}


def _mounted_app() -> FastAPI:
    """counsel 라우터가 달린 앱.

    `api/app.py`는 **양자 승인 파일**이라 등록 커밋을 이 브랜치 마지막으로 몰았다 —
    그 전까지는 여기서 붙여 라우터 자체의 계약을 검증한다(등록 후에는 중복 등록하지 않는다).
    """
    app = create_app()
    mounted = any(
        getattr(route, "path", "").startswith("/v1/counsel") for route in app.routes
    )
    if not mounted:
        app.include_router(counsel_router)
    return app


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    reset_counsel_stores()
    yield
    reset_counsel_stores()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(_mounted_app()) as test_client:
        yield test_client


def _post(client: TestClient, **overrides: object) -> httpx.Response:
    body = {**_REQUEST, **overrides}
    response: httpx.Response = client.post(
        "/v1/counsel/drafts", json=body, headers=_HEADERS
    )
    return response


# ── §4-① POST → 202 ──────────────────────────────────────────────


def test_post_returns_202_with_job_id(client: TestClient) -> None:
    response = _post(client)
    assert response.status_code == 202, response.text
    data = response.json()["data"]
    assert data["job_id"]
    assert data["status"]


def test_post_echoes_request_id(client: TestClient) -> None:
    assert _post(client).headers["X-Request-Id"] == "rq-counsel-1"


def test_missing_header_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/v1/counsel/drafts",
        json=_REQUEST,
        headers={k: v for k, v in _HEADERS.items() if k != "X-Tenant-Id"},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "INVALID_SCHEMA"
    assert body["meta"]["versions"]  # 실패에도 meta.versions (04 §2.2)


def test_schema_violation_is_rejected(client: TestClient) -> None:
    response = _post(client, inquiry={"inquiry_ref": "iq_884"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_SCHEMA"


# ── 라벨 → 4축 (05 §7) ───────────────────────────────────────────


def test_unknown_label_value_is_rejected(client: TestClient) -> None:
    """🔴 미지값은 조용히 삼키지 않는다 — 표기 드리프트를 즉시 드러낸다(05 §7-4)."""
    response = _post(client, labels=["narrative", "anxiety_sensitive"])
    assert response.status_code == 400
    assert "anxiety_sensitive" in str(response.json()["error"]["detail"])


def test_duplicate_axis_is_rejected(client: TestClient) -> None:
    response = _post(client, labels=["data", "narrative"])
    assert response.status_code == 400


def test_partial_labels_are_accepted(client: TestClient) -> None:
    """부분 라벨은 정상이다 — 누락 축은 기본값(05 §7-2)."""
    assert _post(client, labels=["narrative"]).status_code == 202


def test_empty_labels_are_accepted(client: TestClient) -> None:
    """개통 첫날 전원이 라벨 0개다(라벨 검토함 계약 §6)."""
    assert _post(client, labels=[]).status_code == 202


# ── §4-③ GET → envelope ─────────────────────────────────────────


def _completed(client: TestClient) -> dict[str, Any]:
    job_id = _post(client).json()["data"]["job_id"]
    response = client.get(f"/v1/counsel/drafts/{job_id}", headers=_HEADERS)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_get_returns_contract_envelope(client: TestClient) -> None:
    body = _completed(client)
    assert body["error"] is None
    assert body["meta"]["execution_id"]
    assert body["meta"]["versions"]
    assert body["data"]["status"] == "succeeded"


def test_result_carries_every_contract_field(client: TestClient) -> None:
    result = _completed(client)["data"]["result"]
    assert _RESULT_FIELDS <= set(result)


def test_generated_draft_has_citations(client: TestClient) -> None:
    """🔴 불변식 2 — 근거 없는 초안은 존재할 수 없다."""
    result = _completed(client)["data"]["result"]
    assert result["draft_status"] == "generated", result["status_reason"]
    assert len(result["citations"]) >= 1
    assert result["citations"][0]["record_id"] == "le_2041"
    assert result["text"]


def test_labels_applied_reports_all_four_axes(client: TestClient) -> None:
    """기본값이 쓰였는지 화면이 알 수 있어야 한다(05 §7-2)."""
    result = _completed(client)["data"]["result"]
    assert set(result["labels_applied"]) == {
        "narrative",
        "attitude",
        "anxious",
        "frequent",
    }


def test_label_suggestions_is_empty_in_v1(client: TestClient) -> None:
    """⚠ v1 상수 [] — 생성기 미구현(99 D ㊲). BE에 통보된 사실이다."""
    assert _completed(client)["data"]["result"]["label_suggestions"] == []


def test_unknown_job_is_404(client: TestClient) -> None:
    response = client.get(
        "/v1/counsel/drafts/00000000-0000-4000-8000-00000000ffff", headers=_HEADERS
    )
    assert response.status_code == 404


def test_other_tenant_cannot_read_job(client: TestClient) -> None:
    """테넌트 격리 — 다른 테넌트의 job_id는 존재를 숨긴다(404)."""
    job_id = _post(client).json()["data"]["job_id"]
    response = client.get(
        f"/v1/counsel/drafts/{job_id}", headers={**_HEADERS, "X-Tenant-Id": "t2"}
    )
    assert response.status_code == 404
