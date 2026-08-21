"""FastAPI 앱 팩토리 · 예외 핸들러 — 공통 계약 (A+B 확인 완료, 02_ownership §5 v3).

DomainException(runtime/errors.py, error_codes §4)을 code·http_status로 매핑해
error envelope로 응답한다. 미분류 예외는 500 INTERNAL(내부 상세 응답 미포함 — 로그만).

민감 detail은 예외 클래스의 노출 정책(`DomainException.detail_is_exposed()`)에 따라
응답에서 제거된다 — 04 §2.3 · error_codes §4. 판정은 여기서 하지 않는다(예외 정의 옆).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable, Iterator
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from ai.api.console import install_console_handlers
from ai.api.envelope import error_envelope
from ai.api.routers.classify import VERSION_SCOPE as _classify_scope
from ai.api.routers.classify import router as classify_router
from ai.api.routers.confirmations import VERSION_SCOPE as _confirmations_scope
from ai.api.routers.confirmations import router as confirmations_router
from ai.api.routers.counsel import VERSION_SCOPE as _counsel_scope
from ai.api.routers.counsel import router as counsel_router
from ai.api.routers.detect import VERSION_SCOPE as _detect_scope
from ai.api.routers.detect import router as detect_router
from ai.api.routers.diagnosis import VERSION_SCOPE as _diagnosis_scope
from ai.api.routers.diagnosis import router as diagnosis_router
from ai.api.routers.ops import VERSION_SCOPE as OPS_VERSION_SCOPES
from ai.api.routers.ops import router as ops_router
from ai.api.routers.problem import VERSION_SCOPE as _problems_scope
from ai.api.routers.problem import router as problem_router
from ai.api.version_scope import RouterScope, resolve_versions
from ai.runtime.errors import DomainException

logger = logging.getLogger(__name__)

#: ⚠ 양자 승인 파일 수정(실패 응답 버전 스코프 · 99 ㊓) — 라우터 등록과 같은 취급, B 리뷰.
#: 🔴 **접두 문자열은 여기 없다** — 각 라우터 모듈의 `VERSION_SCOPE`가 갖는다(경로를 바꾸는
#:  사람과 접두를 고치는 사람이 같아야 한다). 여기는 **등록과 스코프를 한 자리에 묶기만** 한다.
#: ⚠ 라우터를 include하면서 여기 스코프를 빠뜨리면 그 엔드포인트의 실패 응답이 **남의 버전을
#:  단다** — `tests/ai/contract/test_version_scope_registry.py`가 잡는다.
ROUTER_VERSION_SCOPES: tuple[RouterScope, ...] = (
    _detect_scope,
    _counsel_scope,
    _classify_scope,
    _confirmations_scope,
    _problems_scope,
    _diagnosis_scope,
    *OPS_VERSION_SCOPES,
)


# ⚠ 양자 승인 파일 수정(openapi 후처리) — 라우터 등록 선례, B 리뷰
# 🔴 왜: FastAPI 가 path 파라미터가 있으면 `422` 를 **자동 주입**한다
#     (`fastapi/openapi/utils.py` — 응답에 `422`·`4XX`·`default` 가 **없을 때만** 넣는다
#     [읽음 8/22]).
#     `job_id` 는 `str` 이라 **어떤 값도 유효**하다 ⇒ **도달 불가**인데 문서에 남아
#     BE codegen 이 **못 오는 핸들러**를 만든다(99 #105 · 배포 openapi 로 확인됨).
# 🔴 왜 여기: `openapi_extra` 는 deep-merge 라 **키를 못 지운다.** 선언에 `422` 를 넣어 맞추는 것은
#     «도달 불가를 문서화» 하는 것이라 처방이 아니고, `"4XX"`·`"default"` 를 넣으면 codegen 이
#     **다른 핸들러**를 만든다. ⇒ 문서 후처리가 유일한 길이다.
# ⚠ 조건: path 파라미터가 **전부 `str`** 인 경로에서만 뗀다.
#     🔴 `/v1/problems/{set_id}/items/{slot_index}` 계열은 `slot_index: int` 라
#     **`422` 가 실제로 도달한다** — 전역 제거는 틀린다(실측 8/22: 자동 422 8곳 중 2곳).
# 🔴 경로를 하드코딩하지 않는다 — 목록을 손으로 적으면 **새 경로가 생길 때 아무도 안 운다**
#     (로그 149·173·175·178 이 네 번 잡은 형태다). 판정은 **규칙**이다.
def _api_routes(routes: Iterable[Any]) -> Iterator[APIRoute]:
    """등록된 `APIRoute` 를 전부 훑는다 — 중첩 라우터를 따라 내려간다.

    ⚠ FastAPI 0.139 는 `include_router` 한 것을 `_IncludedRouter` 로 감싸 두고 실제 라우트는
    `original_router.routes` 에 있다 [읽음]. 버전이 바뀌어도 깨지지 않게 **두 경로를 다 본다** —
    🔴 못 찾으면 조용히 0건이 되어 **후처리가 아무것도 안 하고 통과**한다. 그 상태는
    `tests/ai/contract/test_unreachable_422_is_not_documented.py` 가 red 로 잡는다.
    """
    for route in routes or ():
        if isinstance(route, APIRoute):
            yield route
        nested = getattr(route, "routes", None) or getattr(
            getattr(route, "original_router", None), "routes", None
        )
        if nested:
            yield from _api_routes(nested)


def _install_unreachable_422_removal(app: FastAPI) -> None:
    """도달 불가 `422` 를 openapi 문서에서 뗀다(99 #105).

    🔴 **판정 정보를 「생성된 스키마」가 아니라 「라우트 객체」에서 얻는다** — 스키마의
    `parameters` 는 `openapi_extra` 가 덮을 수 있어 **문서가 거짓말을 하면 그 거짓을 믿게 된다.**
    라우트의 `field_info.annotation` 은 **실제 파이썬 시그니처**라 도달 가능성의 정본이다.
    ⚠ 라우터가 `422` 를 **스스로 선언**했으면 손대지 않는다 — 그건 우리가 넣은 것이 아니다.
    """
    original_openapi = app.openapi

    def openapi_without_unreachable_validation_errors() -> dict[str, Any]:
        schema = original_openapi()
        paths: dict[str, Any] = schema.get("paths", {})
        for route in _api_routes(app.routes):
            params = route.dependant.path_params
            if not params:
                continue
            if any(param.field_info.annotation is not str for param in params):
                continue
            if any(str(code) == "422" for code in route.responses):
                continue
            operations: dict[str, Any] = paths.get(route.path_format, {})
            for method in route.methods or ():
                operations.get(method.lower(), {}).get("responses", {}).pop("422", None)
        return schema

    app.openapi = openapi_without_unreachable_validation_errors  # type: ignore[method-assign]


def create_app() -> FastAPI:
    """앱을 조립한다 — 라우터 등록 + 예외 핸들러 + X-Request-Id echo."""
    app = FastAPI(title="체크온 AI 서비스", version="0.1.0")
    app.include_router(detect_router)
    # ⚠ 양자 승인 파일 수정(라우터 등록 **해제**) — 위 등록 선례와 같은 형식, B 리뷰
    # 🔴 `imports_router` 는 **지웠다**(2026-08-22 · import 축 개발 중단 · 99 #187).
    #    승우님 확인(«개발 안 해서 삭제해도 상관 없다») · B 축 참조 0건(준영님 실측).
    #    ⚠ `WorkerKind.MAPPING_PROBE` 등 **원장 축 여덟 자리는 남겼다** — 실행 기록이라
    #    지우면 «그 실행이 없었다» 가 된다(불변식 8). `contracts/agents.py` 주석 참조.
    app.include_router(counsel_router)  # ⚠ 양자 승인 파일 수정(라우터 등록) — 위와 동일, B 리뷰
    app.include_router(classify_router)  # ⚠ 양자 승인 파일 수정(라우터 등록) — 위와 동일, B 리뷰
    # ⚠ 양자 승인 파일 수정(라우터 등록) — 위와 동일, B 리뷰
    app.include_router(confirmations_router)
    # ⚠ 양자 승인 파일 수정(라우터 등록) — counsel 선례, A 리뷰
    app.include_router(problem_router)
    # ⚠ 양자 승인 파일 수정(라우터 등록) — problem 선례, A 리뷰. B 소유 diagnosis 라우터
    #    (Step 1 area×type 그리드 · 2026-08-12 신설)
    app.include_router(diagnosis_router)
    app.include_router(ops_router)

    @app.middleware("http")
    async def _echo_request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # X-Request-Id 응답 echo·로그 correlation — 공통 계층 (04 §2 A판정 7/22).
        response = await call_next(request)
        request_id = request.headers.get("X-Request-Id")
        if request_id:
            response.headers["X-Request-Id"] = request_id
        return response

    @app.exception_handler(DomainException)
    async def _domain_handler(request: Request, exc: DomainException) -> JSONResponse:
        # 민감 detail 강제 제거(error_codes §4 · 04 §2.3) — 5xx의 detail은 마스킹 실패
        # 원문 조각·내부 상세일 수 있어 응답에서 뺀다. 4xx(필드 경로 등)는 그대로 싣는다.
        detail = exc.detail if exc.detail_is_exposed() else None
        if exc.detail is not None and detail is None:
            # **응답에서만 빼는 것** — 디버깅 경로는 살린다(X-Request-Id correlation).
            logger.warning(
                "실패 detail 응답 미노출 code=%s status=%d request_id=%s detail=%r",
                exc.code,
                exc.http_status,
                request.headers.get("X-Request-Id"),
                exc.detail,
            )
        # 실패에도 meta.versions를 싣는다(04 §2.2 A판정) — **그 엔드포인트의** 정적 버전.
        return JSONResponse(
            status_code=exc.http_status,
            content=error_envelope(
                exc.code,
                exc.message,
                detail,
                resolve_versions(request.url.path, ROUTER_VERSION_SCOPES),
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, _exc: Exception) -> JSONResponse:
        # 미분류 예외 — 내부 상세를 응답에 싣지 않는다(로그만). error_codes §1: 500 INTERNAL.
        # 🔴 여기도 요청 경로로 고른다 — 이쪽이 더 빠지기 쉽고, 하필 가장 급할 때 원장이
        #    엉뚱한 엔진을 가리키게 된다(99 ㊓).
        return JSONResponse(
            status_code=500,
            content=error_envelope(
                "INTERNAL",
                "내부 서버 오류",
                versions=resolve_versions(request.url.path, ROUTER_VERSION_SCOPES),
            ),
        )

    install_console_handlers(app)  # 콘솔 관측(PR-ψ) — 기본 전부 꺼짐, 본문은 api/console.py
    _install_unreachable_422_removal(app)  # ⚠ 양자 승인 파일 수정 — 위 주석, B 리뷰

    return app


app = create_app()
