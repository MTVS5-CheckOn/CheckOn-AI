"""문제 세트 생성 잡의 POST·GET API."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from ai.agents.supervisor import Supervisor, system_utc_now
from ai.api.envelope import success_envelope
from ai.api.version_scope import RouterScope
from ai.contracts.agents import JobPhase, WorkerJob
from ai.contracts.diagnosis import DiagnosisResult
from ai.contracts.execution import VersionSet
from ai.contracts.graphrag import GraphContextService
from ai.contracts.problem_generation import (
    ProblemGenerationOutcome,
    ProblemRequest,
)
from ai.db.repositories.idempotency import IdempotencyStore
from ai.db.repositories.run_store import RunStore, default_llm_call_collector
from ai.db.store_factory import (
    build_agent_job_store,
    build_idempotency_store,
    build_run_store,
)
from ai.problem_generation.application.workflow import DiagnosisCallable
from ai.problem_generation.assembly import (
    ProblemGenerationRunner,
    ProblemRuntimeStores,
    open_problem_generation_runner,
    problem_runtime_stores,
    problem_versions,
    reset_problem_memory_runtime,
)
from ai.problem_generation.enqueue import ProblemGenerationEnqueuer
from ai.problem_generation.infrastructure.config import load_verify_config
from ai.problem_generation.infrastructure.graph_context import (
    GrammarNormGraphContextService,
)
from ai.problem_generation.provider import ProblemProviders, build_problem_providers
from ai.runtime.errors import (
    DomainException,
    IdempotencyConflict,
    NotFound,
    SnapshotInvalid,
)

logger = logging.getLogger(__name__)

router = APIRouter()


#: 파일이 깨졌으면 실패 응답 때가 아니라 기동 때 죽는 게 맞다 —
#: `require_problem_providers`가 미배선을 기동에서 막는 것과 같은 축이다.
_PROBLEM_FAILURE_VERSIONS: Final = problem_versions(
    taxonomy_version=None,
    verify_config_version=load_verify_config().version,
)


def problem_failure_versions() -> VersionSet:
    """실패 응답용 정적 버전 세트 — 실행 config 확정 전에도 나간다(04 §2.2 A판정 · 99 ㊓).

    🔴 `taxonomy_version`은 요청 **바디**에서 오므로 헤더 누락·JSON 파싱 실패 시점엔 아직
    모른다. 없는 값을 지어내지 않고 `None`으로 둔다 — counsel 실패 응답의 `threshold=None`과
    같은 판단이다("그 실행이 v3를 썼다"는 없는 사실을 만들지 않는다).
    """

    return _PROBLEM_FAILURE_VERSIONS


#: 이 라우터가 응답하는 경로 접두와 그 버전 세트 — `api/app.py`가 **실패 응답**에 쓴다(99 ㊓).
#: 🔴 접두를 여기 두는 이유: **경로를 바꾸는 사람과 접두를 고치는 사람이 같아야 한다.**
#:  `app.py`에 박으면 다른 파일이라 조용히 갈린다.
#: `/v1/problems`·`/{job_id}` 둘을 한 접두가 덮는다.
VERSION_SCOPE: Final = RouterScope("/v1/problems", problem_failure_versions)

_POST_ENDPOINT = "/v1/problems"
_REQUIRED_HEADERS = ("X-Tenant-Id", "X-Request-Id", "Idempotency-Key")
_HEADER_DERIVED_FIELDS = frozenset({"tenant_id", "request_id", "idempotency_key"})
_LEASE_OWNER = "problem-router"


class ProblemRouterSettings(BaseSettings):
    """PG 슈퍼바이저 운영 파라미터."""

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="PG_", extra="ignore"
    )

    lease_seconds: int = 300
    priority_aging_seconds: int = 600


class ProblemJobView(BaseModel):
    """JobPhase와 도메인 결과를 섞지 않는 조회 투영."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    status: JobPhase
    result: ProblemGenerationOutcome | None = None


@dataclass(frozen=True, slots=True)
class _CachedView:
    view: ProblemJobView
    versions: VersionSet


_idempotency_store: IdempotencyStore = build_idempotency_store()
_run_store: RunStore = build_run_store()
_stores: ProblemRuntimeStores = problem_runtime_stores()
_views: dict[tuple[str, str], _CachedView] = {}
_providers: ProblemProviders | None = None
_graph_context: GraphContextService | None = None
_diagnosis: DiagnosisCallable | None = None


class ProblemProviderNotWired(RuntimeError):
    """실 PG provider가 기동 조립에서 주입되지 않음."""


class ProblemServicesNotWired(RuntimeError):
    """GraphContext·진단 서비스가 아직 조립되지 않음."""


def set_problem_providers(providers: ProblemProviders) -> None:
    """실 provider 또는 테스트가 명시한 provider를 주입한다."""

    global _providers
    _providers = providers


def require_problem_providers() -> ProblemProviders:
    if _providers is None:
        raise ProblemProviderNotWired(
            "problem_generation provider가 배선되지 않았다 — Fake 폴백은 허용하지 않는다"
        )
    return _providers


