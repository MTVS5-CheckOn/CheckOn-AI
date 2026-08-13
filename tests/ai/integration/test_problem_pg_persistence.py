"""PG 기본 문제생성의 부모·슬롯 영속과 재시작 조회 회귀."""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ai.api.app import create_app
from ai.api.routers import problem as problem_router
from ai.contracts.diagnosis import DiagnosisResult
from ai.contracts.problem_generation import ProblemRequest
from ai.db.repositories.run_store import default_llm_call_collector
from ai.db.session import get_engine
from ai.db.settings import get_db_settings
from ai.db.store_factory import build_run_store, reset_shared_agent_runtime
from ai.problem_generation.assembly import problem_runtime_stores
from ai.problem_generation.provider import ProblemProviders

pytestmark = pytest.mark.integration

_FAKES_DIR = Path(__file__).parents[1] / "fakes"
sys.path.insert(0, str(_FAKES_DIR))

from fake_graph_context import FakeGraphContextService  # noqa: E402
from fake_provider import FakeProvider  # noqa: E402
from test_problem_router import (  # noqa: E402
    _body,
    _generated_item_json,
    _solve_result_json,
)


async def _unused_diagnosis(_: ProblemRequest) -> DiagnosisResult:
    raise AssertionError("teacher_manual 요청은 진단을 호출하지 않아야 한다")


def _headers(tenant_id: str) -> dict[str, str]:
    suffix = uuid.uuid4().hex
    return {
        "X-Tenant-Id": tenant_id,
        "X-Request-Id": f"request-{suffix}",
        "Idempotency-Key": f"idem-{suffix}",
    }


def _prepare_pg(*, generator_steps: tuple[str, ...] | None = None) -> None:
    reset_shared_agent_runtime()
    problem_router.reset_problem_router()
    problem_router.set_problem_providers(
        ProblemProviders(
            generator=FakeProvider(
                generator_steps or (_generated_item_json(),), name="pg-generator"
            ),
            verifier=FakeProvider((_solve_result_json(),), name="pg-verifier"),
            has_dedicated_verifier=False,
        )
    )
    problem_router.set_problem_services(
        graph_context=FakeGraphContextService(), diagnosis=_unused_diagnosis
    )
    problem_router.set_problem_stores(problem_runtime_stores())
    problem_router.set_problem_run_store(build_run_store())
    default_llm_call_collector().reset()


async def _persisted_counts(set_id: str, *, database_url: str) -> tuple[int, int]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            parent = await session.scalar(
                text("SELECT count(*) FROM problem_set WHERE id = CAST(:set_id AS uuid)"),
                {"set_id": set_id},
            )
            items = await session.scalar(
                text("SELECT count(*) FROM problem_item WHERE set_id = CAST(:set_id AS uuid)"),
                {"set_id": set_id},
            )
        return int(parent or 0), int(items or 0)
    finally:
        await engine.dispose()


def test_pg_post_persists_items_and_cache_miss_recovers_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = f"tenant-pg-{uuid.uuid4().hex[:10]}"
    headers = _headers(tenant_id)
    monkeypatch.setenv("STORE_BACKEND", "pg")
    monkeypatch.setenv("PG_DRAIN_ENABLED", "false")
    get_db_settings.cache_clear()
    get_engine.cache_clear()
    database_url = get_db_settings().database_url
    _prepare_pg()

    try:
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            posted = client.post("/v1/problems", headers=headers, json=_body())
            assert posted.status_code == 202, posted.text
            job_id = posted.json()["data"]["job_id"]
            first_job = client.get(
                f"/v1/problems/{job_id}", headers={"X-Tenant-Id": tenant_id}
            )
            assert first_job.status_code == 200, first_job.text
            set_id = first_job.json()["data"]["result"]["set_id"]
            assert asyncio.run(
                _persisted_counts(set_id, database_url=database_url)
            ) == (1, 1)

            problem_router._views.clear()  # noqa: SLF001 — 프로세스 재시작 캐시 소실 재현

            restarted_job = client.get(
                f"/v1/problems/{job_id}", headers={"X-Tenant-Id": tenant_id}
            )
            restarted_items = client.get(
                f"/v1/problems/{set_id}/items", headers={"X-Tenant-Id": tenant_id}
            )
            hidden_job = client.get(
                f"/v1/problems/{job_id}", headers={"X-Tenant-Id": "tenant-other"}
            )
            hidden_items = client.get(
                f"/v1/problems/{set_id}/items",
                headers={"X-Tenant-Id": "tenant-other"},
            )

        assert restarted_job.status_code == 200, restarted_job.text
        assert restarted_items.status_code == 200, restarted_items.text
        assert hidden_job.status_code == 404
        assert hidden_items.status_code == 404
    finally:
        monkeypatch.setenv("STORE_BACKEND", "memory")
        get_db_settings.cache_clear()
        get_engine.cache_clear()
        reset_shared_agent_runtime()
        problem_router.reset_problem_router()


def test_pg_dropped_slot_survives_cache_loss_with_reasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = f"tenant-drop-{uuid.uuid4().hex[:10]}"
    headers = _headers(tenant_id)
    monkeypatch.setenv("STORE_BACKEND", "pg")
    monkeypatch.setenv("PG_DRAIN_ENABLED", "false")
    get_db_settings.cache_clear()
    get_engine.cache_clear()
    database_url = get_db_settings().database_url
    _prepare_pg(generator_steps=("not-json", "not-json", "not-json"))

    try:
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            posted = client.post("/v1/problems", headers=headers, json=_body())
            assert posted.status_code == 202, posted.text
            job_id = posted.json()["data"]["job_id"]
            first_job = client.get(
                f"/v1/problems/{job_id}", headers={"X-Tenant-Id": tenant_id}
            )
            set_id = first_job.json()["data"]["result"]["set_id"]
            assert asyncio.run(
                _persisted_counts(set_id, database_url=database_url)
            ) == (1, 1)

            problem_router._views.clear()  # noqa: SLF001 — 프로세스 재시작 캐시 소실 재현
            restarted_job = client.get(
                f"/v1/problems/{job_id}", headers={"X-Tenant-Id": tenant_id}
            )
            restarted_items = client.get(
                f"/v1/problems/{set_id}/items", headers={"X-Tenant-Id": tenant_id}
            )

        assert restarted_job.status_code == 200, restarted_job.text
        result = restarted_job.json()["data"]["result"]
        assert result["items"][0]["status"] == "dropped"
        assert result["dropped_reasons"] == ["generation_exhausted"]
        assert restarted_items.status_code == 200, restarted_items.text
        assert restarted_items.json()["data"]["status_counts"]["dropped"] == 1
    finally:
        monkeypatch.setenv("STORE_BACKEND", "memory")
        get_db_settings.cache_clear()
        get_engine.cache_clear()
        reset_shared_agent_runtime()
        problem_router.reset_problem_router()
