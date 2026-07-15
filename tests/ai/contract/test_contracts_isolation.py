"""contracts 패키지의 의존 방향 검사 — 구조 규칙을 테스트로 고정한다.

규칙(03_coding_rules.md §2): 의존 방향은 `capability → contracts ← capability`.
contracts는 다른 내부 모듈(capability·플랫폼)을 import하지 않는다 —
표준 라이브러리 + pydantic + contracts 내부만 허용.

이 검사가 없으면 contracts가 서서히 런타임에 의존하게 되고, 순환 import로
드러날 때는 이미 늦다.
"""

import ast
from pathlib import Path

import pytest

CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "src" / "ai" / "contracts"

#: contracts가 의존해도 되는 서드파티 — 경계 데이터는 전부 pydantic (§4)
ALLOWED_THIRD_PARTY = {"pydantic"}


def _contract_modules() -> list[Path]:
    return sorted(CONTRACTS_DIR.glob("*.py"))


def _imported_roots(source: str) -> set[str]:
    """모듈 소스에서 import 대상의 최상위 패키지명을 뽑는다."""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_contracts_dir_found() -> None:
    """경로 계산이 틀리면 아래 검사가 조용히 0건을 통과시킨다."""
    assert CONTRACTS_DIR.is_dir()
    assert _contract_modules(), "contracts 모듈을 하나도 찾지 못했다"


@pytest.mark.parametrize("module_path", _contract_modules(), ids=lambda p: p.name)
def test_contract_module_imports_only_stdlib_pydantic_or_contracts(module_path: Path) -> None:
    source = module_path.read_text(encoding="utf-8")
    for root in _imported_roots(source):
        if root == "ai":
            continue  # ai.contracts.* 인지는 아래에서 따로 본다
        assert root in ALLOWED_THIRD_PARTY or root in _stdlib_names(), (
            f"{module_path.name}: 허용되지 않은 import '{root}' — "
            "contracts는 표준 라이브러리 + pydantic + contracts 내부만 의존한다"
        )


@pytest.mark.parametrize("module_path", _contract_modules(), ids=lambda p: p.name)
def test_contract_module_does_not_import_other_internal_packages(module_path: Path) -> None:
    """`ai.` 로 시작하는 import는 `ai.contracts.` 만 허용."""
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("ai."):
            assert node.module.startswith("ai.contracts"), (
                f"{module_path.name}: contracts가 내부 모듈 '{node.module}'을 import한다 — "
                "의존 방향 위반 (03_coding_rules.md §2)"
            )
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("ai."):
                    assert alias.name.startswith("ai.contracts"), (
                        f"{module_path.name}: 내부 모듈 '{alias.name}' import — 의존 방향 위반"
                    )


def _stdlib_names() -> frozenset[str]:
    import sys

    return frozenset(sys.stdlib_module_names)
