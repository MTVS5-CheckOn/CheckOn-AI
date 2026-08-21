"""AI–BE 확정 지시서의 구현 영향 사실을 코드·fixture와 대조한다."""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
from pathlib import Path
from typing import Any

from ai.api.routers import problem as problem_router
from ai.contracts.agents import TERMINAL_PHASES, JobPhase
from ai.contracts.problem_generation import Choice, TargetSource
from ai.contracts.taxonomy import AreaTag
from ai.problem_generation.domain.policy import (
    SUPPORTED_AREAS,
    supports_source_procurement,
)

_REPO_ROOT = Path(__file__).parents[3]
_MEETING_SPEC = _REPO_ROOT / "docs/part_b/AI_BE_COMMUNICATION_MEETING_SPEC.md"
_DETAIL_SPEC = _REPO_ROOT / "docs/part_b/PROBLEM_GENERATION_AI_BE_INTEGRATION_SPEC.md"
_POST_FIXTURE = (
    _REPO_ROOT / "tests/ai/contract/fixtures/http/post_problems.202.json"
)
_SNAPSHOT_START = "<!-- ai-be-contract-snapshot:start -->"
_SNAPSHOT_END = "<!-- ai-be-contract-snapshot:end -->"


def _snapshot() -> dict[str, Any]:
    text = _MEETING_SPEC.read_text(encoding="utf-8")
    block = text.split(_SNAPSHOT_START, maxsplit=1)[1].split(
        _SNAPSHOT_END, maxsplit=1
    )[0]
    payload = block.split("```json", maxsplit=1)[1].split("```", maxsplit=1)[0]
    parsed = json.loads(payload)
    assert isinstance(parsed, dict)
    return parsed


def _called_names(function: Any) -> set[str]:  # noqa: ANN401 — inspect 대상 callable
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    names: set[str] = set()
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        if isinstance(call.func, ast.Name):
            names.add(call.func.id)
        elif isinstance(call.func, ast.Attribute):
            names.add(call.func.attr)
    return names


def test_snapshot_matches_worker_phases_and_both_documents_list_them() -> None:
    snapshot = _snapshot()
    all_phases = {phase.value for phase in JobPhase}
    terminal_phases = {phase.value for phase in TERMINAL_PHASES}

    assert set(snapshot["job_phases"]) == all_phases
    assert set(snapshot["terminal_phases"]) == terminal_phases

    detail = _DETAIL_SPEC.read_text(encoding="utf-8")
    for phase in JobPhase:
        assert f"| `{phase.value}` |" in detail


def test_snapshot_matches_supported_areas_and_source_shapes() -> None:
    snapshot = _snapshot()
    assert set(snapshot["supported_areas"]) == {
        area.value for area in SUPPORTED_AREAS
    }
    assert set(snapshot["supported_areas"]) == {area.value for area in AreaTag}

    shapes = snapshot["source_shapes"]
    for area in AreaTag:
        shape = shapes[area.value]
        expected = (
            shape == "passage",
            shape == "work_selection",
        )
        accepted = {
            (has_passage, has_work_selection)
            for has_passage in (False, True)
            for has_work_selection in (False, True)
            if supports_source_procurement(
                area_tag=area,
                has_passage_request=has_passage,
                has_work_selection=has_work_selection,
            )
        }
        assert accepted == {expected}


def test_snapshot_matches_enqueue_only_post_fixture_and_router_wiring() -> None:
    snapshot = _snapshot()
    fixture = json.loads(_POST_FIXTURE.read_text(encoding="utf-8"))
    called = _called_names(problem_router._generate)  # noqa: SLF001

    assert snapshot["problem_post"] == {
        "http_status": 202,
        "initial_status": fixture["data"]["status"],
        "execution_mode": "background_drain",
    }
    assert "_notify_problem_drain" in called
    assert "_run_next_for_tenant" not in called


def test_snapshot_keeps_feedback_and_current_unsupported_boundary_visible() -> None:
    snapshot = _snapshot()

    assert snapshot["answer_number_base"] == 1
    assert snapshot["misconception_feedback_field"] == (
        "choices[].misconception_tag"
    )
    assert "misconception_tag" in Choice.model_fields
    assert snapshot["unsupported_target_sources"] == [
        TargetSource.WEAKNESS_AUTO.value
    ]
    assert snapshot["supported_revision_kinds"] == ["ai_refine"]
    assert snapshot["retry_after_is_advisory"] is True
    assert snapshot["startup_queued_recovery"] == "postgres_sweep"
    assert "pending_problem_tenants" in _called_names(
        problem_router._start_problem_drain  # noqa: SLF001
    )
