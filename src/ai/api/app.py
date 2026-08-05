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

from ai.api.envelope import error_envelope
from ai.api.routers.classify import router as classify_router
from ai.api.routers.confirmations import router as confirmations_router
from ai.api.routers.counsel import router as counsel_router
from ai.api.routers.detect import detection_versions
from ai.api.routers.detect import router as detect_router
from ai.api.routers.imports import router as imports_router
from ai.runtime.errors import DomainException

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """앱을 조립한다 — 라우터 등록 + 예외 핸들러 + X-Request-Id echo."""
    app = FastAPI(title="체크온 AI 서비스", version="0.1.0")
    app.include_router(detect_router)
    app.include_router(imports_router)  # ⚠ 양자 승인 파일 수정(라우터 등록) — detect 선례, B 리뷰
    app.include_router(counsel_router)  # ⚠ 양자 승인 파일 수정(라우터 등록) — 위와 동일, B 리뷰
    app.include_router(classify_router)  # ⚠ 양자 승인 파일 수정(라우터 등록) — 위와 동일, B 리뷰
    # ⚠ 양자 승인 파일 수정(라우터 등록) — 위와 동일, B 리뷰
    app.include_router(confirmations_router)

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
        # 실패에도 meta.versions를 싣는다(04 §2.2 A판정) — 정적 엔드포인트 버전.
        return JSONResponse(
            status_code=exc.http_status,
            content=error_envelope(exc.code, exc.message, detail, detection_versions()),
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
