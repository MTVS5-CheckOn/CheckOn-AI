"""운영 liveness·readiness·버전 조회 라우터."""

#: 🔴 **[공통 계약 · 변경 시 양쪽 축 확인]** — A 셋(detect·classify·counsel)과
#: B 둘(diagnosis·problem)을 **전부 읽는다.** 한 축이 고치면 반대 축이 깨진다.
#: ⚠ 양자 승인 13목록에는 **없다** — 이 파일은 **읽기만** 하고 시그니처를 정하지 않는다.
#:   `app.py`·`envelope.py` 와 달리 여기를 고쳐도 **상대 계약이 안 바뀐다** —
#:   상대가 바뀌면 여기가 깨질 뿐이다. 그래서 양자 승인의 무게는 과하다.
#: 🔴 «확인해라」가 말뿐이 되지 않게, 그 확인을 무는 검사를 적어 둔다:
#:   `tests/ai/contract/test_meta_versions_cover_every_scope.py`
#: 🔴 소유: 공통 (A·B) · 표기 확정 2026-08-24 (A 제안 ⓒ · B 수용)

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
from ai.composition.labels.versions import labels_versions
from ai.contracts.execution import VersionSet
from ai.db.session import get_sessionmaker

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
    """실행 없는 ops용 공통 shape — meta 확장 시 envelope 계약과 함께 갱신한다."""

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
    """제한시간 안에 PostgreSQL 경량 질의가 가능한지 확인한다.

    DomainException 경로를 타지 않는 첫 엔드포인트다. 공개 논리 상태인 503 detail이
    5xx 기본 미노출 정책으로 지워지지 않도록 error_envelope와 JSONResponse를 직접 쓴다.
    """

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
    # ⚠ 🔴 **표기 판정 대기(2026-08-24 회신)** — 이 파일의 [공통 계약] 표기·양자 승격
    #   여부는 준영님 판정 중이다. 이 회차는 **목록 한 줄과 주석만** 건드린다.
    # 🔴 **`ROUTER_VERSION_SCOPES` 에서 파생하지 않는다 — 뜻이 다르다**(실측 8/24):
    #     `RouterScope`   «**실패 응답**의 `meta.versions` 를 무엇으로 채우나» — **경로별 11개**
    #     여기            «운영자가 조회하는 **capability** 목록» — **중복을 뺀 축**
    #   ⇒ 1:1 이 아니다(confirmations 는 classify 와 같은 engine, ops 는 3경로 1축).
    #   🔴 **대신 「둘 다 갱신됐나」를 무는 가드**를 세웠다
    #   (`tests/ai/contract/test_meta_versions_cover_every_scope.py`).
    capabilities = {
        "classify": versions_dict(classify_versions()),
        "counsel": versions_dict(counsel_versions()),
        "detect": versions_dict(detection_versions()),
        "diagnosis": versions_dict(diagnosis_versions()),
        # 🔴 **(8/24) labels 추가** — №65 가 `labels_versions()` 를 만들고 **여기 안 붙였다**.
        #   승우님께 «다음 회차에 넣습니다» 로 고지한 그 자리다.
        "labels": versions_dict(labels_versions()),
        "problem_generation": versions_dict(problem_failure_versions()),
    }
    return _success(
        {
            "app_version": version("checkon-ai"),
            "capabilities": capabilities,
        }
    )


__all__ = ["VERSION_SCOPE", "health", "meta_versions", "ready", "router"]