def bootstrap_problem_providers() -> None:
    """기동 시 env 기반 실 OpenAI 호환 provider를 멱등 조립한다."""

    if _providers is not None:
        return
    set_problem_providers(build_problem_providers())


def _startup() -> None:
    bootstrap_problem_providers()
    bootstrap_problem_services()
    require_problem_providers()
    require_problem_services()


router.add_event_handler("startup", _startup)


def set_problem_services(
    *, graph_context: GraphContextService, diagnosis: DiagnosisCallable
) -> None:
    """근거층·진단 구현 주입 seam. 근거층 구현 전에는 명시 주입만 허용한다."""

    global _graph_context, _diagnosis
    _graph_context = graph_context
    _diagnosis = diagnosis


async def _diagnosis_not_wired(_request: ProblemRequest) -> DiagnosisResult:
    raise ProblemServicesNotWired(
        "weakness_auto 진단 서비스가 배선되지 않았다 — 명시 주입이 필요하다"
    )


def bootstrap_problem_services() -> None:
    """기동 시 실 어문규범 GraphContext를 멱등 조립한다."""

    if _graph_context is not None:
        return
    set_problem_services(
        graph_context=GrammarNormGraphContextService(),
        diagnosis=_diagnosis or _diagnosis_not_wired,
    )


def require_problem_services() -> tuple[GraphContextService, DiagnosisCallable]:
    """배선된 실 근거층과 주입된 진단 seam을 반환한다."""

    return _require_services()


def set_problem_stores(stores: ProblemRuntimeStores) -> None:
    """테스트와 후속 PG 어댑터가 조립부 저장소 묶음을 교체하는 seam."""

    global _stores
    _stores = stores


def set_problem_run_store(store: RunStore) -> None:
    """AI_RUN·LLM_CALL 적재 관측용 저장소 주입 seam."""

    global _run_store
    _run_store = store


def set_problem_idempotency_store(store: IdempotencyStore) -> None:
    """멱등 재반환 테스트·PG 어댑터용 저장소 주입 seam."""

    global _idempotency_store
    _idempotency_store = store


def reset_problem_router() -> None:
    """PG 테스트가 명시 호출해 라우터 소유 인메모리 상태를 격리한다."""

    global _idempotency_store, _run_store, _stores
    global _providers, _graph_context, _diagnosis
    reset_problem_memory_runtime()
    default_llm_call_collector().reset()
    _idempotency_store = build_idempotency_store()
    _run_store = build_run_store()
    _stores = problem_runtime_stores()
    _views.clear()
    _providers = None
    _graph_context = None
    _diagnosis = None


def _require_services() -> tuple[GraphContextService, DiagnosisCallable]:
    if _graph_context is None or _diagnosis is None:
        raise ProblemServicesNotWired(
            "problem_generation GraphContext·진단 서비스가 배선되지 않았다"
        )
    return _graph_context, _diagnosis


def _build_supervisor() -> Supervisor:
    settings = ProblemRouterSettings()
    return Supervisor(
        store=build_agent_job_store(),
        lease_duration=timedelta(seconds=settings.lease_seconds),
        priority_aging_interval=timedelta(seconds=settings.priority_aging_seconds),
        clock=system_utc_now,
    )


