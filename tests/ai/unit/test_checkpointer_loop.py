"""체크포인터의 **이벤트 루프 분기** — Windows 없이도 값으로 확인한다.

🔴 **왜 단위 검사인가.** 종전에는 같은 분기가 `tests/ai/integration/`에만 있었다.
그 마커는 기본 실행에서 빠지므로, **배포 명령이 Windows에서 깨진 채로도 게이트가 초록**
이었다(실측 2026-08-12: `python -m ai.agents.checkpointer` → `InterfaceError` → exit 1).
루프 규칙이 프로덕션으로 온 지금은 **PG 없이 도는 자리**에서 지킨다.

⚠ psycopg의 async 구현은 Windows 기본 `ProactorEventLoop`에서 **연결 시도 전에**
`InterfaceError`를 낸다 — 그래서 이 분기는 「있으면 좋은 것」이 아니라 가동 조건이다.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path
from typing import Final

import pytest

from ai.agents.checkpointer import checkpoint_loop_factory, run_with_checkpoint_loop

_PACKAGE_ROOT: Final = Path(__file__).resolve().parents[3] / "src" / "ai" / "__init__.py"


def test_the_package_root_pins_the_selector_policy_on_windows_only() -> None:
    """🔴 **서버 본체의 루프**를 고정한 자리 — 없어지면 Windows 상담 경로가 전부 500이다.

    운영 기동은 `uvicorn ai.api.app:app`이라 uvicorn이 자기 루프를 만든다. 우리가 끼어들 수
    있는 곳은 **앱 모듈보다 먼저 import되는 패키지 루트**뿐이다.

    🔴 **분기가 없어지면 맥·리눅스가 Windows 정책을 쓴다** — 그래서 `sys.platform` 가드를
    같이 본다(그 OS 없이 값으로 확인해야 하므로 원본을 읽는다).
    """
    source = _PACKAGE_ROOT.read_text(encoding="utf-8")
    assert "WindowsSelectorEventLoopPolicy" in source, (
        "패키지 루트가 Selector 정책을 안 건다 — Windows에서 psycopg 경로가 InterfaceError다"
    )
    guard = 'if sys.platform == "win32":'
    assert guard in source, "정책 설정에 플랫폼 가드가 없다 — 맥·리눅스 기본 루프가 바뀐다"
    body = source.split(guard, 1)[1]
    assert "set_event_loop_policy" in body, (
        "정책 설정이 Windows 가드 **밖**에 있다 — 맥·리눅스까지 갈아 끼운다"
    )
    assert source.count("set_event_loop_policy") == 1, (
        "정책을 두 번 이상 건다 — 어느 것이 이기는지 읽는 사람이 모른다"
    )


def test_the_default_loop_on_this_platform_can_run_psycopg() -> None:
    """🔴 **문서가 아니라 지금 이 프로세스**에서 확인한다 — Windows면 Proactor가 아니어야 한다.

    ⚠ 맥·리눅스에는 `ProactorEventLoop`가 없다 — 그쪽은 확인할 것이 없고, 그 사실을
    `getattr`로 표현한다(플랫폼별 import 분기를 두지 않는다).
    """
    proactor = getattr(asyncio, "ProactorEventLoop", None)
    if proactor is None:
        pytest.skip("이 플랫폼에는 ProactorEventLoop가 없다 — 확인할 대상이 없다")

    async def _loop_type() -> type[asyncio.AbstractEventLoop]:
        return type(asyncio.get_running_loop())

    assert not issubclass(asyncio.run(_loop_type()), proactor), (
        "기본 루프가 Proactor다 — 상담·probe·문제생성이 요청마다 InterfaceError로 500이 된다"
    )


@pytest.mark.parametrize(
    ("platform", "expected_selector"),
    [("win32", True), ("darwin", False), ("linux", False)],
)
def test_the_loop_factory_is_windows_only(
    monkeypatch: pytest.MonkeyPatch, platform: str, expected_selector: bool
) -> None:
    """🔴 **Windows에서만** 루프를 갈아 끼우는지 — 그 OS 없이 값으로 확인한다.

    ⚠ 이 검사가 없으면 *"Windows에서 고쳤다"* 를 **Windows에서만** 확인할 수 있고,
    macOS 경로를 실수로 바꿔도 여기서는 안 보인다.
    """
    monkeypatch.setattr(sys, "platform", platform)
    factory = checkpoint_loop_factory()
    if expected_selector:
        assert factory is asyncio.SelectorEventLoop
    else:
        assert factory is None, f"{platform}에서 루프를 갈아 끼웠다 — 기존 경로가 바뀐다"


def test_the_windows_call_form_is_actually_supported() -> None:
    """🔴 **Windows 분기의 호출 형태가 유효한가** — 아니면 그쪽만 `TypeError`가 난다.

    ⚠ Windows가 없는 기기에서 이 분기를 **한 번도 실행하지 않고** 머지하면,
    고쳤다고 믿은 자리에서 **다른 예외**가 난다. `loop_factory`는 Python 3.12에서
    `asyncio.run`에 들어왔다 — **그 사실을 값으로** 확인한다(POSIX에도 `SelectorEventLoop`가
    있어 호출 자체는 여기서도 돈다).
    """
    assert "loop_factory" in inspect.signature(asyncio.run).parameters, (
        "이 파이썬의 asyncio.run은 loop_factory를 안 받는다 — Windows 분기가 TypeError다"
    )

    async def _noop() -> str:
        return "ok"

    assert asyncio.run(_noop(), loop_factory=asyncio.SelectorEventLoop) == "ok"


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_the_runner_returns_the_coroutine_result_on_every_platform(
    monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    """🔴 **두 분기가 다 실제로 돈다** — 한쪽만 돌면 반대 OS에서 처음 터진다."""
    monkeypatch.setattr(sys, "platform", platform)

    async def _answer() -> int:
        await asyncio.sleep(0)
        return 42

    assert run_with_checkpoint_loop(_answer()) == 42


def test_the_deploy_entrypoint_does_not_call_bare_asyncio_run() -> None:
    """🔴 **배포 명령이 루프 분기를 우회하지 않는지** 원본으로 확인한다.

    ⚠ 값 검사만 두면 `main()`이 다시 `asyncio.run(...)`으로 돌아가도 안 보인다 —
    그게 이번 결함의 형태였다(분기는 있었는데 **배포 명령이 안 썼다**).
    """
    from ai.agents import checkpointer  # noqa: PLC0415 — 원본을 읽으려고 모듈을 잡는다

    source = inspect.getsource(checkpointer.main)
    assert "run_with_checkpoint_loop(" in source, (
        "배포 진입점이 루프 분기를 안 쓴다 — Windows에서 InterfaceError로 exit 1이 된다"
    )
    assert "asyncio.run(" not in source, (
        "배포 진입점이 asyncio.run을 직접 부른다 — Windows 기본 루프로 떨어진다"
    )
