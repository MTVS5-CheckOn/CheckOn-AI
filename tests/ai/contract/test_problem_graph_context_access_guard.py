"""프로덕션 PG 조립이 실 GraphContext만 선택하는지 고정한다."""

from __future__ import annotations

import ast
from pathlib import Path

from ai.api.routers import problem as problem_router
from ai.problem_generation.infrastructure.graph_context import (
    AreaDelegatingGraphContextService,
)

_REAL_LLM_SMOKE_PATH = (
    Path(__file__).resolve().parents[1] / "integration" / "test_pg_real_llm_smoke.py"
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


def test_real_llm_smoke_uses_production_graph_context_wiring() -> None:
    """오프라인 가드가 없으면 실 LLM 스모크 배선이 운영과 조용히 갈릴 수 있다."""

    tree = ast.parse(_REAL_LLM_SMOKE_PATH.read_text(encoding="utf-8"))
    functions = {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    graph_context = functions.get("_graph_context")

    assert graph_context is not None, "실 LLM 스모크의 _graph_context 함수가 없다"
    called_names = {
        node.func.id
        for node in ast.walk(graph_context)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    referenced_names = {
        node.id for node in ast.walk(graph_context) if isinstance(node, ast.Name)
    }

    assert called_names == {"AreaDelegatingGraphContextService"}, (
        "운영은 AreaDelegatingGraphContextService인데 실 LLM 스모크가 다른 "
        f"GraphContext를 조립한다: {sorted(called_names)}"
    )
    assert "FakeGraphContextService" not in referenced_names, (
        "운영은 AreaDelegatingGraphContextService인데 실 LLM 스모크는 Fake "
        "GraphContext를 사용한다"
    )
    assert not any(isinstance(node, ast.If) for node in ast.walk(graph_context)), (
        "실 LLM 스모크가 영역별 GraphContext 분기로 운영 배선과 갈렸다"
    )
