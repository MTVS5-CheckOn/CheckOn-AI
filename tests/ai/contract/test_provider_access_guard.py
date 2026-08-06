"""🔴 라우터가 전역 `_provider`를 **직접 읽지 않는다** — 같은 패턴 5번째를 막는다.

#108이 "조용한 Fake 금지"를 세우며 `require_counsel_provider()`를 만들었는데,
**refine 핸들러 한 줄만** `writer=_provider`로 전역을 직접 읽고 있었다. 같은 파일 안에서
두 경로가 갈렸고 CI는 초록이었다.

같은 형태가 반복됐다:

| PR | 규율이 있던 곳 | 빠진 곳 |
| --- | --- | --- |
| #108 | `assembly.py` | 라우터 |
| #111 | `graph.py` | `refine.py` |
| #114 | `gate_feedback.yaml` | `_GATE_REASON_TO_BLOCK` |
| #116 | 호칭 갈래 관계어 | 별명형 갈래 관계어 |
| **이번** | POST 경로 | **refine 경로** |

⇒ "한 곳에 규율을 적는다"로는 부족하고 **경로 전수를 CI가 세야** 한다.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from ai.api.routers import counsel as counsel_router

#: `_provider`를 직접 읽어도 되는 함수 — 배선 자체를 다루는 셋뿐이다.
_ALLOWED = frozenset(
    {"set_counsel_provider", "require_counsel_provider", "bootstrap_counsel_provider"}
)

#: 모듈 최상위 선언(`_provider: Any = None`)은 읽기가 아니다.
_MODULE_LEVEL = "<module>"


def _reads_of_provider() -> list[tuple[str, int]]:
    """`_provider`를 읽는 (함수명, 줄번호) 전수."""
    source = Path(inspect.getfile(counsel_router)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    found: list[tuple[str, int]] = []

    class _Walk(ast.NodeVisitor):
        def __init__(self) -> None:
            self.scope = _MODULE_LEVEL

        def _visit_function(self, node: ast.AST) -> None:
            outer = self.scope
            self.scope = getattr(node, "name", outer)
            self.generic_visit(node)
            self.scope = outer

        visit_FunctionDef = _visit_function  # noqa: N815
        visit_AsyncFunctionDef = _visit_function  # noqa: N815

        def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
            if node.id == "_provider" and isinstance(node.ctx, ast.Load):
                found.append((self.scope, node.lineno))
            self.generic_visit(node)

    _Walk().visit(tree)
    return found


def test_scan_finds_the_seam() -> None:
    """🔴 검사 경로 절단 검출 — 허용 함수에서조차 못 찾으면 스캔이 끊긴 것이다."""
    scopes = {scope for scope, _ in _reads_of_provider()}
    assert scopes & _ALLOWED, (
        f"`_provider` 읽기를 하나도 못 찾았다 — AST 스캔이 끊겼다(찾은 스코프: {scopes})"
    )


def test_no_handler_reads_the_global_provider_directly() -> None:
    """🔴 배선 함수 셋 밖에서 `_provider`를 읽으면 실패."""
    offenders = [
        (scope, line)
        for scope, line in _reads_of_provider()
        if scope not in _ALLOWED and scope != _MODULE_LEVEL
    ]
    assert not offenders, (
        "라우터 핸들러가 전역 `_provider`를 직접 읽는다: "
        + ", ".join(f"{scope}():{line}" for scope, line in offenders)
        + " — `require_counsel_provider()`를 쓰라(미배선을 조용히 통과시킨다 · #108)"
    )


def test_both_request_paths_go_through_the_seam() -> None:
    """POST와 refine **둘 다** seam을 지난다 — 한쪽만 지나던 것이 이번 결함이다."""
    source = Path(inspect.getfile(counsel_router)).read_text(encoding="utf-8")
    assert source.count("require_counsel_provider()") >= 3, (
        "seam 호출이 3곳 미만이다 — POST · refine · startup 훅이 각각 써야 한다"
    )
