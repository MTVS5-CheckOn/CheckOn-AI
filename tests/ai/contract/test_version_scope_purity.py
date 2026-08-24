"""🔴 `VERSION_SCOPE.versions()`는 **순수**해야 한다 — 실패 응답 경로의 I/O 금지 (99 ㊤).

`api/app.py`의 두 예외 핸들러가 `resolve_versions(request.url.path, …)`를 부르고, 그건
`matched.versions()`를 **`try` 없이** 호출한다(`version_scope.py`). ⇒ 그 팩토리가 던지면
**예외 핸들러 안에서 예외가 나** envelope 없는 500이 된다. `_unhandled`는 `Exception`
핸들러라 **그 아래가 없다.**

04 §2.2 A판정(7/22)이 이미 규정했다 — *"실행 전 오류의 조립 규칙은 엔드포인트가 아는
**정적 앱 버전**"*. **정적**이라는 말이 파일·네트워크·DB를 배제한다. 이 가드는 그 판정의
집행이다.

## 검사 방법 판정 (8/8)

후보 셋을 재고 **ⓐ 런타임 차단**을 골랐다:

  ⓐ `open`·`Path.read_text`·`socket` 등을 막고 각 `versions()`를 실제로 부른다
     ✅ **실행 경로 전체**를 보고 **간접 호출도 잡는다**
     ⚠ 막는 목록이 **목록형**이다 — 아래 한계 참조
  ⓑ AST 정적 검사(`load_*`·`open`·`read_*` 호출 이름)
     ❌ **한 겹 안의 간접 호출을 못 본다.** 실제로 그 형태다 —
        `problem_failure_versions()`는 `load_verify_config()`를 부르고 파일 읽기는
        **그 안**에 있다. AST로는 `load_verify_config` 이름만 보이고 그게 I/O인지 모른다
  ⓒ 두 번 불러 같은 객체인지 → 캐시 여부만 보고 순수성은 못 본다

🔴 **ⓐ의 한계를 여기 명시한다(로그 67을 여기서 반복하지 않으려면).** 아래 `_BLOCKED`는
**목록**이고, 목록에 없는 I/O(예: `os.environ` 읽기·서브프로세스·C 확장의 직접 syscall)는
**못 막는다.** 목록을 늘리는 것이 답이 아니라, *"이 가드가 잡는 것은 파일·소켓까지"* 를
알고 쓰는 것이 답이다. 전칭이 불가능한 대상이라 목록형을 **의식적으로** 골랐다.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any, Final

import pytest

from ai.api.app import ROUTER_VERSION_SCOPES
from ai.api.version_scope import RouterScope
from ai.contracts.execution import VersionSet


class _ForbiddenIo(RuntimeError):
    """`versions()`가 I/O를 탔다 — 실패 응답 경로에서 금지된다."""


#: 막는 것 — `(모듈 또는 클래스, 속성명)`. ⚠ **목록형이다**(위 docstring 한계 참조).
_BLOCKED: Final[tuple[tuple[Any, str], ...]] = (
    (Path, "read_text"),
    (Path, "read_bytes"),
    (Path, "open"),
    (socket, "socket"),
    (socket, "create_connection"),
)


def _install_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """I/O 진입점을 전부 `_ForbiddenIo`로 바꾼다."""

    def _boom(*args: object, **kwargs: object) -> object:
        raise _ForbiddenIo("실패 응답 경로에서 I/O를 탔다")

    monkeypatch.setattr("builtins.open", _boom)
    for target, name in _BLOCKED:
        monkeypatch.setattr(target, name, _boom)


#: 🔴 **B 수정 대기 중인 스코프**(99 ㊤). `xfail(strict=True)`이지 **화이트리스트가 아니다** —
#: strict라 **초록이 되는 순간 red**가 나므로 B의 모듈 상수화가 머지되면 **자동으로 검출**되고
#: 그때 이 줄을 지우면 된다. 목록에 없는 스코프가 실패하면 **그대로 red**다.
#: ⚠ 화이트리스트(#119·#125에서 금지한 것)와의 차이가 그 자동 검출이다 — 화이트리스트는
#: 영원히 조용하지만 이건 **고쳐지면 시끄럽다.**
_PENDING_B_FIX: Final[frozenset[str]] = frozenset()

_PENDING_REASON: Final = (
    "B의 pg 라우터가 problem_failure_versions()에서 load_verify_config()를 부른다"
    "(매 호출 파일 읽기 + YAML + Pydantic). B가 자기 버그로 인정했고 모듈 상수화로 "
    "고치기로 했다(99 ㊤). 🔴 strict=True다 — 그 수정이 오면 이 xfail이 red가 되고, "
    "그때 _PENDING_B_FIX에서 이 접두를 지운다."
)


def _scope_params(*, mark_pending: bool = False) -> list[Any]:
    """스코프별 파라미터.

    ⚠ `mark_pending`은 **순수성 테스트에만** 준다 — B 수정 대기분이 실패하는 것은 *I/O를
    탄다* 는 축뿐이고, 시그니처 축(`인자 없이 부를 수 있다`)은 **지금도 통과**한다.
    거기까지 `xfail`을 붙이면 `strict=True`가 **XPASS로 red**를 낸다(8/8에 실제로 그랬다).
    """
    return [
        pytest.param(
            scope,
            id=scope.prefix.replace("/", "_"),
            marks=(
                [pytest.mark.xfail(strict=True, reason=_PENDING_REASON)]
                if mark_pending and scope.prefix in _PENDING_B_FIX
                else []
            ),
        )
        for scope in ROUTER_VERSION_SCOPES
    ]


def test_the_scan_finds_scopes() -> None:
    """🔴 **검사 경로가 끊기면 통과가 아니라 실패다.**

    `ROUTER_VERSION_SCOPES`가 비었으면 *"위반이 없다"* 가 아니라 *"안 봤다"* 다.
    """
    assert ROUTER_VERSION_SCOPES, (
        "ROUTER_VERSION_SCOPES가 비었다 — 위반이 없는 게 아니라 검사가 끊긴 것이다"
    )


@pytest.mark.parametrize("scope", _scope_params(mark_pending=True))
def test_version_scope_factories_are_pure(
    scope: RouterScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 각 `versions()`가 파일·소켓을 타지 않고 `VersionSet`을 만든다.

    타면 **예외 핸들러 안에서 예외가 나** envelope 없는 500이 된다 — `_unhandled`는
    `Exception` 핸들러라 그 아래가 없다.

    고치는 법: 팩토리가 읽는 값을 **모듈 상수**로 올린다(import 시 1회). 실패 응답에
    실리는 것은 *"엔드포인트가 아는 정적 앱 버전"* 이지 실행 시점의 config가 아니다.
    """
    _install_blocks(monkeypatch)
    try:
        versions = scope.versions()
    except _ForbiddenIo as exc:
        pytest.fail(
            f"{scope.prefix}의 versions()가 I/O를 탄다({exc}) — 실패 응답 경로다. "
            "그 I/O가 던지면 예외 핸들러 안에서 예외가 나 envelope 없는 500이 된다"
            "(_unhandled는 Exception 핸들러라 그 아래가 없다 · 04 §2.2 '정적 앱 버전'). "
            "고치는 법: 읽는 값을 모듈 상수로 올린다(import 시 1회)."
        )
    assert isinstance(versions, VersionSet)


