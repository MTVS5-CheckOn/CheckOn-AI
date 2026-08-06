"""🔴 counsel 기동 가드가 **startup 훅에 실려 있다**는 전제를 잠근다 (99 ㉨).

`api/app.py`는 양자 승인 파일이라 counsel 라우터가 **자기 기동 가드를 직접 들고 갔다**
(`counsel.py:257` `router.add_event_handler("startup", _startup)`). `include_router`가 그
훅을 앱으로 옮겨 주는 덕에 `create_app()`만으로 미배선이 걸린다.

⚠ **그 다리가 하나뿐이고, 이미 반쯤 끊겼다.** 실측(8/7 · fastapi 0.139 · starlette 1.3):
`FastAPI` 객체에는 **`add_event_handler`가 아예 없다**(`AttributeError`) — `APIRouter`에만
남아 있다. 즉 지금 우리가 쓰는 경로가 **마지막 남은 경로**다. `app.py`가
`FastAPI(lifespan=...)`로 전환되면 **라우터의 startup 훅은 더 이상 실행되지 않는다.**
가드가 조용히 죽고 #108이 막은 *"배포하면 Fake가 답한다"* 로 정확히 되돌아간다
— Fake의 `fallback_text`는 숫자·금칙어가 없어 **게이트를 통과**하고 LLM 호출이 0건이라
**원장에도 안 남는다**(사후 구분 불가).

🔴 **A는 `app.py`를 고치지 않는다.** 양자 승인 파일이고 B가 곧 pg 라우터를 등록하러 온다 —
지금 손대면 충돌한다. 대신 `detect.py`에 쓴 것과 같은 처방으로 **전제를 여기서 고정한다**
(#120 · 99 ㉷). 아래 두 번째 테스트가 **본체**다.

⚠ **이 테스트는 lifespan 전환을 막지 않는다.** 전환은 정당하다 — 다만 *"가드를 같이
옮겨라"* 를 red로 알려 줄 뿐이다. 전환하는 사람이 실패 메시지를 읽고 한 줄 옮기면 끝난다.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai.api import app as app_module
from ai.api.app import create_app
from ai.api.routers import counsel as counsel_router
from ai.api.routers.counsel import reset_counsel_stores
from ai.db.store_factory import reset_shared_agent_runtime


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    reset_shared_agent_runtime()  # A·B 공용 잡 원장(99 ㊒)
    reset_counsel_stores()
    yield
    reset_shared_agent_runtime()  # A·B 공용 잡 원장(99 ㊒)
    reset_counsel_stores()


def test_counsel_startup_guard_actually_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """앱을 띄우면 `_startup`이 **실제로** 불린다 — 부작용으로 관측한다.

    ⚠ 미배선 시 기동이 막히는 쪽은 `test_counsel_service_wiring.py`의
    `test_app_does_not_start_when_provider_is_not_wired`가 이미 지킨다. 여기서 겹쳐 보는
    것은 **훅이 도는지** 하나뿐이다 — 아래 두 번째 테스트가 지킬 전제의 반쪽이다.
    """
    called: list[str] = []
    original = counsel_router.bootstrap_counsel_provider

    def _spy() -> None:
        called.append("bootstrap")
        original()

    monkeypatch.setattr(counsel_router, "bootstrap_counsel_provider", _spy)
    with TestClient(create_app()):
        pass

    assert called, (
        "create_app()으로 앱을 띄웠는데 counsel 기동 가드가 돌지 않았다 — "
        "router.add_event_handler('startup', _startup)가 앱으로 옮겨지지 않는다"
    )


def test_the_router_startup_hook_fires_twice_and_the_guard_survives_it() -> None:
    """🔴 **훅이 기동 1회에 2번 돈다**(8/7 실측 · 프레임워크 거동 · 99 신규 등재).

    등록은 1건인데(`len(router.on_startup) == 1`) 호출은 2회다. 바닐라 `APIRouter` +
    `add_event_handler`로도 그대로 재현되므로 **우리 조립 탓이 아니다**(starlette 1.3의
    deprecated 경로 호환 처리로 보인다).

    지금 무해한 이유는 **가드가 우연히 멱등**이라서다 — `bootstrap_counsel_provider`는
    *"이미 배선돼 있으면 덮지 않는다"* 로 시작하고 `require_counsel_provider`는 읽기다.
    ⚠ **다음 사람이 라우터 startup 훅에 멱등하지 않은 부작용**(카운터 증가·큐 적재·연결
    풀 생성)을 넣으면 **조용히 두 번 일어난다.** 그래서 관측 자체를 여기 못 박는다.

    ⚠ 이 테스트는 2를 **요구하지 않는다** — 1이 되면(프레임워크가 고치면) 그것도 정상이라
    통과한다. 지키는 것은 **"몇 번 돌든 배선 결과가 같다"** 다.
    """
    counsel_router.bootstrap_counsel_provider()
    wired = counsel_router._provider
    for _ in range(3):
        counsel_router._startup()
    assert counsel_router._provider is wired, (
        "기동 가드를 반복 호출했더니 배선이 바뀌었다 — 라우터 startup 훅은 기동 1회에 "
        "2번 돈다(8/7 실측). 훅에 넣는 부작용은 반드시 멱등이어야 한다"
    )


def test_app_still_uses_event_handlers_not_lifespan() -> None:
    """🔴 **본체** — `app.py`가 `lifespan=`으로 전환되지 않았다는 전제를 잠근다.

    ⚠ 위 테스트만 있으면 **전환 후에도 초록이 될 수 있다.** 훅이 죽어도 다른 경로로
    `bootstrap`이 불릴 수 있고, 무엇보다 `_startup`을 직접 부르는 테스트는 전환과 무관하게
    통과한다. 지켜야 하는 것은 *"돌았다"* 가 아니라 **"돌게 만드는 메커니즘이 그대로다"** 다.

    AST로 본다 — 주석·문자열 안의 `lifespan`은 위반이 아니고, `FastAPI(...)` 호출의
    **키워드 인자**만이 전제를 깬다.
    """
    source = Path(inspect.getfile(app_module)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "lifespan"
    ]
    assert not offenders, (
        f"api/app.py:{offenders}에 lifespan=이 생겼다 — counsel 라우터의 기동 가드"
        "(counsel.py:257 add_event_handler)가 **더 이상 실행되지 않는다**. "
        "가드를 lifespan 쪽으로 함께 옮겨야 한다 — 안 옮기면 배포 시 Fake가 답한다(#108). "
        "옮길 것: `_startup()` 1회 호출(bootstrap_counsel_provider → require_counsel_provider). "
        "옮긴 뒤 이 테스트의 단정을 '앱 lifespan이 _startup을 부른다'로 바꾸면 된다."
    )
