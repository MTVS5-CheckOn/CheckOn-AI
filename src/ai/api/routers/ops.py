"""운영 liveness·readiness·버전 조회 라우터."""

from __future__ import annotations

import asyncio
import logging
from importlib.metadata import version
from typing import Any, Final

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from ai.api.envelope import error_envelope, versions_dict
from ai.api.routers.detect import detection_versions
from ai.api.routers.diagnosis import diagnosis_versions
from ai.api.routers.problem import problem_failure_versions
from ai.api.version_scope import RouterScope
from ai.composition.classify.classifier import classify_versions
from ai.composition.counsel.versions import counsel_versions
from ai.contracts.execution import VersionSet
from ai.db.session import get_sessionmaker
from ai.import_mapping.versions import import_versions

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ops"])

_READY_TIMEOUT_SECONDS: Final = 2.0
_READY_ERROR_MESSAGE: Final = "서비스 준비 상태를 확인할 수 없습니다."
_OPS_ENGINE: Final = "ops-0.1"
_OPS_VERSIONS: Final = VersionSet(
    pipeline_version="0.1.0",
    engine_version=_OPS_ENGINE,
    schema_version="0.1",
    contract_version="0.1",
)


def ops_versions() -> VersionSet:
    """엔진이 관여하지 않는 앱 운영 응답의 기존 정본 버전."""

    return _OPS_VERSIONS


VERSION_SCOPE: Final = (
    RouterScope("/v1/health", ops_versions),
    RouterScope("/v1/ready", ops_versions),
    RouterScope("/v1/meta/versions", ops_versions),
)


def _success(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "data": data,
        "error": None,
        "meta": {
            "execution_id": None,
            "versions": versions_dict(ops_versions()),
        },
    }


@router.get("/v1/health")
async def health() -> dict[str, Any]:
    """프로세스가 요청을 처리할 수 있는지만 확인한다."""

    return _success({"status": "alive"})


@router.get("/v1/ready", response_model=None)
async def ready() -> dict[str, Any] | JSONResponse:
    """제한시간 안에 PostgreSQL 경량 질의가 가능한지 확인한다."""

    try:
        async with asyncio.timeout(_READY_TIMEOUT_SECONDS):
            sessions = get_sessionmaker()
            async with sessions() as session:
                await session.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning(
            "데이터베이스 readiness 실패 error_type=%s",
            type(exc).__name__,
        )
        return JSONResponse(
            status_code=503,
            content=error_envelope(
                "SERVICE_NOT_READY",
                _READY_ERROR_MESSAGE,
                {"unavailable_components": ["database"]},
                ops_versions(),
            ),
        )
    return _success({"status": "ready"})


@router.get("/v1/meta/versions")
async def meta_versions() -> dict[str, Any]:
    """구현된 capability factory의 현재 선언 버전을 모아 반환한다."""

    # confirmations는 classify_versions를 공유하므로 별도 capability 축으로 중복하지 않는다.
    capabilities = {
        "classify": versions_dict(classify_versions()),
        "counsel": versions_dict(counsel_versions()),
        "detect": versions_dict(detection_versions()),
        "diagnosis": versions_dict(diagnosis_versions()),
        "imports": versions_dict(import_versions()),
        "problem_generation": versions_dict(problem_failure_versions()),
    }
    return _success(
        {
            "app_version": version("checkon-ai"),
            "capabilities": capabilities,
        }
    )


__all__ = ["VERSION_SCOPE", "health", "meta_versions", "ready", "router"]
