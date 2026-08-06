"""단건(N=1)에서의 컨텍스트 부재 수렴 — 점검 B-4 잔여.

묶음이 사라져도(99 D ㉛ — 요청 단위 = 문의 1건) **컨텍스트 부재 = `rejected_insufficient`**
수렴은 유효해야 한다. 잡이 `failed`로 새면 화면이 "아직 데이터를 모으는 중이에요"(정직한
거부)가 아니라 **"다시 시도"(에러)** 를 그린다 — 인박스 계약 §4 매핑 표가 깨진다.

부재는 두 층위에서 온다. 둘 다 같은 곳으로 떨어져야 한다.
① **요청 층위** — 인용 가능한 근거(`record_id` 있는 fact)가 0건. 라우터가 LLM 호출 전에 끊는다.
② **그래프 층위** — 컨텍스트 묶음에 그 학생이 없다(`context_missing`). N=1에서도 유효해야 한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers.counsel import reset_counsel_stores, set_counsel_stores
from ai.composition.counsel.stores import ContextBundleRecord, InMemoryContextStore
from ai.contracts.composition import DraftStatus
from ai.contracts.counsel import WireDraftStatus, wire_status_for
from ai.db.store_factory import reset_shared_agent_runtime

_HEADERS = {
    "X-Tenant-Id": "t1",
    "X-Request-Id": "rq-ctx-1",
    "Idempotency-Key": "t1:counsel:ctx",
}

_BASE: dict[str, Any] = {
    "inquiry": {
        "inquiry_ref": "iq_884",
        "topic": "counsel_request",
        "urgency": "normal",
        "received_at": "2026-07-31T14:20:00+09:00",
        "text_masked": "이번 달 어떤지 궁금합니다",
    },
    "student_ref": "st_8f2a",
    "parent_ref": "pa_9c1d",
    "class_ref": "cl_a1",
    "labels": [],
    "dismissed_suggestions": [],
    "context": {
        "snapshot_hash": "sha256:" + "c" * 64,
        "period_label": "2026년 7월",
        "facts": [{"record_id": "le_2041", "summary": "6월 지문 42개·312문항"}],
    },
}


class _DroppingContextStore(InMemoryContextStore):
    """묶음은 있는데 **그 학생이 없다** — N=1에서의 컨텍스트 부재를 재현한다.

    저장은 정상으로 받고 조회에서만 학생을 비운다(해시·테넌트는 그대로라 워커의 앞선
    방어 두 겹을 통과해 **그래프까지 도달**한다 — 이 테스트가 보려는 지점이 거기다).
    실측 결과 이 경우 `student_refs = sorted(bundle.contexts)`가 비어 학생 결과가 **0건**이
    된다(그래프의 `context_missing` 분기는 재개 경로에서 뜬다). 둘 다 같은 곳으로 떨어져야
    한다 — 결과 계약이 있는데 학생 결과가 없다는 건 장애가 아니라 부재다.
    """

    async def get(self, ref: str, *, tenant_id: str) -> ContextBundleRecord | None:
        bundle = await super().get(ref, tenant_id=tenant_id)
        if bundle is None:
            return None
        return bundle.model_copy(update={"contexts": {}})





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


def _result(client: TestClient, body: dict[str, Any]) -> dict[str, Any]:
    post = client.post("/v1/counsel/drafts", json=body, headers=_HEADERS)
    assert post.status_code == 202, post.text
    job_id = post.json()["data"]["job_id"]
    got = client.get(f"/v1/counsel/drafts/{job_id}", headers=_HEADERS)
    assert got.status_code == 200, got.text
    data: dict[str, Any] = got.json()["data"]
    return data


# ── ① 요청 층위 — 인용 가능한 근거 0건 ───────────────────────────


def test_no_citable_evidence_is_rejected_not_failed(client: TestClient) -> None:
    """🔴 근거가 없으면 정직한 거부다 — 에러가 아니다(불변식 4)."""
    body = {**_BASE, "context": {**_BASE["context"], "facts": []}}
    data = _result(client, body)
    assert data["status"] == "succeeded"  # 잡은 성공했다 — 초안이 없을 뿐이다
    assert data["result"]["draft_status"] == "rejected_insufficient"
    assert data["result"]["text"] is None


def test_facts_without_record_id_are_not_citable(client: TestClient) -> None:
    """집계·기준선 파생 fact만 있으면 인용할 게 없다 — 같은 곳으로 떨어진다."""
    body = {
        **_BASE,
        "context": {
            **_BASE["context"],
            "facts": [{"summary": "평소보다 낮은 편"}],  # record_id 없음
        },
    }
    assert _result(client, body)["result"]["draft_status"] == "rejected_insufficient"


def test_rejection_happens_before_the_llm_call(client: TestClient) -> None:
    """게이트 통과 초안을 만들어 놓고 버리지 않는다 — 호출 전에 끊는다(04 §3.9 규약)."""
    from ai.api.routers.counsel import set_counsel_provider
    from ai.composition.counsel.provider import FakeCounselProvider

    provider = FakeCounselProvider()
    set_counsel_provider(provider)
    _result(client, {**_BASE, "context": {**_BASE["context"], "facts": []}})
    assert provider.write_calls == []


# ── ② 그래프 층위 — context_missing이 N=1에서도 거부로 수렴한다 ──


def test_graph_context_missing_converges_to_rejected(client: TestClient) -> None:
    """🔴 잡은 succeeded, 초안은 rejected_insufficient — 화면 매핑이 깨지지 않는다."""
    set_counsel_stores(context_store=_DroppingContextStore())
    data = _result(client, _BASE)
    assert data["status"] == "succeeded"
    assert data["result"]["draft_status"] == "rejected_insufficient"
    assert data["result"]["status_reason"] == "context_missing"


def test_context_missing_maps_to_insufficient_not_failed() -> None:
    """와이어 파생 고정 — `context_missing`이 `failed`로 새면 화면이 '다시 시도'가 된다."""
    wire, reason = wire_status_for(DraftStatus.REJECTED_INSUFFICIENT, "context_missing")
    assert wire is WireDraftStatus.REJECTED_INSUFFICIENT
    assert reason == "context_missing"


def test_response_is_not_an_http_error(client: TestClient) -> None:
    """게이트 거부는 에러가 아니다 — 200이며 error는 null이다(불변식 4)."""
    set_counsel_stores(context_store=_DroppingContextStore())
    post = client.post("/v1/counsel/drafts", json=_BASE, headers=_HEADERS)
    job_id = post.json()["data"]["job_id"]
    got: httpx.Response = client.get(
        f"/v1/counsel/drafts/{job_id}", headers=_HEADERS
    )
    assert got.status_code == 200
    assert got.json()["error"] is None
