"""🔴 **예외 클래스 이름이 겹치지 않는다** — 동명이인이 사고를 만든다.

`runtime/errors.py`에 `LlmUnavailable`이 있었고 `contracts/llm.py`에도 있었다. 둘은
전혀 다른 것이다 — 하나는 **HTTP 매핑**(503), 하나는 **벤더 신호**. 같은 이름이라
§4 트리 A 판정(7/22)이 말한 *"runtime adapter가 받아 변환하는 단일 경계"* 가 코드에서
보이지 않았고, 8/6까지 아무도 못 알아챘다(raise 0곳·import 0곳으로 죽어 있었다).

⚠ **대상은 예외 클래스뿐이다**(B 요청). 상수·함수·데이터클래스는 보지 않는다 —
`determinism` 재수출처럼 **이름이 두 모듈에 보이는 게 정상인** 경우가 있다.

⚠ **한계:** AST는 실제 MRO를 못 본다. 상속 판정은 **base 이름 휴리스틱**이다 —
`Exception`·`*Error`·`DomainException` 등으로 끝나는 base를 상속하면 예외로 본다.
간접 상속(`class A(B)` where `B(Exception)`)은 같은 파일에 B가 있으면 잡고, 다른 파일이면
놓친다. 놓치는 방향이라 **오탐은 없고 미탐이 있을 수 있다** — 그 대신 화이트리스트가 없다.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3] / "src" / "ai"

#: 이 이름을 상속하면 예외로 본다. `*Error`·`*Exception` 접미는 아래에서 따로 본다.
_EXCEPTION_BASES = frozenset(
    {
        "Exception",
        "BaseException",
        "ValueError",
        "RuntimeError",
        "TypeError",
        "KeyError",
        "LookupError",
        "OSError",
    }
)


def _is_exception_base(name: str) -> bool:
    return name in _EXCEPTION_BASES or name.endswith(("Error", "Exception"))


def _base_names(node: ast.ClassDef) -> list[str]:
    names: list[str] = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(base.attr)
    return names


def _exception_classes() -> dict[str, list[str]]:
    """예외 클래스 이름 → 정의 위치 목록. 같은 파일 안의 간접 상속도 한 겹 따라간다."""
    found: dict[str, list[str]] = defaultdict(list)
    for path in sorted(p for p in _SRC.rglob("*.py") if "__pycache__" not in p.parts):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        local_exceptions = {
            node.name
            for node in classes
            if any(_is_exception_base(b) for b in _base_names(node))
        }
        # 한 겹 더 — 같은 파일 안에서 위 집합을 상속한 것도 예외다.
        for _ in range(3):  # 상한 있는 루프(불변식 6) — 깊은 계층은 놓친다(docstring)
            grew = {
                node.name
                for node in classes
                if any(b in local_exceptions for b in _base_names(node))
            }
            if grew <= local_exceptions:
                break
            local_exceptions |= grew
        for name in sorted(local_exceptions):
            found[name].append(str(path.relative_to(_SRC.parent)))
    return found


def test_scan_finds_exception_classes() -> None:
    """🔴 검사 경로 절단 검출 — 하나도 못 찾으면 **통과가 아니라 실패**다.

    조용한 통과가 7/30 트레이스 사고의 유형이다.
    """
    found = _exception_classes()
    assert len(found) >= 15, f"예외 클래스 스캔이 끊겼다: {len(found)}종"
    # 정본 몇 개가 실제로 잡히는지 — 스캔이 엉뚱한 걸 세고 있지 않은지 본다.
    for name in ("DomainException", "LlmError", "LlmUpstreamDown", "RedactionUncertain"):
        assert name in found, f"{name}을 못 찾았다 — 상속 판정 휴리스틱이 어긋났다"


def test_no_duplicate_exception_class_names() -> None:
    """🔴 같은 이름의 예외 클래스가 두 곳에 정의되지 않는다.

    ⚠ **화이트리스트를 두지 않는다.** 지금 통과해야 정상이다 — 이 PR이 유일한 중복
    (`LlmUnavailable`)을 없앴다. 예외를 허용하기 시작하면 목록이 사고를 숨긴다.
    """
    duplicates = {
        name: paths for name, paths in _exception_classes().items() if len(paths) > 1
    }
    assert not duplicates, (
        f"동명 예외 클래스: {duplicates} — 하나는 개명하라. "
        "받는 쪽과 받히는 쪽이 같은 이름이면 그건 경계가 아니다(§4 A 판정 7/22)"
    )


def test_the_boundary_pair_is_named_apart() -> None:
    """변환 경계의 양쪽이 **이름으로 구분**된다 — 이 PR이 세운 규율."""
    found = _exception_classes()
    assert "LlmUnavailable" in found and "LlmTimeout" in found  # contracts.llm 쪽
    assert "LlmUpstreamDown" in found and "LlmUpstreamTimeout" in found  # runtime 쪽
    assert found["LlmUnavailable"] != found["LlmUpstreamDown"]
