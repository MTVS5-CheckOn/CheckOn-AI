"""문제생성의 모든 LLM gateway 호출이 직전 redaction 계약을 지킨다.

호출부를 화이트리스트로 고정하고 AST 구조를 검사한다. 새 호출부가 생기거나 기존
호출 경로를 찾지 못하면 조용히 통과하지 않고, redaction 검토가 필요하다는 메시지로
실패한다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[3] / "src" / "ai"
_PROBLEM_GENERATION = _SRC / "problem_generation"

#: gateway를 호출해도 되는 지점의 화이트리스트. **application 계층에만 존재해야 한다** —
#: domain은 순수 규칙이라 LLM을 부르지 않고, infrastructure는 저장·설정 어댑터다.
#: 새 호출 지점이 생기면 이 목록을 늘리기 전에 계층이 맞는지 먼저 본다.
_EXPECTED_GATEWAY_CALLS = frozenset(
    {
        ("problem_generation/application/cross_solver.py", "solve"),
        ("problem_generation/application/generator.py", "generate"),
    }
)


def _attribute_path(node: ast.expr) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        return (*_attribute_path(node.value), node.attr)
    return ()


def _is_gateway_complete(call: ast.Call) -> bool:
    path = _attribute_path(call.func)
    return len(path) >= 2 and path[-1] == "complete" and path[-2].endswith("gateway")


def _gateway_call_sites() -> dict[tuple[str, str], list[ast.Call]]:
    sites: dict[tuple[str, str], list[ast.Call]] = {}
    for path in sorted(_PROBLEM_GENERATION.rglob("*.py")):
        relative_path = path.relative_to(_SRC).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            calls = [
                call
                for call in ast.walk(function)
                if isinstance(call, ast.Call) and _is_gateway_complete(call)
            ]
            if calls:
                sites[(relative_path, function.name)] = calls
    return sites


def _function_node(
    module_path: str,
    function_name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    path = _SRC / module_path
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == function_name
    ]
    assert len(matches) == 1, (
        f"{module_path}:{function_name} 함수를 하나로 찾지 못했다 "
        f"(발견={len(matches)}) — redaction 검사 경로가 끊겼다"
    )
    return matches[0]


def _is_redact_assignment(node: ast.AST) -> bool:
    if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
        return False
    targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
    return (
        "redacted" in targets
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "redact"
    )


def _blocks_uncertain_redaction(node: ast.AST) -> bool:
    if not isinstance(node, ast.If):
        return False
    test = node.test
    if not (
        isinstance(test, ast.Attribute)
        and isinstance(test.value, ast.Name)
        and test.value.id == "redacted"
        and test.attr == "uncertain"
    ):
        return False
    return any(
        isinstance(child, ast.Raise)
        and isinstance(child.exc, ast.Call)
        and isinstance(child.exc.func, ast.Name)
        and child.exc.func.id == "RedactionBlocked"
        for child in ast.walk(node)
    )


def _uses_masked_prompt(call: ast.Call) -> bool:
    if not call.args or not isinstance(call.args[0], ast.Call):
        return False
    request_call = call.args[0]
    if not (isinstance(request_call.func, ast.Name) and request_call.func.id == "LLMRequest"):
        return False
    prompt = next(
        (keyword.value for keyword in request_call.keywords if keyword.arg == "prompt"),
        None,
    )
    return (
        isinstance(prompt, ast.Attribute)
        and isinstance(prompt.value, ast.Name)
        and prompt.value.id == "redacted"
        and prompt.attr == "masked_text"
    )


def test_gateway_complete_call_sites_match_whitelist() -> None:
    sites = _gateway_call_sites()
    assert sites, (
        "problem_generation에서 gateway.complete 호출부를 찾지 못했다 "
        "— redaction 검사 경로가 끊겼다"
    )
    assert set(sites) == _EXPECTED_GATEWAY_CALLS, (
        "gateway.complete 호출부가 바뀌었다 — redaction 계약을 검토하고 "
        f"화이트리스트를 갱신하라 (발견={sorted(sites)})"
    )
    multiple = {site: len(calls) for site, calls in sites.items() if len(calls) != 1}
    assert not multiple, (
        "화이트리스트 함수의 gateway.complete 호출 수가 바뀌었다 "
        f"— redaction 계약을 재검토하라: {multiple}"
    )


@pytest.mark.parametrize(
    ("module_path", "function_name"),
    sorted(_EXPECTED_GATEWAY_CALLS),
)
def test_gateway_call_uses_fail_closed_redacted_prompt(
    module_path: str,
    function_name: str,
) -> None:
    function = _function_node(module_path, function_name)
    calls = [
        call
        for call in ast.walk(function)
        if isinstance(call, ast.Call) and _is_gateway_complete(call)
    ]
    assert len(calls) == 1, (
        f"{module_path}:{function_name}의 gateway.complete 호출을 하나로 찾지 못했다 "
        "— redaction 검사 경로가 끊겼다"
    )
    gateway_call = calls[0]
    redactions = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Assign)
        and _is_redact_assignment(node)
        and node.lineno < gateway_call.lineno
    ]
    assert redactions, f"{module_path}:{function_name}이 gateway 호출 전에 redact를 통과하지 않는다"
    uncertain_guards = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.If)
        and _blocks_uncertain_redaction(node)
        and node.lineno < gateway_call.lineno
    ]
    assert uncertain_guards, (
        f"{module_path}:{function_name}이 redacted.uncertain을 RedactionBlocked로 차단하지 않는다"
    )
    assert _uses_masked_prompt(gateway_call), (
        f"{module_path}:{function_name}이 LLMRequest.prompt에 "
        "redacted.masked_text를 전달하지 않는다"
    )
