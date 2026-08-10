"""AGENT_RUN.run_id 물리 FK 제거의 ORM·ERD·마이그레이션 구조 대조."""

from __future__ import annotations

import ast
from pathlib import Path

from erd_parser import ERD_PATH, parse_erd

from ai.db.models import AgentRun, Base

_MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "ai"
    / "db"
    / "migrations"
    / "versions"
    / "0009_drop_agent_run_ai_fk.py"
)
_CONSTRAINT = "fk_agent_run_run_id_ai_run"


def test_agent_run_run_id_is_not_a_physical_ai_run_fk() -> None:
    """ORM과 ERD 모두 run_id를 NOT NULL 실행 신원으로 두고 물리 FK로 표시하지 않는다."""
    run_id = AgentRun.__table__.columns["run_id"]
    erd_run_id = parse_erd(ERD_PATH)["agent_run"].columns["run_id"]
    erd_source = ERD_PATH.read_text(encoding="utf-8")

    assert run_id.nullable is False
    assert not run_id.foreign_keys
    assert erd_run_id.fk is False
    assert (
        'uuid run_id "WorkerJob.execution_id — 잡 실행 신원, '
        'AI_RUN이 존재하면 같은 ID로 논리 결합"'
    ) in erd_source


def test_only_agent_run_ai_run_fk_is_removed_from_orm() -> None:
    """산출물 축의 나머지 AI_RUN FK 다섯은 그대로 유지한다."""
    ai_run_fks = {
        (table.name, column.name)
        for table in Base.metadata.tables.values()
        for column in table.columns
        for foreign_key in column.foreign_keys
        if foreign_key.target_fullname == "ai_run.execution_id"
    }
    assert ai_run_fks == {
        ("signal", "run_id"),
        ("draft", "run_id"),
        ("weakness_map", "run_id"),
        ("llm_call", "run_id"),
        ("problem_set", "run_id"),
    }


def test_migration_drops_and_restores_the_exact_constraint() -> None:
    """0009는 지정 FK 하나만 내리고 downgrade에서 같은 이름과 대상을 복원한다."""
    tree = ast.parse(_MIGRATION.read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    dropped = [call for call in calls if _call_name(call) == "op.drop_constraint"]
    restored = [call for call in calls if _call_name(call) == "op.create_foreign_key"]

    assert len(dropped) == 1
    assert len(restored) == 1
    assert _literal_args(dropped[0]) == (_CONSTRAINT, "agent_run")
    assert {
        keyword.arg: ast.literal_eval(keyword.value) for keyword in dropped[0].keywords
    } == {"type_": "foreignkey"}
    assert _literal_args(restored[0]) == (
        _CONSTRAINT,
        "agent_run",
        "ai_run",
        ["run_id"],
        ["execution_id"],
    )


def _call_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
        return f"{call.func.value.id}.{call.func.attr}"
    return ""


def _literal_args(call: ast.Call) -> tuple[object, ...]:
    return tuple(
        _CONSTRAINT
        if isinstance(argument, ast.Name) and argument.id == "_CONSTRAINT"
        else ast.literal_eval(argument)
        for argument in call.args
    )