def _canonical_hash(body: dict[str, Any]) -> str:
    canonical = json.dumps(
        body, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _format_validation_error(exc: ValidationError) -> list[dict[str, str]]:
    return [
        {"field": ".".join(str(part) for part in error["loc"]), "type": error["type"]}
        for error in exc.errors()
    ]


def _problem_request(
    raw_body: dict[str, Any], *, tenant_id: str, request_id: str, idempotency_key: str
) -> ProblemRequest:
    duplicated = sorted(_HEADER_DERIVED_FIELDS.intersection(raw_body))
    if duplicated:
        raise SnapshotInvalid(
            "헤더 파생 필드는 요청 바디에 둘 수 없다", {"fields": duplicated}
        )
    try:
        return ProblemRequest.model_validate(
            {
                **raw_body,
                "tenant_id": tenant_id,
                "request_id": request_id,
                "idempotency_key": idempotency_key,
            }
        )
    except ValidationError as exc:
        raise SnapshotInvalid(
            "요청 바디 스키마 위반", _format_validation_error(exc)
        ) from exc


async def _view_for(
    job: WorkerJob,
    *,
    tenant_id: str,
    runner: ProblemGenerationRunner | None = None,
) -> ProblemJobView:
    result: ProblemGenerationOutcome | None = None
    if job.phase is JobPhase.SUCCEEDED:
        if job.result_ref is None:
            raise DomainException("성공한 문제 생성 잡에 result_ref가 없다")
        result = (
            await runner.result_of(job.result_ref, tenant_id=tenant_id)
            if runner is not None
            else await _stores.results.get(job.result_ref, tenant_id=tenant_id)
        )
        if result is None:
            raise DomainException("성공한 문제 생성 잡의 결과를 찾을 수 없다")
    return ProblemJobView(
        job_id=str(job.job_id),
        status=job.phase,
        result=result,
    )


async def _generate(request: ProblemRequest) -> tuple[ProblemJobView, WorkerJob]:
    supervisor = _build_supervisor()
    job = await ProblemGenerationEnqueuer(
        supervisor=supervisor,
        request_store=_stores.requests,
    ).enqueue(request)
    graph_context, diagnosis = _require_services()
    providers = require_problem_providers()
    async with open_problem_generation_runner(
        supervisor=supervisor,
        providers=providers,
        graph_context=graph_context,
        diagnosis=diagnosis,
        stores=_stores,
        lease_owner=_LEASE_OWNER,
        run_store=_run_store,
    ) as runner:
        ran = await runner.run_next(tenant_id=request.tenant_id)
        if ran is not None and ran.job_id != job.job_id:
            logger.info(
                "PG 러너가 다른 잡을 실행했다 mine=%s ran=%s — 결과는 자기 잡에서 읽는다",
                job.job_id,
                ran.job_id,
            )
        mine = await supervisor.get(
            tenant_id=request.tenant_id, job_id=job.job_id
        ) or job
        return await _view_for(
            mine, tenant_id=request.tenant_id, runner=runner
        ), job


@router.post("/v1/problems", status_code=202)
async def post_problem(request: Request, response: Response) -> dict[str, Any]:
    """문제 세트 생성 잡을 넣고 최초·재반환 모두 202를 돌려준다."""

    missing = [name for name in _REQUIRED_HEADERS if not request.headers.get(name)]
    if missing:
        raise SnapshotInvalid("필수 헤더 누락", {"missing_headers": missing})
    tenant_id = request.headers["X-Tenant-Id"]
    request_id = request.headers["X-Request-Id"]
    idempotency_key = request.headers["Idempotency-Key"]
    try:
        raw_body = await request.json()
    except ValueError as exc:
        raise SnapshotInvalid("요청 바디가 유효한 JSON이 아님", str(exc)) from exc
    if not isinstance(raw_body, dict):
        raise SnapshotInvalid("요청 바디는 JSON 객체여야 한다")

    body_hash = _canonical_hash(raw_body)
    hit = await _idempotency_store.get(
        tenant_id=tenant_id,
        endpoint=_POST_ENDPOINT,
        idempotency_key=idempotency_key,
    )
    if hit is not None:
        if hit.snapshot_hash == body_hash:
            response.status_code = 202
            return hit.response_body
        raise IdempotencyConflict(
            "같은 Idempotency-Key에 다른 바디",
            {"idempotency_key": idempotency_key},
        )

    problem_request = _problem_request(
        raw_body,
        tenant_id=tenant_id,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
    view, job = await _generate(problem_request)
    versions = problem_versions(
        taxonomy_version=problem_request.taxonomy_version,
        verify_config_version=load_verify_config().version,
    )
    _views[(tenant_id, view.job_id)] = _CachedView(view=view, versions=versions)
    envelope = success_envelope(
        data={"job_id": view.job_id},
        execution_id=str(job.execution_id),
        versions=versions,
    )
    await _idempotency_store.put(
        tenant_id=tenant_id,
        endpoint=_POST_ENDPOINT,
        idempotency_key=idempotency_key,
        snapshot_hash=body_hash,
        response_body=envelope,
    )
    return envelope


@router.get("/v1/problems/{job_id}")
async def get_problem(job_id: str, request: Request) -> dict[str, Any]:
    """테넌트 범위에서 잡 현재 phase와 확정 결과를 회수한다."""

    tenant_id = request.headers.get("X-Tenant-Id")
    if not tenant_id:
        raise SnapshotInvalid("필수 헤더 누락", {"missing_headers": ["X-Tenant-Id"]})
    cached = _views.get((tenant_id, job_id))
    if cached is None:
        raise NotFound("job_id 부재", {"job_id": job_id})
    try:
        parsed_job_id = uuid.UUID(job_id)
    except ValueError as exc:
        raise NotFound("job_id 부재", {"job_id": job_id}) from exc
    job = await _build_supervisor().get(
        tenant_id=tenant_id, job_id=parsed_job_id
    )
    if job is None:
        raise NotFound("job_id 부재", {"job_id": job_id})
    view = await _view_for(job, tenant_id=tenant_id)
    _views[(tenant_id, job_id)] = _CachedView(
        view=view, versions=cached.versions
    )
    return success_envelope(
        data=view.model_dump(mode="json"),
        execution_id=str(uuid.uuid4()),
        versions=cached.versions,
    )


__all__ = [
    "VERSION_SCOPE",
    "ProblemJobView",
    "ProblemProviderNotWired",
    "ProblemServicesNotWired",
    "bootstrap_problem_services",
    "bootstrap_problem_providers",
    "get_problem",
    "post_problem",
    "problem_failure_versions",
    "require_problem_providers",
    "require_problem_services",
    "reset_problem_router",
    "router",
    "set_problem_idempotency_store",
    "set_problem_providers",
    "set_problem_run_store",
    "set_problem_services",
    "set_problem_stores",
]
