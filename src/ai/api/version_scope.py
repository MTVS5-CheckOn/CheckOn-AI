"""실패 응답의 버전 스코프 — **라우터가 자기 접두를 소유한다** (99 ㊓).

소유: 박진희 (api 라우터 계층 · **양자 승인 파일 아님**). `api/app.py`가 이 모듈의
`resolve_versions()`를 불러 실패 응답의 `meta.versions`를 고른다.

🔴 **경로 문자열이 어디 사는가가 이 설계의 전부다.** `app.py`에 박으면 라우터가 경로를
바꿀 때 조용히 갈린다 — 경로를 바꾸는 사람과 접두를 고치는 사람이 **다른 파일을 연다**.
그래서 접두는 각 라우터 모듈의 `VERSION_SCOPE`에 살고, `app.py`는 그것을 **모으기만** 한다.

⚠ **`app.py`는 양자 승인 파일**이라 diff를 최소로 유지해야 한다는 제약도 같은 방향이다 —
라우터가 늘 때마다 양자 파일을 다시 열지 않으려면 조립 로직이 여기 있어야 한다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Final, NamedTuple

from ai.contracts.execution import VersionSet

logger = logging.getLogger(__name__)


class RouterScope(NamedTuple):
    """라우터 하나의 경로 접두와 버전 세트 팩토리.

    `versions`는 **인자 없이** 부를 수 있어야 한다 — 실패 응답은 실행 config가 확정되기
    전에도 나가므로(헤더 누락 등) 정적 버전만으로 조립된다(04 §2.2 A판정).
    """

    prefix: str
    versions: Callable[[], VersionSet]


#: 🔴 **스코프에 안 걸린 요청의 버전 세트.**
#:
#: ⚠ **도달 경로가 하나뿐이다(8/8 실측):** *"라우터를 등록했는데 `VERSION_SCOPE`를 빠뜨린"*
#: 경우다. **없는 경로(`/v1/nope`)는 여기 안 온다** — Starlette가 라우트 매칭 전에 자기
#: 404(`{"detail": "Not Found"}`)를 내고 우리 핸들러를 아예 안 탄다(99 ㊜). 즉 이 폴백은
#: 등록 누락 전용 안전망이고, 계약 가드가 CI에서 먼저 잡는다.
#:
#: ⚠ **`detection_versions()`로 폴백하지 않는다** — 그게 ㊓의 결함 그 자체다. 감지와
#: 무관한 요청에 감지 엔진·임계 시트 버전을 다는 것은 거짓말이고, 특히 `threshold`는
#: *"그 실행이 임계 v3를 썼다"* 는 **없는 사실**을 만든다.
#:
#: ⚠ **`engine`을 무엇으로 적을지는 미확정이다**(99 ㊙). `VersionSet`의 네 필드가 필수라
#: 비울 수 없어 앱 레벨 토큰 `"app-0.1"`을 임시로 쓴다 — *"엔진이 관여하지 않은 응답"* 을
#: 뜻하는 명시 토큰이며, 빈 문자열은 *"못 받았다"* 와 구분되지 않아 쓰지 않는다
#: (`run_store.UNKNOWN_MODEL`과 같은 판단).
_APP_ENGINE: Final = "app-0.1"

FALLBACK_VERSIONS: Final = VersionSet(
    pipeline_version="0.1.0",
    engine_version=_APP_ENGINE,
    schema_version="0.1",
    contract_version="0.1",
)


def resolve_versions(path: str, scopes: tuple[RouterScope, ...]) -> VersionSet:
    """요청 경로가 속한 라우터의 버전 세트 — 못 찾으면 앱 기본값 + **경고**.

    🔴 **가장 긴 접두가 이긴다.** 지금은 겹침이 없지만 `/v1/counsel`과
    `/v1/counsel/labels` 같은 게 생기면 짧은 쪽이 먼저 잡혀 조용히 틀린다.

    🔴 **폴백에 경고를 남기는 이유** — 등록을 빠뜨린 라우터가 생기면 **이 로그가 유일한
    신호**다(로그 59: *"관측 장치를 만들 때 읽는 자리를 같이 만들지 않으면 없는 것과
    같다"*). 계약 가드가 CI에서 먼저 잡지만, 가드를 우회하는 경로(동적 등록 등)가 생기면
    런타임에서는 이것뿐이다.
    """
    matched = max(
        (scope for scope in scopes if path.startswith(scope.prefix)),
        key=lambda scope: len(scope.prefix),
        default=None,
    )
    if matched is None:
        logger.warning(
            "버전 스코프 미등록 경로 path=%r — 앱 기본 버전으로 응답한다(engine=%s). "
            "라우터가 새로 붙었다면 그 모듈에 VERSION_SCOPE를 노출하고 app.py의 "
            "ROUTER_VERSION_SCOPES에 추가하라(99 ㊓).",
            path,
            _APP_ENGINE,
        )
        return FALLBACK_VERSIONS
    return matched.versions()


__all__ = ["FALLBACK_VERSIONS", "RouterScope", "resolve_versions"]
