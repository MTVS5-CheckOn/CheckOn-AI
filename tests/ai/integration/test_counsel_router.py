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
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers.counsel import reset_counsel_stores, set_counsel_provider
from ai.composition.counsel.provider import FakeCounselProvider
from ai.db.store_factory import reset_shared_agent_runtime

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
        "topic": "grade",
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





@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    reset_shared_agent_runtime()  # A·B 공용 잡 원장(99 ㊒)
    reset_counsel_stores()
    yield
    reset_shared_agent_runtime()  # A·B 공용 잡 원장(99 ㊒)
    reset_counsel_stores()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
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


# ── 멱등 (04 §2.3 · 점검 B-6) ────────────────────────────────────
#
# 구현은 라우터 신설 커밋에 들어 있다 — POST 핸들러의 제어 흐름이 한 갈래라 멱등 조회를
# 떼어 놓으면 "저장은 하는데 조회는 안 하는" 중간 상태가 커밋으로 남는다.
# 여기서는 계약을 고정한다: 같은 키 + 같은 바디 = 재반환 · 다른 바디 = 409.


def test_same_key_same_body_replays_the_first_result(client: TestClient) -> None:
    """재전송이 이중 생성을 만들지 않는다 — job_id가 그대로다."""
    first = _post(client)
    second = _post(client)
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()


def test_same_key_same_body_does_not_run_the_worker_twice(client: TestClient) -> None:
    """멱등 히트는 **실행을 건너뛴다** — LLM 원가가 두 번 나가면 안 된다."""
    provider = FakeCounselProvider(drafts=["이번 기간 학습 상황을 정리해 드립니다."])
    set_counsel_provider(provider)
    _post(client)
    calls_after_first = len(provider.write_calls)
    _post(client)
    assert len(provider.write_calls) == calls_after_first


def test_same_key_different_body_is_409(client: TestClient) -> None:
    _post(client)
    response = _post(client, student_ref="st_other")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_different_key_creates_a_new_job(client: TestClient) -> None:
    first = _post(client).json()["data"]["job_id"]
    second = client.post(
        "/v1/counsel/drafts",
        json=_REQUEST,
        headers={**_HEADERS, "Idempotency-Key": "t1:counsel:2"},
    ).json()["data"]["job_id"]
    assert first != second


def test_idempotency_is_scoped_by_tenant(client: TestClient) -> None:
    """키 스코프는 (tenant_id, endpoint, key)다 — 다른 테넌트가 같은 키를 써도 독립이다."""
    first = _post(client).json()["data"]["job_id"]
    second = client.post(
        "/v1/counsel/drafts",
        json=_REQUEST,
        headers={**_HEADERS, "X-Tenant-Id": "t2"},
    ).json()["data"]["job_id"]
    assert first != second


# ── 앱 등록 (api/app.py — 양자 승인 파일) ────────────────────────


def test_counsel_routes_are_registered_in_the_app() -> None:
    """`create_app()`이 실제로 이 경로를 서빙한다 — 라우터만 있고 등록이 빠지면 무의미하다.

    `app.routes`가 아니라 OpenAPI 스키마를 본다 — 이 FastAPI 버전은 include_router 결과를
    `path`가 없는 래퍼로 담아서, 경로 순회로는 등록 누락을 검출하지 못한다(실측).
    """
    paths = set(create_app().openapi()["paths"])
    assert "/v1/counsel/drafts" in paths
    assert "/v1/counsel/drafts/{job_id}" in paths
    assert "/v1/counsel/drafts/{job_id}/refine" in paths
    assert not any("{draft_id}" in path for path in paths), (
        "refine 대상 키는 job_id다 — draft_id는 어떤 응답에도 실리지 않는다(04 §3.9)"
    )
