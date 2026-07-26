"""벤더 독립 경계 — `openai` import는 llm/providers/ 안에서만 (B-5 확정 7/23).

벤더 확정으로 SDK 설치는 해제됐지만, capability·contracts·플랫폼이 벤더 SDK를
직접 import하면 벤더 교체가 다시 전 계층으로 번진다. 이 검사가 그 경계를 고정한다
(contracts/llm.py의 벤더명 검출 테스트를 소스 트리 전체로 확장 — CLAUDE.md §3 각주).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[3] / "src" / "ai"
#: 벤더 SDK를 import해도 되는 유일한 곳 — provider 어댑터.
_ALLOWED = _SRC / "llm" / "providers"
_VENDORS = {"openai", "anthropic", "cohere", "mistralai", "google"}


def _vendor_imports(path: Path) -> set[str]:
    roots: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots & _VENDORS


def _production_modules() -> list[Path]:
    return sorted(p for p in _SRC.rglob("*.py") if "__pycache__" not in p.parts)


def test_src_tree_found() -> None:
    assert _production_modules(), "src/ai 모듈을 하나도 찾지 못했다"


@pytest.mark.parametrize("module", _production_modules(), ids=lambda p: str(p.name))
def test_vendor_sdk_only_in_providers(module: Path) -> None:
    """벤더 SDK import는 llm/providers/ 밖에서 0건이어야 한다."""
    vendors = _vendor_imports(module)
    if not vendors:
        return
    assert _ALLOWED in module.parents, (
        f"{module.relative_to(_SRC)}가 벤더 SDK {sorted(vendors)}를 import한다 — "
        "capability·contracts는 벤더 독립(llm/providers/ 안에서만 허용)"
    )
