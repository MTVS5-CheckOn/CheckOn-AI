"""Import → mapping_probe 연결 — needs_probing 시 WorkerJob enqueue + status=probing (§6).

enqueuer 미배선(기본)이면 preview_ready 유지(기존 동작 무변). 배선하면 probing으로 전이하고
슈퍼바이저 큐에 mapping_probe.resolve 잡이 들어간다. 실 실행은 워커가 별도로 한다.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from io import BytesIO
from typing import Any

import pandas as pd  # type: ignore[import-untyped]
from fastapi.testclient import TestClient

from ai.agents.job_store import InMemoryJobStore
from ai.agents.supervisor import Supervisor
from ai.api.app import create_app
from ai.api.routers.imports import reset_import_stores, set_import_stores
from ai.contracts.agents import WorkerKind
from ai.import_mapping.probe.enqueue import ProbeEnqueuer
from ai.import_mapping.probe.stores import InMemoryProfileStore

_HEADERS = {"X-Tenant-Id": "t1", "X-Request-Id": "r1", "Idempotency-Key": "t1:probe"}

# 저신뢰 컬럼(점수→score 0.55<0.9) → needs_probing. 기동 조건은 저신뢰 축뿐이다(§3.3).
_ROSTER_LOWCONF = BytesIO()
pd.DataFrame(
    {
        "원생명": ["김"],
        "반": ["A1"],
        "등원일": ["2026-03-02"],
        "상태": ["재원"],
        "동의": ["동의"],
        "점수": [88],
    }
).to_excel(_ROSTER_LOWCONF, index=False)
_BYTES = _ROSTER_LOWCONF.getvalue()


class _Loader:
    def load(self, source_url: str) -> bytes:
        return _BYTES


def _post(client: TestClient) -> dict[str, Any]:
    resp = client.post(
        "/v1/imports",
        json={"source_url": "s3://r.xlsx", "filename": "r.xlsx"},
        headers=_HEADERS,
    )
    assert resp.status_code == 202
    data: dict[str, Any] = resp.json()["data"]
    return data


def test_default_no_enqueuer_stays_preview_ready() -> None:
    """enqueuer 미배선 → probing 안 하고 preview_ready(기존 동작 무변)."""
    reset_import_stores()
    set_import_stores(source_loader=_Loader())
    try:
        data = _post(TestClient(create_app()))
        assert data["status"] == "preview_ready"
    finally:
        reset_import_stores()


def test_needs_probing_enqueues_worker_job_and_transitions() -> None:
    reset_import_stores()
    supervisor = Supervisor(
        store=InMemoryJobStore(),
        lease_duration=timedelta(minutes=5),
        priority_aging_interval=timedelta(minutes=1),
    )
    profiles = InMemoryProfileStore()
    set_import_stores(
        source_loader=_Loader(),
        probe_enqueuer=ProbeEnqueuer(supervisor=supervisor, profile_store=profiles),
    )
    try:
        data = _post(TestClient(create_app()))
        assert data["status"] == "probing"  # §6 전이

        # 슈퍼바이저 큐에 mapping_probe 잡이 들어갔다 — payload_ref는 profile:// URI.
        leased = asyncio.run(
            supervisor.lease_next(
                tenant_id="t1", worker_kind=WorkerKind.MAPPING_PROBE, lease_owner="w1"
            )
        )
        assert leased is not None
        assert leased.operation.value == "mapping_probe.resolve"
        assert leased.payload_ref.startswith("profile://")
    finally:
        reset_import_stores()


# ── (지시서 68) 응답이 **실제 실행 ID**를 가리키는가 (99 ㉾ · ㊯) ──


def _envelope(client: TestClient) -> dict[str, Any]:
    resp = client.post(
        "/v1/imports",
        json={"source_url": "s3://r.xlsx", "filename": "r.xlsx"},
        headers=_HEADERS,
    )
    assert resp.status_code == 202, resp.text
    body: dict[str, Any] = resp.json()
    return body


def test_a_probing_response_points_at_the_probe_execution() -> None:
    """🔴 **조사를 띄웠으면 응답이 그 실행을 가리킨다** — 종전엔 `ImportJob.job_id`였다.

    ⚠ 그 값은 **어떤 `AI_RUN` 행도 가리키지 않았다**(04 §2.2 「상관 ID」 부류). 조사 워커가
    원장을 쓰게 된 지금 그대로 두면 **원장 행은 생기는데 아무도 그 행을 못 찾는다.**
    ⚠ **POST·GET·멱등 재응답이 같아야** 한다 — 조회 방식에 따라 다른 ID를 말하면 안 된다.
    """
    reset_import_stores()
    supervisor = Supervisor(
        store=InMemoryJobStore(),
        lease_duration=timedelta(minutes=5),
        priority_aging_interval=timedelta(minutes=1),
    )
    profiles = InMemoryProfileStore()
    set_import_stores(
        source_loader=_Loader(),
        probe_enqueuer=ProbeEnqueuer(supervisor=supervisor, profile_store=profiles),
    )
    try:
        client = TestClient(create_app())
        first = _envelope(client)
        job_id = first["data"]["job_id"]
        execution_id = first["meta"]["execution_id"]
        assert first["data"]["status"] == "probing"

        leased = asyncio.run(
            supervisor.lease_next(
                tenant_id="t1", worker_kind=WorkerKind.MAPPING_PROBE, lease_owner="w1"
            )
        )
        assert leased is not None
        #: 🔴 **값 대조** — 「UUID처럼 생겼다」가 아니라 **그 잡의 실행 신원**이어야 한다.
        assert execution_id == str(leased.execution_id), (
            f"응답이 조사 실행을 안 가리킨다: {execution_id} != {leased.execution_id}"
        )
        assert execution_id != job_id, (
            "여전히 ImportJob.job_id를 싣고 있다 — 상관 ID 그대로다"
        )

        #: GET·멱등 재응답도 같은 선택을 지난다.
        got = client.get(f"/v1/imports/{job_id}", headers=_HEADERS)
        assert got.status_code == 200, got.text
        assert got.json()["meta"]["execution_id"] == execution_id, "GET이 다른 ID를 말한다"
        assert _envelope(client)["meta"]["execution_id"] == execution_id, (
            "멱등 재응답이 다른 ID를 말한다"
        )
    finally:
        reset_import_stores()


def test_a_path_without_a_probe_keeps_the_correlation_id() -> None:
    """🔴 **조사를 안 띄운 경로는 상관 ID 그대로다** — 없는 실행을 지어내지 않는다.

    ⚠ `preview_ready`는 워커도 LLM도 안 타므로 **원장에 행이 없는 것이 정상**이다.
    """
    reset_import_stores()
    set_import_stores(source_loader=_Loader())
    try:
        client = TestClient(create_app())
        body = _envelope(client)
        assert body["data"]["status"] == "preview_ready"
        assert body["meta"]["execution_id"] == body["data"]["job_id"], (
            "조사가 없는데 다른 ID가 실렸다 — 가리킬 실행이 없다"
        )
    finally:
        reset_import_stores()


def test_an_unreadable_file_keeps_the_correlation_id() -> None:
    """실패 경로도 상관 ID 유지 — 조사 잡이 생성되지 않았다."""

    class _Broken:
        def load(self, source_url: str) -> bytes:
            raise NotImplementedError("스토리지 미배선")

    reset_import_stores()
    set_import_stores(source_loader=_Broken())
    try:
        body = _envelope(TestClient(create_app()))
        assert body["data"]["status"] == "failed"
        assert body["meta"]["execution_id"] == body["data"]["job_id"]
    finally:
        reset_import_stores()
