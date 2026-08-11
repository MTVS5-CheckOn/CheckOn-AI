"""counsel 라우터가 **백엔드에 맞는 step sink로 조립되는가** (99 #37).

🔴 **초기 조립과 `reset_counsel_stores()`를 따로 본다.** 한쪽만 빌더를 타는 **반쪽 수정**이
통과하면 안 된다 — 이 저장소가 여러 번 겪은 형태다(99 #02).

⚠ **테스트가 `_step_sink`에 대역을 꽂아 생산 배선을 대신하지 않는다** — 여기서 재는 것은
**조립이 무엇을 고르는가**다.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

import pytest

from ai.api.routers import counsel as counsel_router
from ai.api.routers.counsel import reset_counsel_stores
from ai.db.repositories.counsel_step_store import PgCounselAgentStepSink
from ai.db.settings import get_db_settings
from ai.db.store_factory import reset_shared_agent_runtime

_ROUTER: Final = Path(counsel_router.__file__)
_BUILDER: Final = "build_counsel_agent_step_sink"


#: 🔴 **주입 seam은 위반이 아니다.** `set_counsel_stores(step_sink=…)`가 인자를 그대로
#: 대입하는 것은 **테스트·조립부가 명시로 넣는 자리**이고 유지 대상이다.
#: ⚠ 처음엔 그것까지 세어 red를 냈다 — **검사가 자기 이름보다 넓었다**(로그 85 계열).
#: 여기서 묻는 것은 **「기본 조립」 두 자리**다: 모듈 초기값과 `reset_counsel_stores()`.
_INJECTION_SEAM: Final = "set_counsel_stores"


def _reset_all() -> None:
    """⚠ **둘을 나란히 부른다**(99 ㊒) — 잡과 체크포인트가 `thread_id`로 엮여 있다.

    (기존 페어링 가드가 이 파일을 잡아 줬다.)
    """
    reset_shared_agent_runtime()
    reset_counsel_stores()


def _default_assignments() -> list[tuple[str, ast.expr]]:
    """**기본 조립**의 `_step_sink` 대입 — 주입 seam 안쪽은 제외한다."""
    tree = ast.parse(_ROUTER.read_text(encoding="utf-8"))
    seam_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == _INJECTION_SEAM:
            seam_lines |= set(range(node.lineno, (node.end_lineno or node.lineno) + 1))

    found: list[tuple[str, ast.expr]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets: list[ast.expr] = list(node.targets)
            value: ast.expr | None = node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        else:
            continue
        if value is None:
            continue
        lineno = node.lineno
        for target in targets:
            if (
                isinstance(target, ast.Name)
                and target.id == "_step_sink"
                and lineno not in seam_lines
            ):
                found.append((f"line {lineno}", value))
    return found


def test_the_scan_finds_both_assignment_sites() -> None:
    """🔴 절단 가드 — 대입 자리를 둘 미만 찾으면 **안 본 것**이다(초기값 + reset)."""
    found = _default_assignments()
    assert len(found) >= 2, (
        f"기본 조립의 `_step_sink` 대입을 {len(found)}개만 찾았다 — "
        "초기값과 reset 둘을 봐야 한다"
    )


def test_every_assignment_goes_through_the_builder() -> None:
    """🔴 **초기값과 reset이 「둘 다」 빌더를 타야 한다.**

    ⚠ 한쪽만 고치면 **프로세스 기동 시엔 인메모리, 테스트 reset 뒤엔 PG** 같은
    엇갈린 상태가 된다 — 그 차이는 조용하다.
    """
    offenders = [
        f"{where}: {ast.unparse(value)}"
        for where, value in _default_assignments()
        if not (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == _BUILDER
        )
    ]
    assert not offenders, (
        f"빌더를 안 타는 `_step_sink` 대입이 있다: {offenders} — "
        f"초기값과 reset 둘 다 `{_BUILDER}()`여야 한다"
    )


def test_reset_rebuilds_for_the_current_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """🔴 **reset이 현재 설정을 다시 읽어야 한다** — 굳은 값을 되돌리면 안 된다."""
    monkeypatch.setenv("STORE_BACKEND", "pg")
    get_db_settings.cache_clear()
    try:
        _reset_all()
        assert isinstance(counsel_router._step_sink, PgCounselAgentStepSink), (
            f"pg인데 reset이 {type(counsel_router._step_sink).__name__}를 꽂았다"
        )
    finally:
        monkeypatch.delenv("STORE_BACKEND", raising=False)
        get_db_settings.cache_clear()
        _reset_all()


def test_memory_backend_keeps_the_in_memory_sink() -> None:
    """기본값 회귀 — memory에서는 종전 동작 그대로다."""
    from ai.composition.counsel.stores import InMemoryAgentStepSink

    _reset_all()
    assert isinstance(counsel_router._step_sink, InMemoryAgentStepSink)
