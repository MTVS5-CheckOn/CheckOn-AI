"""운영 probe 라우터의 독립 계약 — app.py 등록 전에도 표면을 고정한다."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from ai.api.envelope import versions_dict
from ai.api.routers import ops
from ai.api.routers.detect import detection_versions
from ai.api.routers.diagnosis import diagnosis_versions
from ai.api.routers.problem import problem_failure_versions
from ai.api.version_scope import FALLBACK_VERSIONS, resolve_versions
from ai.composition.classify.classifier import classify_versions
from ai.composition.counsel.versions import counsel_versions
from ai.import_mapping.versions import import_versions


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
    assert response.json()["meta"]["versions"] == versions_dict(FALLBACK_VERSIONS)


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
            "imports": versions_dict(import_versions()),
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
        resolve_versions(scope.prefix, ops.VERSION_SCOPE) == FALLBACK_VERSIONS
        for scope in ops.VERSION_SCOPE
    )
    assert resolve_versions("/v1/problems", ops.VERSION_SCOPE) == FALLBACK_VERSIONS
