"""FastAPI 앱 팩토리 · 예외 핸들러 — 공통 계약 (A+B 확인 완료, 02_ownership §5 v3).

DomainException(runtime/errors.py, error_codes §4)을 code·http_status로 매핑해
error envelope로 응답한다. 미분류 예외는 500 INTERNAL(내부 상세 응답 미포함 — 로그만).

민감 detail은 예외 클래스의 노출 정책(`DomainException.detail_is_exposed()`)에 따라
응답에서 제거된다 — 04 §2.3 · error_codes §4. 판정은 여기서 하지 않는다(예외 정의 옆).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

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
from ai.api.routers.imports import VERSION_SCOPE as _imports_scope
from ai.api.routers.imports import router as imports_router
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
    _imports_scope,
    _counsel_scope,
    _classify_scope,
    _confirmations_scope,
    _problems_scope,
    _diagnosis_scope,
    *OPS_VERSION_SCOPES,
)


def create_app() -> FastAPI:
    """앱을 조립한다 — 라우터 등록 + 예외 핸들러 + X-Request-Id echo."""
    app = FastAPI(title="체크온 AI 서비스", version="0.1.0")
    app.include_router(detect_router)
    app.include_router(imports_router)  # ⚠ 양자 승인 파일 수정(라우터 등록) — detect 선례, B 리뷰
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

    return app


app = create_app()
