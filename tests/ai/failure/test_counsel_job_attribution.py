"""요청이 **자기 잡의 결과만** 받는다 — 큐에 남의 잡이 남아 있을 때.

라우터는 `run_next(tenant_id=…)`로 잡을 돌리는데, 그건 `worker_kind + tenant_id`로만
lease한다 — **job_id를 지정해 집을 수 없다.** 같은 테넌트에 queued 잡이 남아 있으면
이 요청이 남의 잡을 실행하고, 결과를 `ran.result_ref`에서 꺼내면 **다른 학생의 초안 본문**이
이 요청의 `job_id`·`citations`와 함께 나간다.

🔴 **동시성이 필요 없다.** 잔여 잡 1건이면 순차 실행에서도 난다 — 이전 요청의 잔여,
프로세스 재기동 후 남은 큐, 다른 요청이 만든 잡 전부 같은 조건이다.

⚠ **인메모리 백엔드에서는 재현되지 않는다** — `build_agent_job_store()`가 호출마다 새
`InMemoryJobStore`를 만들어 요청끼리 큐를 공유하지 않기 때문이다(그 사실 자체가 별건 ·
99). 프로덕션(PG)은 큐를 공유하므로 여기서는 **공유 저장소를 주입해 PG 조건을 재현**한다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ai.agents.job_store import InMemoryJobStore
from ai.agents.supervisor import Supervisor, system_utc_now
from ai.api.app import create_app
from ai.api.routers.counsel import (
    reset_counsel_stores,
    set_counsel_stores,
)
from ai.composition.counsel.enqueue import CounselPackEnqueuer
from ai.composition.counsel.labels import snapshot_from_labels
from ai.composition.counsel.stores import InMemoryContextStore
from ai.contracts.composition import DraftContext, EvidenceFact
from ai.db.store_factory import reset_shared_agent_runtime

_HEADERS = {
    "X-Tenant-Id": "t1",
    "X-Request-Id": "rq-attr-1",
    "Idempotency-Key": "t1:counsel:attr",
}

_BODY: dict[str, Any] = {
    "inquiry": {
        "inquiry_ref": "iq_900",
        "topic": "counsel_request",
        "urgency": "normal",
        "received_at": "2026-08-06T14:20:00+09:00",
        "text_masked": "이번 달 어떤지 궁금합니다",
    },
    "student_ref": "st_mine",
    "parent_ref": "pa_mine",
    "class_ref": "cl_a1",
    "labels": [],
    "dismissed_suggestions": [],
    "context": {
        "snapshot_hash": "sha256:" + "d" * 64,
        "period_label": "2026년 7월",
        "facts": [{"record_id": "le_2041", "summary": "6월 지문 42개"}],
    },
}

#: 🔴 이 문장이 응답에 뜨면 **남의 초안이 나간 것**이다. 게이트를 통과하도록 숫자·금칙어
#: 없이 만든다 — 게이트에 걸려 버리면 누출이 아니라 실패로 보여 테스트가 헛돈다.
_OTHER_STUDENT_TEXT = "다른 학생의 상담 초안 본문입니다"


def _other_context() -> DraftContext:
    snapshot, _applied = snapshot_from_labels(())
    return DraftContext(
        student_ref="st_other",
        guardian_ref="pa_other",
        label_snapshot=snapshot,
        facts=(EvidenceFact(label="근거", value="7월 지문", record_id="le_9999"),),
        evidence_summaries=(),
        period_label="2026년 7월",
        fallback_text=_OTHER_STUDENT_TEXT,
    )


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    reset_shared_agent_runtime()  # A·B 공용 잡 원장(99 ㊒)
    reset_counsel_stores()
    yield
    reset_shared_agent_runtime()  # A·B 공용 잡 원장(99 ㊒)
    reset_counsel_stores()


@pytest.fixture
def shared_queue(monkeypatch: pytest.MonkeyPatch) -> InMemoryJobStore:
    """요청끼리 큐를 공유하게 만든다 — PG 백엔드의 조건이다."""
    store = InMemoryJobStore()
    monkeypatch.setattr(
        "ai.api.routers.counsel.build_agent_job_store", lambda: store
    )
    return store


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def _leave_stale_job(store: InMemoryJobStore) -> None:
    """다른 학생의 잡을 큐에 **먼저** 넣어 둔다(FIFO라 이게 먼저 lease된다)."""
    contexts = InMemoryContextStore()
    set_counsel_stores(context_store=contexts)
    supervisor = Supervisor(
        store=store,
        lease_duration=timedelta(seconds=60),
        priority_aging_interval=timedelta(seconds=60),
        clock=system_utc_now,
    )
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        CounselPackEnqueuer(
            supervisor=supervisor, context_store=contexts
        ).enqueue(
            tenant_id="t1",
            class_ref="cl_other",
            contexts={"st_other": _other_context()},
        )
    )


def test_request_does_not_receive_another_students_draft(
    client: TestClient, shared_queue: InMemoryJobStore
) -> None:
    """🔴 남의 초안 본문이 이 요청의 job_id로 나가면 안 된다."""
    _leave_stale_job(shared_queue)

    post = client.post("/v1/counsel/drafts", json=_BODY, headers=_HEADERS)
    assert post.status_code == 202, post.text
    job_id = post.json()["data"]["job_id"]

    got = client.get(f"/v1/counsel/drafts/{job_id}", headers=_HEADERS)
    assert got.status_code == 200, got.text
    result = got.json()["data"]["result"]

    text = (result or {}).get("text") or ""
    assert _OTHER_STUDENT_TEXT not in text, (
        f"다른 학생의 초안이 이 요청으로 나갔다: {text!r}"
    )


def test_unrun_job_is_not_reported_as_llm_failed(
    client: TestClient, shared_queue: InMemoryJobStore
) -> None:
    """🔴 안 돌아간 잡을 장애로 보고하지 않는다.

    `status="queued"` + `draft_status="llm_failed"`는 **모순 조합**이다 — 잡은 살아 있는데
    화면은 "다시 시도"를 그리고, 강사가 누르면 초안이 2개 생긴다.

    ⚠ *"터미널이면 넘어간다"* 같은 조기 return을 두지 않는다 — 잔여 잡이 먼저 lease되는
    상황을 만들어 놓았으므로 **내 잡은 반드시 `queued`로 남는다.** 조기 return을 두면
    귀속이 어긋난 채로도 통과해 테스트가 헛돈다.
    """
    _leave_stale_job(shared_queue)

    post = client.post("/v1/counsel/drafts", json=_BODY, headers=_HEADERS)
    job_id = post.json()["data"]["job_id"]
    data = client.get(f"/v1/counsel/drafts/{job_id}", headers=_HEADERS).json()["data"]

    assert data["status"] == "queued", (
        f"잔여 잡이 먼저 lease됐으므로 내 잡은 queued여야 한다: {data['status']}"
    )
    assert data["result"] is None, (
        f"미실행 잡({data['status']})인데 result가 실렸다: {data['result']}"
    )
