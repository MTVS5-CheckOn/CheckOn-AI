"""🔴 등록된 라우터는 **자기 `VERSION_SCOPE`** 를 갖는다 (99 ㊓).

레지스트리를 고른 이유의 절반이 이 파일이다. 최소안(`app.py`에 경로 문자열 박기)은 **새
라우터가 생기면 조용히 빠진다** — 그 엔드포인트의 실패 응답이 남의 버전을 달고 나가는데
아무도 모른다. 같은 형태를 이 저장소가 여덟 번 겪었다(#108·#111·#114·#116·#117·#119·
#120·#125).

⚠ **화이트리스트 없음.** 예외를 허용하기 시작하면 목록이 사고를 숨긴다(#119·#125와 같은
판단).

⚠ **경로 계산은 레포 관용구**(`Path(__file__).resolve().parents[3]`)다 — #125에서 이것만
상대 경로라 CWD가 다르면 거짓 red가 났다.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any, Final

from ai.api import app as app_module
from ai.api.app import ROUTER_VERSION_SCOPES, create_app

_ROUTERS_DIR: Final = (
    Path(__file__).resolve().parents[3] / "src" / "ai" / "api" / "routers"
)


def registered_v1_paths(app: Any) -> set[str]:  # noqa: ANN401 — FastAPI
    """앱에 **실제로 등록된** `/v1/*` 경로 전수.

    ⚠ FastAPI 0.139는 `include_router`를 `_IncludedRouter`로 감싸 **라우트를 평탄화하지
    않는다** — `app.routes`를 그냥 훑으면 `/openapi.json` 같은 기본 경로만 나오고, 그러면
    이 가드가 *"검사할 게 없다"* 로 **조용히 통과**한다(8/8에 실제로 그럴 뻔했다).
    """
    paths: set[str] = set()
    for route in app.routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            paths |= {getattr(sub, "path", "") for sub in included.routes}
        elif hasattr(route, "path"):
            paths.add(route.path)
    return {path for path in paths if path.startswith("/v1")}


def test_the_scan_finds_registered_routes() -> None:
    """🔴 **검사 경로가 끊기면 통과가 아니라 실패다.**

    등록 경로를 하나도 못 찾으면 *"위반이 없다"* 가 아니라 *"안 봤다"* 다 — FastAPI가
    라우트 저장 방식을 바꾸면 조용히 그렇게 된다.
    """
    paths = registered_v1_paths(create_app())
    assert paths, (
        "앱에서 /v1 경로를 하나도 찾지 못했다 — 위반이 없는 게 아니라 검사가 끊긴 것이다. "
        "FastAPI가 include_router의 라우트 보관 방식을 바꿨는지 확인하라"
    )


def test_every_registered_route_has_a_version_scope() -> None:
    """🔴 등록된 모든 `/v1` 경로가 `ROUTER_VERSION_SCOPES`의 어느 접두에든 걸린다.

    걸리지 않으면 그 엔드포인트의 **실패 응답이 앱 기본값**(`engine="app-0.1"`)으로
    나간다 — 성공은 자기 버전을 다는데 실패만 다르다. 그게 ㊓ 그 자체다.
    """
    prefixes = tuple(scope.prefix for scope in ROUTER_VERSION_SCOPES)
    orphans = sorted(
        path
        for path in registered_v1_paths(create_app())
        if not path.startswith(prefixes)
    )
    assert not orphans, (
        "버전 스코프가 없는 등록 경로가 있다:\n  " + "\n  ".join(orphans) + "\n\n"
        "그 엔드포인트의 **실패 응답이 남의 버전(또는 앱 기본값)을 단다**(99 ㊓) — "
        "성공은 자기 것을 다는데 실패만 다르면 BE가 어느 쪽을 믿어야 할지 모른다.\n"
        "고치는 법:\n"
        "  ① 그 라우터 모듈에 VERSION_SCOPE = RouterScope('/v1/<접두>', <자기>_versions)\n"
        "     를 노출한다\n"
        "  ② `api/app.py`의 `ROUTER_VERSION_SCOPES`에 추가\n"
        "     (⚠ app.py는 양자 승인 파일이다 — 등록 1줄과 같은 취급으로 표기하고 리뷰 요청)"
    )


def test_version_scopes_do_not_collide() -> None:
    """⚠ 접두가 서로를 **포함하지 않는다** — longest-prefix에 기대지 않는다.

    `resolve_versions`는 가장 긴 접두를 고르므로 포함 관계가 있어도 **동작은 한다.**
    그래도 막는 이유: 포함 관계가 생기는 순간 *"어느 쪽이 이기는가"* 를 읽는 사람이
    구현을 봐야 알 수 있고, 그건 계약이 아니라 우연이다. 정말 필요해지면 이 단정을
    지우는 게 아니라 **의도를 명시한 테스트로 바꾼다**.
    """
    prefixes = [scope.prefix for scope in ROUTER_VERSION_SCOPES]
    assert len(prefixes) == len(set(prefixes)), f"접두 중복: {prefixes}"
    nested = [
        (outer, inner)
        for outer in prefixes
        for inner in prefixes
        if outer != inner and inner.startswith(outer)
    ]
    assert not nested, (
        f"접두가 서로를 포함한다: {nested} — longest-prefix가 동작은 하지만 어느 쪽이 "
        "이기는지가 구현에만 있다. 의도라면 이 단정을 그 의도로 바꿔라"
    )


def test_every_router_module_declares_a_scope() -> None:
    """⚠ **라우터 파일 쪽에서도** 센다 — `app.py`에만 있으면 등록을 빠뜨려도 안 보인다.

    위 테스트는 *"등록된 것"* 을 보고 이 테스트는 *"있는 것"* 을 본다. 라우터 모듈을
    만들어 놓고 `include_router`를 빠뜨리면 위 테스트는 통과하고 이 테스트가 잡는다.
    """
    modules = sorted(
        path.stem
        for path in _ROUTERS_DIR.glob("*.py")
        if path.stem != "__init__" and "APIRouter()" in path.read_text(encoding="utf-8")
    )
    assert modules, f"{_ROUTERS_DIR}에서 라우터 모듈을 찾지 못했다 — 검사가 끊겼다"

    missing = [
        name
        for name in modules
        if "VERSION_SCOPE" not in (_ROUTERS_DIR / f"{name}.py").read_text(encoding="utf-8")
    ]
    assert not missing, (
        f"VERSION_SCOPE를 노출하지 않는 라우터 모듈: {missing}\n"
        "각 라우터가 자기 접두를 소유해야 한다 — 경로를 바꾸는 사람과 접두를 고치는 "
        "사람이 같아야 하기 때문이다(99 ㊓)"
    )


def test_the_handlers_do_not_hardcode_one_capability() -> None:
    """🔴 `app.py`가 특정 capability의 `*_versions()`를 **직접 부르지 않는다**.

    이게 ㊓의 원형이다 — `detection_versions()`가 두 핸들러에 박혀 있었다. AST로 본다:
    핸들러 안에서 `resolve_versions` 말고 다른 `*_versions` 호출이 있으면 red다.
    """
    tree = ast.parse(Path(inspect.getfile(app_module)).read_text(encoding="utf-8"))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    hardcoded = sorted(
        name for name in called if name.endswith("_versions") and name != "resolve_versions"
    )
    assert not hardcoded, (
        f"app.py가 capability별 버전 함수를 직접 부른다: {hardcoded} — 그 엔드포인트가 "
        "아닌 응답에도 그 버전이 실린다(99 ㊓). resolve_versions(request.url.path, …)를 쓰라"
    )
