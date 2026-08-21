"""PG 요청·결과 저장소의 backend 조립 계약."""

from __future__ import annotations

from ai.db.repositories.problem_runtime_store import (
    PgProblemRequestStore,
    PgProblemResultStore,
)
from ai.db.settings import DbSettings
from ai.problem_generation.assembly import problem_runtime_stores


def test_pg_backend_builds_persistent_request_and_result_stores() -> None:
    stores = problem_runtime_stores(settings=DbSettings(_env_file=None, store_backend="pg"))

    assert isinstance(stores.requests, PgProblemRequestStore)
    assert isinstance(stores.results, PgProblemResultStore)


def test_memory_backend_keeps_in_process_request_and_result_stores() -> None:
    stores = problem_runtime_stores(settings=DbSettings(_env_file=None, store_backend="memory"))

    assert type(stores.requests).__name__ == "_InMemoryProblemRequestStore"
    assert type(stores.results).__name__ == "_InMemoryProblemResultStore"