@pytest.mark.parametrize("scope", _scope_params())
def test_version_scope_factories_take_no_arguments(scope: RouterScope) -> None:
    """⚠ `resolve_versions`가 **인자 없이** 부른다 — 시그니처를 계약으로 잠근다.

    기본값이 있는 인자는 괜찮다(`detection_versions(config=None)`가 그 형태다).
    """
    scope.versions()  # 인자 없이 불려도 터지지 않는다

def test_the_sweep_has_something_to_sweep() -> None:
    """🔴 **순회 대상이 0이면 이 파일의 검사들이 「조용히 사라진다」**(99 #202 · №88 실측).

    `parametrize` 에 빈 목록이 가면 red 도 skip 도 아니고 **collect 조차 안 된다** —
    실측(2026-08-24): 이 파일을 비웠더니 `23 passed` 가 `12 passed` 가 됐고 **아무도 안 빨개졌다.**

    🔴 **`N` 이 아니라 「0이 아니다」로 문다** — 이 목록은 **늘어나는 것**이라 `N` 을 박으면
    항목이 생길 때마다 그 수를 고쳐야 한다(99 #211 ⓐ 의 반대편). «수가 줄면 알아차린다»가
    목적인 자리(코퍼스·픽스처 총수)와는 **다르다** — 여기 목적은 «**사라지면 알아차린다**» 다.
    """
    assert _scope_params(), "순회 대상이 0이다 — 위 검사들이 조용히 사라졌다"
