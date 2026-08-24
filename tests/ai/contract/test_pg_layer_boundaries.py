"""problem_generation 계층 경계 계약 — 의존 방향을 AST로 고정한다.

계층 정의는 `docs/part_b/13_code_layout.md`가 정본이다.

    domain          순수 규칙·값 객체. I/O·LLM·프레임워크 의존 0
    application     도메인을 조합하고 포트를 호출하는 오케스트레이션
    infrastructure  포트 구현·파일 I/O 어댑터

허용 방향은 `infrastructure → application → domain` 하나뿐이다. 역방향이 생기면
순수 규칙이 바깥 세계에 묶여 테스트가 어려워지고, 결정론 경로(불변식 8)에
파일·네트워크가 섞여 들어온다.

이 테스트가 없으면 계층은 조용히 무너진다 — import 한 줄이면 되기 때문이다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PG = Path(__file__).resolve().parents[3] / "src" / "ai" / "problem_generation"

#: domain이 의존해도 되는 외부 뿌리. contracts는 계약이라 순수하고,
#: pydantic은 값 객체 정의에 쓴다. 그 밖은 전부 바깥 세계다.
_DOMAIN_ALLOWED_ROOTS = frozenset({"ai.contracts", "ai.problem_generation.domain"})

#: application 설정도 조립부에서 주입한다. infrastructure 역참조 예외는 없다.
_APPLICATION_TO_INFRASTRUCTURE_ALLOWED: frozenset[str] = frozenset()


def _modules(layer: str) -> list[Path]:
    return sorted(p for p in (_PG / layer).glob("*.py") if p.name != "__init__.py")


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module)
        elif isinstance(node, ast.Import):
            roots.update(alias.name for alias in node.names)
    return roots


@pytest.mark.parametrize("module", _modules("domain"), ids=lambda p: p.name)
def test_domain_does_not_depend_on_outer_layers(module: Path) -> None:
    """domain은 상위 계층을 모른다 — 역방향 의존은 0이어야 한다."""
    offenders = sorted(
        root
        for root in _imported_roots(module)
        if root.startswith("ai.problem_generation.")
        and not root.startswith("ai.problem_generation.domain")
    )
    assert not offenders, (
        f"{module.name}이 상위 계층을 참조한다: {', '.join(offenders)}. "
        "domain은 순수 규칙이므로 application·infrastructure를 알면 안 된다."
    )


@pytest.mark.parametrize("module", _modules("domain"), ids=lambda p: p.name)
def test_domain_has_no_io_or_llm_dependency(module: Path) -> None:
    """domain에 파일·네트워크·LLM이 들어오면 결정론 경로가 깨진다(불변식 8)."""
    banned = {"yaml", "aiokafka", "sqlalchemy", "httpx", "openai", "langgraph"}
    roots = _imported_roots(module)
    offenders = sorted(
        root
        for root in roots
        if root.split(".")[0] in banned or root.startswith("ai.llm")
    )
    assert not offenders, (
        f"{module.name}에 I/O·LLM 의존이 있다: {', '.join(offenders)}. "
        "설정 로딩은 infrastructure, LLM 호출은 application이 맡는다."
    )


@pytest.mark.parametrize("module", _modules("application"), ids=lambda p: p.name)
def test_application_to_infrastructure_stays_on_whitelist(module: Path) -> None:
    """application → infrastructure 역참조는 허용하지 않는다."""
    uses_infra = any(
        root.startswith("ai.problem_generation.infrastructure")
        for root in _imported_roots(module)
    )
    relative = f"application/{module.name}"
    if relative in _APPLICATION_TO_INFRASTRUCTURE_ALLOWED:
        return
    assert not uses_infra, (
        f"{relative}이 infrastructure를 직접 import 한다. "
        "포트(application/ports.py)로 받아 조립부에서 주입하라 — "
        "예외를 늘리려면 _APPLICATION_TO_INFRASTRUCTURE_ALLOWED를 함께 고친다."
    )


def test_infrastructure_implements_ports_not_the_reverse() -> None:
    """포트는 application이 소유하고 infrastructure가 구현한다."""
    for module in _modules("infrastructure"):
        offenders = sorted(
            root
            for root in _imported_roots(module)
            if root.startswith("ai.problem_generation.application")
            and not root.startswith("ai.problem_generation.application.ports")
        )
        assert not offenders, (
            f"{module.name}이 application의 포트 밖을 참조한다: {', '.join(offenders)}."
        )


def test_layers_exist_and_are_populated() -> None:
    """계층이 비어 있으면 이 계약 자체가 무력화된다 — 파일 이동 시 조기 경보."""
    for layer in ("domain", "application", "infrastructure"):
        modules = _modules(layer)
        assert modules, f"{layer} 계층이 비어 있다 — 13 §2 구조가 깨졌다."

def test_the_sweep_has_something_to_sweep() -> None:
    """🔴 **순회 대상이 0이면 이 파일의 검사들이 「조용히 사라진다」**(99 #202 · №88 실측).

    `parametrize` 에 빈 목록이 가면 red 도 skip 도 아니고 **collect 조차 안 된다** —
    실측(2026-08-24): 이 파일을 비웠더니 `29 passed` 가 `20 passed` 가 됐고 **아무도 안 빨개졌다.**

    🔴 **`N` 이 아니라 「0이 아니다」로 문다** — 이 목록은 **늘어나는 것**이라 `N` 을 박으면
    항목이 생길 때마다 그 수를 고쳐야 한다(99 #211 ⓐ 의 반대편). «수가 줄면 알아차린다»가
    목적인 자리(코퍼스·픽스처 총수)와는 **다르다** — 여기 목적은 «**사라지면 알아차린다**» 다.
    """
    assert _modules("domain"), "domain 순회 대상이 0이다 — 검사가 조용히 사라졌다"
    assert _modules("application"), "application 순회 대상이 0이다"
