"""ops 직접 응답이 자기 버전을 유지하는 계약.

ops는 app.py 예외 핸들러를 지나지 않고 라우터가 직접 error_envelope를 만든다. 따라서
99 ㊓의 '남의 버전을 단다' 결함은 app.py가 아니라 ops.py 안에서 재현될 수 있다.
ops.py가 capability 버전 함수 6개를 import하므로 실수 여지가 실재하며 이 파일이 그 자리를
지킨다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from ai.api.app import create_app
from ai.api.routers import ops


class _Session:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error

    async def execute(self, _statement: object) -> None:
        if self.error is not None:
            raise self.error


def _sessionmaker(session: _Session) -> Any:  # noqa: ANN401
    @asynccontextmanager
    async def context() -> AsyncIterator[AsyncSession]:
        yield session  # type: ignore[misc]

    return context


def _engine(response: Any) -> str:  # noqa: ANN401
    engine: str = response.json()["meta"]["versions"]["engine"]
    return engine


def test_ops_success_responses_carry_the_ops_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops, "get_sessionmaker", lambda: _sessionmaker(_Session()))
    expected = ops.ops_versions().engine_version

    with TestClient(create_app()) as client:
        responses = (
            client.get("/v1/health"),
            client.get("/v1/ready"),
            client.get("/v1/meta/versions"),
        )

    assert all(response.status_code == 200 for response in responses)
    assert [_engine(response) for response in responses] == [expected, expected, expected]


def test_ready_failure_response_carries_the_ops_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ops,
        "get_sessionmaker",
        lambda: _sessionmaker(_Session(error=RuntimeError("database unavailable"))),
    )

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        response = client.get("/v1/ready")

    assert response.status_code == 503
    assert _engine(response) == ops.ops_versions().engine_version
