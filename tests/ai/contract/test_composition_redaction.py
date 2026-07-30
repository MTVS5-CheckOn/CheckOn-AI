"""redact 누락 방지 — A 경로에서 gateway로 나가기 전 redaction이 반드시 있다 (불변식 3).

B가 자기 경로(`problem_generation`)에 같은 보호를 넣고 A 경로는 A 판단으로 넘겼다
(2026-07-30). 같은 형태로 A 쪽에 건다.

**두 축으로 고정한다:**
1. **화이트리스트** — gateway 호출(`.complete(...)`)이 허용된 지점 목록을 상수로 두고,
   목록 밖에서 호출이 생기면 실패한다. 새 소비자가 생기면 사람이 한 번 보게 된다.
2. **호출 직전 redaction** — 허용 지점의 함수 본문에 `redact(...)` 호출과
   `uncertain` 분기(fail-closed)가 있어야 한다.

**"검사 경로가 끊겼다"를 명시적으로 실패시킨다** — 대상 모듈이 하나도 안 잡히거나 gateway
호출을 하나도 못 찾으면 통과가 아니라 실패다. 조용한 통과가 7/30 트레이스 사고의 유형이다.

선례: `test_evidence_blind_isolation.py`(blind 격리 AST) · `test_evaluation_isolation.py`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[3] / "src" / "ai"
_COMPOSITION = _SRC / "composition"

#: gateway 호출이 **허용된** 지점(화이트리스트). 목록 밖 호출은 실패다.
#: 새 LLM 소비자를 추가하려면 이 목록에 넣어야 하고, 그때 redaction 검사도 함께 걸린다.
_ALLOWED_GATEWAY_CALLERS: frozenset[tuple[str, str]] = frozenset(
    {
        ("briefing.py", "make_brief"),  # 브리핑 문장화(ⓐ) — role=narrator
        ("counsel/provider.py", "write"),  # 상담 초안 — role=counselor
    }
)

#: `.complete(...)` 를 gateway 호출로 본다 — LLMProvider·LlmGateway 공통 시그니처.
_GATEWAY_CALL_ATTR = "complete"


def _iter_python_files() -> list[Path]:
    return sorted(p for p in _COMPOSITION.rglob("*.py") if "__pycache__" not in p.parts)


def _rel(path: Path) -> str:
    """`_ALLOWED_GATEWAY_CALLERS`와 대조할 키 — **항상 `/` 표기**(화이트리스트가 정본).

    `str()`을 쓰면 Windows에서 `counsel\\provider.py`가 나와 전부 불일치한다.
    선례: `test_problem_generation_redaction.py`의 `relative_to(...).as_posix()`.
    """
    return path.relative_to(_COMPOSITION).as_posix()


def _enclosing_functions(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]


def _gateway_calls(path: Path) -> list[tuple[str, str]]:
    """(파일, 함수명) — 이 파일 안에서 `.complete(...)`를 호출하는 함수들."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, str]] = []
    for func in _enclosing_functions(tree):
        for node in ast.walk(func):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == _GATEWAY_CALL_ATTR
            ):
                found.append((_rel(path), func.name))
                break
    return found


def _calls_redact(path: Path, func_name: str) -> bool:
    """해당 함수 안에 `redact(...)` 호출이 있는지."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for func in _enclosing_functions(tree):
        if func.name != func_name:
            continue
        for node in ast.walk(func):
            if isinstance(node, ast.Call):
                target = node.func
                if isinstance(target, ast.Name) and target.id == "redact":
                    return True
                if isinstance(target, ast.Attribute) and target.attr == "redact":
                    return True
    return False


def _checks_uncertain(path: Path, func_name: str) -> bool:
    """해당 함수 안에 `uncertain` 분기(fail-closed)가 있는지."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for func in _enclosing_functions(tree):
        if func.name != func_name:
            continue
        for node in ast.walk(func):
            if isinstance(node, ast.Attribute) and node.attr == "uncertain":
                return True
    return False


def _all_gateway_calls() -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    for path in _iter_python_files():
        calls.extend(_gateway_calls(path))
    return calls


# ── 검사 경로 무결성 (조용한 통과 금지) ────────────────────────────


def test_scan_target_exists() -> None:
    """composition/ 모듈을 하나도 못 찾으면 검사가 끊긴 것 — 통과가 아니라 실패."""
    files = _iter_python_files()
    assert files, "composition/ 에서 .py 를 하나도 찾지 못했다 — 검사 경로가 끊겼다"


def test_at_least_one_gateway_call_found() -> None:
    """gateway 호출을 하나도 못 찾으면 검사가 끊긴 것 — 통과가 아니라 실패."""
    calls = _all_gateway_calls()
    assert calls, (
        "composition/ 에서 gateway 호출(.complete)을 하나도 찾지 못했다 — "
        "검사 경로가 끊겼다(호출 방식이 바뀌었는지 확인)"
    )


def test_whitelist_entries_all_exist() -> None:
    """화이트리스트에 죽은 항목이 없다 — 삭제된 지점을 남겨두면 검사가 헐거워진다."""
    found = set(_all_gateway_calls())
    stale = sorted(_ALLOWED_GATEWAY_CALLERS - found)
    assert not stale, f"화이트리스트에 실재하지 않는 지점이 있다: {stale}"


# ── ① 화이트리스트 — 목록 밖 호출 금지 ────────────────────────────


def test_no_gateway_call_outside_whitelist() -> None:
    """새 LLM 소비자가 생기면 실패한다 — 사람이 redaction을 확인하고 목록에 넣어야 한다."""
    unexpected = sorted(set(_all_gateway_calls()) - _ALLOWED_GATEWAY_CALLERS)
    assert not unexpected, (
        f"화이트리스트 밖에서 gateway를 호출한다: {unexpected}. "
        "redaction(fail-closed)을 붙이고 _ALLOWED_GATEWAY_CALLERS에 등록하라."
    )


# ── ② 호출 직전 redaction + fail-closed 분기 ──────────────────────


@pytest.mark.parametrize(
    ("module", "func"), sorted(_ALLOWED_GATEWAY_CALLERS)
)
def test_gateway_caller_redacts_before_sending(module: str, func: str) -> None:
    """허용 지점은 전송 전 redact()를 호출한다(불변식 3)."""
    path = _COMPOSITION / module
    assert path.is_file(), f"{module} 이 없다 — 화이트리스트가 stale하다"
    assert _calls_redact(path, func), (
        f"{module}::{func} 가 gateway를 호출하는데 redact()가 없다 — 원문 전송 위험"
    )


@pytest.mark.parametrize(
    ("module", "func"), sorted(_ALLOWED_GATEWAY_CALLERS)
)
def test_gateway_caller_is_fail_closed_on_uncertain(module: str, func: str) -> None:
    """마스킹 불확실이면 전송하지 않는다 — uncertain 분기가 있어야 한다."""
    path = _COMPOSITION / module
    assert _checks_uncertain(path, func), (
        f"{module}::{func} 에 uncertain 분기가 없다 — fail-closed가 아니다"
    )
