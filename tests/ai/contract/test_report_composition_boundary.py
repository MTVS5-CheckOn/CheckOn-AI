"""A 판정 ⓒ로 연 리포트→composition 예외를 정확히 한 이름으로 제한한다."""

from __future__ import annotations

import ast
from pathlib import Path

REPORT_PRODUCTION_DIR = Path(__file__).resolve().parents[3] / "src" / "ai" / "report"


def _composition_imports() -> list[tuple[Path, str, tuple[str, ...]]]:
    found: list[tuple[Path, str, tuple[str, ...]]] = []
    for path in sorted(REPORT_PRODUCTION_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("ai.composition"):
                    found.append((path, node.module, tuple(alias.name for alias in node.names)))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("ai.composition"):
                        found.append((path, alias.name, ("<module>",)))
    return found


def test_report_production_has_exactly_one_composition_import_site() -> None:
    """둘째가 생기면 셋째 축이다. red를 끄려고 목록에 한 줄 더하는 것이 아니다 — ⓐ로 가는 것이다."""

    imports = _composition_imports()
    assert len(imports) == 1, (
        f"리포트 프로덕션의 composition import는 정확히 1곳이어야 한다: {imports} — "
        "늘었다면 예외를 추가하지 말고 gates/ 승격(ⓐ)을 시작하라"
    )


def test_the_only_crossed_name_is_check_brief_gate() -> None:
    """한 자리에서 여러 이름을 들이는 우회까지 막는다."""

    [(path, module, names)] = _composition_imports()
    assert path.name == "provider.py"
    assert module == "ai.composition.briefing_gate"
    assert names == ("check_brief_gate",)
