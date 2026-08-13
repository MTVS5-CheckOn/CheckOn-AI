"""프로덕션 PG 조립이 실 GraphContext만 선택하는지 고정한다."""

from __future__ import annotations

from ai.api.routers import problem as problem_router
from ai.problem_generation.infrastructure.graph_context import (
    AreaDelegatingGraphContextService,
)


def test_problem_service_bootstrap_is_idempotent_and_never_uses_fake() -> None:
    problem_router.reset_problem_router()

    problem_router.bootstrap_problem_services()
    first, _ = problem_router.require_problem_services()
    problem_router.bootstrap_problem_services()
    second, _ = problem_router.require_problem_services()

    assert first is second
    assert isinstance(first, AreaDelegatingGraphContextService)
    assert "fake_graph_context" not in type(first).__module__
