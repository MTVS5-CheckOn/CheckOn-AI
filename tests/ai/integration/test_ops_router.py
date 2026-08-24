"""운영 probe 라우터의 독립 계약 — app.py 등록 전에도 표면을 고정한다."""

from __future__ import annotations

import importlib
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from ai.api.app import ROUTER_VERSION_SCOPES, create_app
from ai.api.envelope import success_envelope, versions_dict
from ai.api.routers import ops
from ai.api.routers.detect import detection_versions
from ai.api.routers.diagnosis import diagnosis_versions
from ai.api.routers.problem import problem_failure_versions
from ai.api.version_scope import FALLBACK_VERSIONS, resolve_versions
from ai.composition.classify.classifier import classify_versions
from ai.composition.counsel.versions import counsel_versions
from ai.composition.labels.versions import labels_versions
from ai.db import session as db_session


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(ops.router)

    @app.middleware("http")
    async def echo_request_id(request: Request, call_next: Any) -> Any:  # noqa: ANN401
        response = await call_next(request)
        if request_id := request.headers.get("X-Request-Id"):
            response.headers["X-Request-Id"] = request_id
        return response

    return app


class _Session:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.statements: list[str] = []

    async def execute(self, statement: object) -> None:
        self.statements.append(str(statement))
        if self.error is not None:
            raise self.error


def _sessionmaker(session: _Session) -> Any:  # noqa: ANN401
    @asynccontextmanager
    async def context() -> AsyncIterator[AsyncSession]:
        yield session  # type: ignore[misc]

    return context


def test_health_is_liveness_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ops,
        "get_sessionmaker",
        lambda: (_ for _ in ()).throw(AssertionError("health가 DB를 열었다")),
    )

    with TestClient(_app()) as client:
        response = client.get("/v1/health", headers={"X-Request-Id": "req-health"})

    assert response.status_code == 200
    assert response.headers["X-Request-Id"] == "req-health"
    assert response.json()["data"] == {"status": "alive"}
    assert response.json()["meta"]["execution_id"] is None
    assert response.json()["meta"]["versions"] == versions_dict(ops.ops_versions())
    assert response.json()["meta"]["versions"]["engine"] == "ops-0.1"


def test_ops_success_envelope_keeps_the_common_key_shape() -> None:
    common = success_envelope(
        data={"status": "alive"},
        execution_id="execution-placeholder",
        versions=ops.ops_versions(),
    )
    operations = ops._success({"status": "alive"})

    assert set(operations) == set(common) == {"data", "error", "meta"}
    assert set(operations["meta"]) == set(common["meta"]) == {
        "execution_id",
        "versions",
    }


def test_importing_ops_does_not_create_a_database_sessionmaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def counted_sessionmaker() -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(db_session, "get_sessionmaker", counted_sessionmaker)
    monkeypatch.delitem(sys.modules, "ai.api.routers.ops")

    importlib.import_module("ai.api.routers.ops")

    assert calls == 0


def test_ready_executes_one_bounded_database_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _Session()
    monkeypatch.setattr(ops, "get_sessionmaker", lambda: _sessionmaker(session))

    with TestClient(_app()) as client:
        response = client.get("/v1/ready")

    assert response.status_code == 200
    assert response.json()["data"] == {"status": "ready"}
    assert session.statements == ["SELECT 1"]


def test_ready_failure_is_503_without_driver_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "postgresql://user:password@db.internal/checkon"
    monkeypatch.setattr(
        ops,
        "get_sessionmaker",
        lambda: _sessionmaker(_Session(error=RuntimeError(secret))),
    )

    with TestClient(_app(), raise_server_exceptions=False) as client:
        response = client.get("/v1/ready")

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "SERVICE_NOT_READY",
        "message": "서비스 준비 상태를 확인할 수 없습니다.",
        "detail": {"unavailable_components": ["database"]},
    }
    assert secret not in response.text
    assert "RuntimeError" not in response.text


def test_meta_versions_uses_capability_factories_without_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ops,
        "get_sessionmaker",
        lambda: (_ for _ in ()).throw(AssertionError("versions가 DB를 열었다")),
    )

    with TestClient(_app()) as client:
        response = client.get("/v1/meta/versions")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "app_version": version("checkon-ai"),
        "capabilities": {
            "classify": versions_dict(classify_versions()),
            "counsel": versions_dict(counsel_versions()),
            "detect": versions_dict(detection_versions()),
            "diagnosis": versions_dict(diagnosis_versions()),
            # 🔴 **손으로 옮겨 적은 목록이다**(`ops.py` 의 `capabilities` 사본).
            #   №72 가 `ROUTER_VERSION_SCOPES` 에서 **파생시키려다 접었다** — 실측으로
            #   뜻이 갈렸다(99 #210): scope 는 «실패 응답을 무엇으로 채우나»(경로별 11),
            #   여기는 «운영자가 조회하는 capability»(중복 뺀 축 6). 1:1 이 아니다.
            #   ⇒ 사본은 남기고, «둘 다 갱신됐나» 는 별도 가드가 문다
            #   (`tests/ai/contract/test_meta_versions_cover_every_scope.py`).
            "labels": versions_dict(labels_versions()),
            "problem_generation": versions_dict(problem_failure_versions()),
        },
    }
    assert response.json()["meta"]["execution_id"] is None


def test_ops_version_scopes_are_exact_and_do_not_capture_v1() -> None:
    assert {scope.prefix for scope in ops.VERSION_SCOPE} == {
        "/v1/health",
        "/v1/ready",
        "/v1/meta/versions",
    }
    assert all(
        resolve_versions(scope.prefix, ops.VERSION_SCOPE) == ops.ops_versions()
        for scope in ops.VERSION_SCOPE
    )
    assert ops.ops_versions().engine_version == "ops-0.1"
    assert FALLBACK_VERSIONS.engine_version == "app-0.1"
    assert resolve_versions("/v1/health", ()) == FALLBACK_VERSIONS
    assert resolve_versions("/v1/health", ()) != ops.ops_versions()
    assert resolve_versions("/v1/problems", ops.VERSION_SCOPE) == FALLBACK_VERSIONS


def test_registered_app_exposes_ops_paths_and_keeps_version_scopes_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session()
    monkeypatch.setattr(ops, "get_sessionmaker", lambda: _sessionmaker(session))
    app = create_app()
    paths = app.openapi()["paths"]

    assert {"/v1/health", "/v1/ready", "/v1/meta/versions"} <= set(paths)

    with TestClient(app) as client:
        health = client.get("/v1/health")
        ready = client.get("/v1/ready")
        versions = client.get("/v1/meta/versions")
        problem_failure = client.post("/v1/problems", json={})
        diagnosis_failure = client.post("/v1/diagnosis", json={})

    for response in (health, ready, versions):
        assert response.json()["meta"]["versions"]["engine"] == "ops-0.1"
    assert problem_failure.json()["meta"]["versions"] == versions_dict(
        problem_failure_versions()
    )
    assert diagnosis_failure.json()["meta"]["versions"] == versions_dict(
        diagnosis_versions()
    )
    assert resolve_versions("/v1/nonexistent", ROUTER_VERSION_SCOPES) == FALLBACK_VERSIONS


def test_registered_app_requires_all_three_ops_version_scopes() -> None:
    registered = {
        scope.prefix: resolve_versions(scope.prefix, ROUTER_VERSION_SCOPES)
        for scope in ops.VERSION_SCOPE
    }

    assert set(registered) == {scope.prefix for scope in ops.VERSION_SCOPE}
    assert all(versions.engine_version == "ops-0.1" for versions in registered.values())
