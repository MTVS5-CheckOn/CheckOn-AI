"""counsel 저장소 **배선** — 기동 초기값과 reset이 같은 팩토리를 탄다 (㉻ · 지시서 73 §5).

🔴 **㉻의 진짜 원인은 저장소 구현이 아니라 배선이었다.** `counsel.py`의 `_context_store`·
`_draft_store`는 초기값도 reset도 `InMemory…()` **리터럴**이라 `STORE_BACKEND=pg`가
**아무 영향을 못 줬다**(#37의 스텝 싱크와 같은 형태). 구현을 만들어도 그 자리를 안 고치면
결손은 그대로 남는다.

⚠ **자리가 둘이라 한쪽만 고쳐진다**(99 #02) — 모듈 초기값과 `reset_counsel_stores()`.
그러면 **기동 직후와 테스트 리셋 뒤의 구현이 갈린다.** 문자열 grep은 자기 docstring을
잡으므로(로그 85) **AST로** 본다.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

import pytest

from ai.composition.counsel.stores import (
    InMemoryContextStore,
    InMemoryDraftResultStore,
)
from ai.db.repositories.counsel_context_store import PgContextStore
from ai.db.repositories.counsel_draft_store import PgDraftResultStore
from ai.db.settings import DbSettings
from ai.db.store_factory import build_context_store, build_draft_result_store

_ROUTER: Final = (
    Path(__file__).resolve().parents[4] / "src" / "ai" / "api" / "routers" / "counsel.py"
)

#: 🔴 **배선을 봐야 하는 전역 둘** — 값이 팩토리에서 와야 한다.
_WIRED_GLOBALS: Final = ("_context_store", "_draft_store")
_FACTORIES: Final = {
    "_context_store": "build_context_store",
    "_draft_store": "build_draft_result_store",
}


def _module() -> ast.Module:
    return ast.parse(_ROUTER.read_text(encoding="utf-8"))


def _call_name(node: ast.expr | None) -> str | None:
    """대입 우변이 `f(...)`이면 `f`를 준다 — 리터럴 생성자면 그 이름이 나온다."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _module_level_assignments() -> dict[str, str | None]:
    assigned: dict[str, str | None] = {}
    for node in _module().body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assigned[node.target.id] = _call_name(node.value)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned[target.id] = _call_name(node.value)
    return assigned


def _reset_body_assignments() -> dict[str, str | None]:
    for node in _module().body:
        if isinstance(node, ast.FunctionDef) and node.name == "reset_counsel_stores":
            assigned: dict[str, str | None] = {}
            for statement in ast.walk(node):
                if isinstance(statement, ast.Assign):
                    for target in statement.targets:
                        if isinstance(target, ast.Name):
                            assigned[target.id] = _call_name(statement.value)
            return assigned
    raise AssertionError("reset_counsel_stores가 없다 — 이 검사의 전제가 깨졌다")


@pytest.mark.parametrize("name", _WIRED_GLOBALS)
def test_the_module_initial_value_comes_from_the_factory(name: str) -> None:
    """🔴 **기동 초기값**이 팩토리에서 온다 — 인메모리 리터럴이면 red."""
    actual = _module_level_assignments().get(name, "<없음>")
    assert actual == _FACTORIES[name], (
        f"{name}의 초기값이 {actual}다 — {_FACTORIES[name]}()를 타야 "
        f"STORE_BACKEND=pg가 효력을 갖는다"
    )


@pytest.mark.parametrize("name", _WIRED_GLOBALS)
def test_the_reset_uses_the_same_factory(name: str) -> None:
    """🔴 **reset도 같은 팩토리** — 한쪽만 고치면 기동 전후 구현이 갈린다(99 #02)."""
    actual = _reset_body_assignments().get(name, "<없음>")
    assert actual == _FACTORIES[name], (
        f"reset_counsel_stores의 {name}이 {actual}다 — 초기값과 갈린다"
    )


def test_the_injection_seam_still_exists() -> None:
    """⚠ `set_counsel_stores`를 지우지 않았다 — 테스트 주입 seam은 유지한다."""
    names = {
        node.name
        for node in _module().body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert "set_counsel_stores" in names


@pytest.mark.parametrize(
    ("backend", "context_type", "draft_type"),
    [
        ("memory", InMemoryContextStore, InMemoryDraftResultStore),
        ("pg", PgContextStore, PgDraftResultStore),
    ],
)
def test_the_factories_choose_by_backend(
    backend: str, context_type: type, draft_type: type
) -> None:
    """🔴 **`pg`에서 인메모리 폴백이 없다** — 조용한 강등은 휘발성으로의 강등이다."""
    settings = DbSettings(store_backend=backend)
    assert isinstance(build_context_store(settings), context_type)
    assert isinstance(build_draft_result_store(settings), draft_type)
