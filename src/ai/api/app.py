"""FastAPI 앱 팩토리 · 예외 핸들러 — 공통 계약 (⚠ B 확인 대기, 02_ownership §5 v3).

DomainException(runtime/errors.py, error_codes §4)을 code·http_status로 매핑해
error envelope로 응답한다. 미분류 예외는 500 INTERNAL(내부 상세 응답 미포함 — 로그만).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from ai.api.envelope import error_envelope
from ai.api.routers.detect import detection_versions
from ai.api.routers.detect import router as detect_router
from ai.runtime.errors import DomainException


def create_app() -> FastAPI:
    """앱을 조립한다 — 라우터 등록 + 예외 핸들러 + X-Request-Id echo."""
    app = FastAPI(title="체크온 AI 서비스", version="0.1.0")
    app.include_router(detect_router)

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
    async def _domain_handler(_request: Request, exc: DomainException) -> JSONResponse:
        # 실패에도 meta.versions를 싣는다(04 §2.2 A판정) — 정적 엔드포인트 버전.
        return JSONResponse(
            status_code=exc.http_status,
            content=error_envelope(exc.code, exc.message, exc.detail, detection_versions()),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, _exc: Exception) -> JSONResponse:
        # 미분류 예외 — 내부 상세를 응답에 싣지 않는다(로그만). error_codes §1: 500 INTERNAL.
        return JSONResponse(
            status_code=500,
            content=error_envelope("INTERNAL", "내부 서버 오류", versions=detection_versions()),
        )

    return app


app = create_app()
