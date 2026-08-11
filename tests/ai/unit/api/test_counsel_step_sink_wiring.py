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
from ai.db.repositories.counsel_step_store import PgCounselAgentStepSink

from ai.api.routers import counsel as counsel_router
from ai.api.routers.counsel import reset_counsel_stores
from ai.db.settings import get_db_settings

_ROUTER: Final = Path(counsel_router.__file__)
_BUILDER: Final = "build_counsel_agent_step_sink"


def _assignments_to_step_sink() -> list[ast.AST]:
    """`_step_sink = …` 대입의 **오른쪽**만 모은다 — 초기값과 reset 둘 다."""
    tree = ast.parse(_ROUTER.read_text(encoding="utf-8"))
    values: list[ast.AST] = []
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "_step_sink":
                if getattr(node, "value", None) is not None:
                    values.append(node.value)  # type: ignore[arg-type]
    return values


def test_the_scan_finds_both_assignment_sites() -> None:
    """🔴 절단 가드 — 대입 자리를 둘 미만 찾으면 **안 본 것**이다(초기값 + reset)."""
    values = _assignments_to_step_sink()
    assert len(values) >= 2, f"`_step_sink` 대입을 {len(values)}개만 찾았다"


def test_every_assignment_goes_through_the_builder() -> None:
    """🔴 **초기값과 reset이 「둘 다」 빌더를 타야 한다.**

    ⚠ 한쪽만 고치면 **프로세스 기동 시엔 인메모리, 테스트 reset 뒤엔 PG** 같은
    엇갈린 상태가 된다 — 그 차이는 조용하다.
    """
    offenders = [
        ast.unparse(value)
        for value in _assignments_to_step_sink()
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
    get_db_settings.cache_clear()  # type: ignore[attr-defined]
    try:
        reset_counsel_stores()
        assert isinstance(counsel_router._step_sink, PgCounselAgentStepSink), (
            f"pg인데 reset이 {type(counsel_router._step_sink).__name__}를 꽂았다"
        )
    finally:
        monkeypatch.delenv("STORE_BACKEND", raising=False)
        get_db_settings.cache_clear()  # type: ignore[attr-defined]
        reset_counsel_stores()


def test_memory_backend_keeps_the_in_memory_sink() -> None:
    """기본값 회귀 — memory에서는 종전 동작 그대로다."""
    from ai.composition.counsel.stores import InMemoryAgentStepSink

    reset_counsel_stores()
    assert isinstance(counsel_router._step_sink, InMemoryAgentStepSink)
