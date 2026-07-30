"""도메인 예외 ↔ HTTP 매핑의 전수 불변식 — error_codes.md §4 (canonical adapter).

`runtime/errors.py`가 정본 트리의 구현이다. 여기서는 **개별 예외를 열거하는 대신 하위
클래스를 전수 순회**해 다음을 고정한다.

① 5xx인데 detail이 노출되는 예외가 하나도 없다 — 새 5xx 예외를 만들 때 노출 정책을
   깜빡하는 것을 잡는 회귀(정책 기본값이 `http_status` 파생이므로 깜빡해도 미노출이지만,
   누군가 `expose_detail = True`를 명시하면 여기서 걸린다).
② 4xx는 detail을 노출한다 — 과잉 차단(디버깅 정보 상실) 회귀 방지.
③ 불변식 4: 트리에 `GateRejected`가 없다(게이트 거부는 반환값이며 예외가 아니다).

**검사 경로가 끊기면 실패시킨다** — 소스에 정의된 하위 클래스와 런타임에 발견되는 집합이
어긋나면(예: 새 모듈에 정의됐는데 import되지 않아 `__subclasses__()`에 안 나타남) 통과가
아니라 실패다. 조용한 통과 금지는 `test_composition_redaction.py`와 같은 결이다.
전수 단정 패턴 선례: `test_llm.py::test_model_role_values_frozen`.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

from ai.runtime.errors import DomainException

_SRC = Path(__file__).resolve().parents[3] / "src" / "ai"

#: 트리에 반드시 있어야 하는 예외 — 이게 안 잡히면 스캔이 끊긴 것이다(error_codes §4).
_MUST_EXIST = frozenset(
    {
        "SnapshotInvalid",
        "IdempotencyConflict",
        "NotFound",
        "ConsentAbsent",
        "LlmUnavailable",
        "RedactionUncertain",
        "LedgerWriteFailed",
    }
)


def _iter_python_files() -> list[Path]:
    return sorted(p for p in _SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _module_name(path: Path) -> str:
    rel = path.relative_to(_SRC.parent).with_suffix("")
    parts = [p for p in rel.parts if p != "__init__"]
    return ".".join(parts)


def _source_defined_subclasses() -> dict[str, str]:
    """소스 전체에서 DomainException 하위 클래스를 찾는다 → {클래스명: 모듈명}.

    직접·간접(하위의 하위) 상속을 fixpoint로 모은다 — 이름 기준이라 alias import는
    잡지 못하지만, 이 저장소의 예외 정의 관행(정의 모듈에서 직접 상속)을 덮는다.
    """
    found: dict[str, str] = {}
    bases = {"DomainException"}
    files = _iter_python_files()
    for _ in range(len(files) + 1):  # fixpoint (상한 있는 루프 — 불변식 6)
        grew = False
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef) or node.name in found:
                    continue
                base_names = {b.id for b in node.bases if isinstance(b, ast.Name)}
                if base_names & bases:
                    found[node.name] = _module_name(path)
                    bases.add(node.name)
                    grew = True
        if not grew:
            break
    return found


def _runtime_subclasses() -> dict[str, type[DomainException]]:
    """import 후 런타임에 실제로 존재하는 하위 클래스(재귀)."""
    for module in set(_source_defined_subclasses().values()):
        importlib.import_module(module)

    discovered: dict[str, type[DomainException]] = {}

    def _walk(cls: type[DomainException]) -> None:
        for sub in cls.__subclasses__():
            discovered[sub.__name__] = sub
            _walk(sub)

    _walk(DomainException)
    return discovered


# ── 검사 경로 무결성 (조용한 통과 금지) ────────────────────────────


def test_scan_finds_exception_tree() -> None:
    """소스에서 예외 트리를 못 찾으면 검사가 끊긴 것 — 통과가 아니라 실패."""
    source = _source_defined_subclasses()
    missing = sorted(_MUST_EXIST - set(source))
    assert not missing, f"소스 스캔이 정본 예외를 놓쳤다 — 검사 경로가 끊겼다: {missing}"


def test_source_and_runtime_trees_agree() -> None:
    """소스 정의 = 런타임 발견. 어긋나면 전수 검사가 일부를 못 본다."""
    source = set(_source_defined_subclasses())
    runtime = set(_runtime_subclasses())
    assert source == runtime, (
        f"소스에만 있음={sorted(source - runtime)} · 런타임에만 있음={sorted(runtime - source)}"
    )


# ── ① 5xx는 detail을 노출하지 않는다 (전수) ────────────────────────


def test_no_5xx_exception_exposes_detail() -> None:
    """새 5xx 예외를 만들며 노출 정책을 깜빡·오설정한 경우를 전수로 잡는다."""
    leaking = sorted(
        name
        for name, cls in _runtime_subclasses().items()
        if cls.http_status >= 500 and cls.detail_is_exposed()
    )
    assert not leaking, (
        f"5xx인데 detail을 응답에 싣는 예외가 있다: {leaking}. "
        "5xx의 detail은 내부 상세·원문 조각일 수 있다(error_codes §4)."
    )


def test_domain_exception_base_is_fail_closed() -> None:
    """루트 기본값이 500이므로 detail 미노출 — 파생 규칙의 fail-closed 방향."""
    assert DomainException.http_status == 500
    assert DomainException.detail_is_exposed() is False


# ── ② 4xx는 detail을 노출한다 (과잉 차단 방지) ─────────────────────


def test_all_4xx_exceptions_expose_detail() -> None:
    """4xx는 클라이언트가 고칠 정보다 — 전수로 노출을 보장한다."""
    blocked = sorted(
        name
        for name, cls in _runtime_subclasses().items()
        if 400 <= cls.http_status < 500 and not cls.detail_is_exposed()
    )
    assert not blocked, f"4xx인데 detail이 막혔다(과잉 차단): {blocked}"


@pytest.mark.parametrize(
    ("name", "http_status", "exposed"),
    [
        ("SnapshotInvalid", 400, True),
        ("NotFound", 404, True),
        ("IdempotencyConflict", 409, True),
        ("ConsentAbsent", 422, True),
        ("LlmUnavailable", 503, False),
        ("RedactionUncertain", 500, False),
        ("LedgerWriteFailed", 500, False),
    ],
)
def test_tree_status_and_exposure_frozen(name: str, http_status: int, exposed: bool) -> None:
    """error_codes §4 트리의 상태 코드와 노출 축을 손으로 옮겨 고정한다."""
    cls = _runtime_subclasses()[name]
    assert cls.http_status == http_status
    assert cls.detail_is_exposed() is exposed


# ── ③ 불변식 4 — GateRejected는 예외가 아니다 ──────────────────────


def test_gate_rejection_is_not_an_exception() -> None:
    """게이트 거부는 200 + status 반환값이다 — 트리에 예외로 있으면 반려 대상."""
    assert "GateRejected" not in _runtime_subclasses()
