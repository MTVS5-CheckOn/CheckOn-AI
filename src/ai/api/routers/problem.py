"""문제 세트 생성 잡의 POST·GET API."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from ai.agents.supervisor import Supervisor, system_utc_now
from ai.api.envelope import success_envelope
from ai.api.version_scope import RouterScope
from ai.contracts.agents import TERMINAL_PHASES, JobPhase, WorkerJob
from ai.contracts.diagnosis import DiagnosisResult
from ai.contracts.execution import VersionSet
from ai.contracts.graphrag import GraphContextService
from ai.contracts.problem_generation import (
    GeneratedItem,
    ProblemGenerationOutcome,
    ProblemItemStatus,
    ProblemRequest,
    ProblemSetResult,
    ProblemSetStatus,
)
from ai.db.repositories.idempotency import IdempotencyStore
from ai.db.repositories.run_store import RunStore, default_llm_call_collector
from ai.db.store_factory import (
    build_agent_job_store,
    build_idempotency_store,
    build_run_store,
)
from ai.problem_generation.application.drain import ProblemDrainLoop
from ai.problem_generation.application.ports import ProblemItemStore
from ai.problem_generation.application.workflow import DiagnosisCallable
from ai.problem_generation.assembly import (
    ProblemGenerationRunner,
    ProblemRuntimeStores,
    build_tenant_scoped_item_store,
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
    poll_retry_after_seconds: int = Field(default=2, ge=1)
    """비종단 조회 응답의 `Retry-After` 값(초).

    🔴 **호출자의 폴링 주기를 코드가 아니라 설정이 정한다.** adapter가 자기 상수로 돌면
    우리가 인라인 실행을 바꿔도(느려지거나 빨라져도) 그쪽 주기는 그대로다 —
    "값이 바뀌면 코드 diff가 생기면 위치가 틀린 것"(03 §1).
    """
    drain_enabled: bool = True
    drain_max_jobs_per_cycle: int = Field(default=20, ge=1)
    drain_cycle_interval_seconds: float = Field(default=0.25, gt=0)
    drain_idle_interval_seconds: float = Field(default=1.0, gt=0)
    drain_failure_backoff_initial_seconds: float = Field(default=1.0, gt=0)
    drain_failure_backoff_max_seconds: float = Field(default=30.0, gt=0)
    drain_shutdown_grace_seconds: float = Field(default=30.0, gt=0)


class ProblemJobView(BaseModel):
    """JobPhase와 도메인 결과를 섞지 않는 조회 투영."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    status: JobPhase
    result: ProblemGenerationOutcome | None = None


class ProblemItemView(BaseModel):
    """Step 3 검토 화면이 읽는 문항 1개 — 슬롯 결과 + 본문.

    🔴 **`failure_detail`을 싣지 않는다.** 그 필드는 내부 진단 문자열(`FieldMissing:…`
    같은 구조화 파싱 오류 원문)이고 강사 화면에 그대로 나가면 안 된다. 폐기 사유는
    **어휘가 고정된** `failure_reason`(generation_exhausted·source_unverified·
    banned_topic) 하나로만 나간다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    slot_index: int = Field(ge=0)
    item_id: str | None = None
    status: ProblemItemStatus
    attempt_no: int = Field(ge=1)
    review_reason: str | None = None
    """`needs_review` 배지 사유 — 「검토 필요」는 **실패가 아니다**(06 §3)."""

    failure_reason: str | None = None
    difficulty_band: str | None = None
    item: GeneratedItem | None = None
    """검증 완료 문항의 본문. `verification_unavailable`·`dropped`에는 없을 수 있다."""


class ProblemItemsView(BaseModel):
    """세트 1개의 문항 목록 투영 — 상태 4종 집계를 함께 싣는다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    job_status: JobPhase
    set_id: str | None = None
    set_status: ProblemSetStatus | None = None
    stop_reason: str | None = None
    requested_count: int | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    """`ProblemItemStatus` 4종 집계 — 화면의 「7/1/1/1」이 이 값이다.

    ⚠ **`needs_review`를 실패로 합산하지 않는다** — 정상 상태다.
    """

    items: tuple[ProblemItemView, ...] = ()


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


@dataclass(slots=True)
class _DrainRegistration:
    drain: ProblemDrainLoop
    users: int


_drains: dict[asyncio.AbstractEventLoop, _DrainRegistration] = {}


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


async def _startup() -> None:
    bootstrap_problem_providers()
    bootstrap_problem_services()
    require_problem_providers()
    require_problem_services()
    await _start_problem_drain()


router.add_event_handler("startup", _startup)


async def _shutdown() -> None:
    loop = asyncio.get_running_loop()
    registration = _drains.get(loop)
    if registration is None:
        return
    registration.users -= 1
    if registration.users > 0:
        return
    del _drains[loop]
    await registration.drain.stop()


router.add_event_handler("shutdown", _shutdown)


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


async def _start_problem_drain() -> None:
    settings = ProblemRouterSettings()
    if not settings.drain_enabled:
        return
    loop = asyncio.get_running_loop()
    registration = _drains.get(loop)
    if registration is not None and registration.drain.is_running:
        registration.users += 1
        return
    drain = ProblemDrainLoop(
        run_next=_run_next_for_tenant,
        max_jobs_per_cycle=settings.drain_max_jobs_per_cycle,
        cycle_interval_seconds=settings.drain_cycle_interval_seconds,
        idle_interval_seconds=settings.drain_idle_interval_seconds,
        failure_backoff_initial_seconds=(
            settings.drain_failure_backoff_initial_seconds
        ),
        failure_backoff_max_seconds=settings.drain_failure_backoff_max_seconds,
        shutdown_grace_seconds=settings.drain_shutdown_grace_seconds,
    )
    await drain.start()
    _drains[loop] = _DrainRegistration(drain=drain, users=1)


def _notify_problem_drain(tenant_id: str) -> None:
    registration = _drains.get(asyncio.get_running_loop())
    if registration is not None:
        registration.drain.notify_tenant(tenant_id)


def problem_drain_running() -> bool:
    """수명주기 테스트가 배경 태스크 잔존 여부를 관측하는 읽기 전용 seam."""

    return any(registration.drain.is_running for registration in _drains.values())


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


def _item_store_for(tenant_id: str) -> ProblemItemStore:
    """조회 경로의 문항 저장소 — 실행 경로(`_run_next_for_tenant`)와 같은 규약이다.

    ⚠ `None`은 "교체할 것이 없다"이므로 배선된 `_stores.items`를 그대로 쓴다(그쪽
    docstring 참조 — 여기서 기본 저장소를 새로 만들면 주입 seam이 죽는다).
    """

    tenant_items = build_tenant_scoped_item_store(tenant_id=tenant_id)
    return _stores.items if tenant_items is None else tenant_items


async def _versions_for(
    job: WorkerJob, *, tenant_id: str, job_id: str
) -> VersionSet:
    """응답 버전 세트 — 캐시에 없으면 **요청 레코드에서 복원**한다.

    🔴 종전에는 `_views` 캐시가 유일한 출처라 **POST를 처리하지 않은 프로세스**(재기동
    후·다른 인스턴스)에서는 조회 자체가 성립하지 않았다. `taxonomy_version`은 요청
    바디에서 오는 값이므로 잡의 `payload_ref`로 되짚는다.
    ⚠ 요청 레코드까지 사라졌으면 **지어내지 않고** `None`으로 둔다(실패 응답과 같은 판단).
    """

    cached = _views.get((tenant_id, job_id))
    if cached is not None:
        return cached.versions
    stored_request = await _stores.requests.get(job.payload_ref, tenant_id=tenant_id)
    return problem_versions(
        taxonomy_version=None if stored_request is None else stored_request.taxonomy_version,
        verify_config_version=load_verify_config().version,
    )


async def _items_view(job: WorkerJob, *, tenant_id: str) -> ProblemItemsView:
    """세트 결과와 슬롯 최종본을 합쳐 Step 3 투영을 만든다."""

    view = await _view_for(job, tenant_id=tenant_id)
    result = view.result
    if not isinstance(result, ProblemSetResult):
        # 아직 종단 전이거나 `rejected_insufficient` — 어느 쪽도 문항이 없다.
        # 🔴 404가 아니다: 잡은 실재하고 "문항이 0개"가 사실이다.
        return ProblemItemsView(job_id=view.job_id, job_status=view.status)

    store = _item_store_for(tenant_id)
    counts = {status.value: 0 for status in ProblemItemStatus}
    rows: list[ProblemItemView] = []
    for slot_index, item_result in enumerate(result.items):
        counts[item_result.status.value] += 1
        body: GeneratedItem | None = None
        if item_result.status is not ProblemItemStatus.DROPPED:
            # dropped는 최종본 저장소에 **애초에 저장되지 않는다**(StoredProblemItem 검증).
            try:
                body = (await store.get(result.set_id, slot_index)).item
            except LookupError as exc:
                # 🔴 조용히 비우지 않는다 — 저장된 문항이 사라진 것은 결함이고,
                #    빈 본문을 정상 응답으로 내보내면 강사가 「본문 없는 문항」을 승인한다.
                raise DomainException(
                    "저장된 문항 최종본을 찾을 수 없다",
                    {"set_id": str(result.set_id), "slot_index": slot_index},
                ) from exc
        rows.append(
            ProblemItemView(
                slot_index=slot_index,
                item_id=None if item_result.item_id is None else str(item_result.item_id),
                status=item_result.status,
                attempt_no=item_result.attempt_no,
                review_reason=(
                    None if item_result.review_reason is None
                    else item_result.review_reason.value
                ),
                failure_reason=(
                    None if item_result.failure_reason is None
                    else item_result.failure_reason.value
                ),
                difficulty_band=(
                    None if item_result.difficulty_band is None
                    else item_result.difficulty_band.value
                ),
                item=body,
            )
        )
    return ProblemItemsView(
        job_id=view.job_id,
        job_status=view.status,
        set_id=str(result.set_id),
        set_status=result.status,
        stop_reason=None if result.stop_reason is None else result.stop_reason.value,
        requested_count=result.requested_count,
        counts=counts,
        items=tuple(rows),
    )


async def _require_job(job_id: str, *, tenant_id: str) -> WorkerJob:
    """테넌트 범위의 잡을 찾고 없으면 404 — 잘못된 UUID도 같은 404다."""

    try:
        parsed_job_id = uuid.UUID(job_id)
    except ValueError as exc:
        raise NotFound("job_id 부재", {"job_id": job_id}) from exc
    job = await _build_supervisor().get(tenant_id=tenant_id, job_id=parsed_job_id)
    if job is None:
        raise NotFound("job_id 부재", {"job_id": job_id})
    return job


async def _generate(request: ProblemRequest) -> tuple[ProblemJobView, WorkerJob]:
    supervisor = _build_supervisor()
    job = await ProblemGenerationEnqueuer(
        supervisor=supervisor,
        request_store=_stores.requests,
    ).enqueue(request)
    try:
        ran = await _run_next_for_tenant(request.tenant_id)
    finally:
        _notify_problem_drain(request.tenant_id)
    if ran is not None and ran.job_id != job.job_id:
        logger.info(
            "PG 러너가 다른 잡을 실행했다 mine=%s ran=%s — 결과는 자기 잡에서 읽는다",
            job.job_id,
            ran.job_id,
        )
    mine = await supervisor.get(
        tenant_id=request.tenant_id, job_id=job.job_id
    ) or job
    return await _view_for(mine, tenant_id=request.tenant_id), job


async def _run_next_for_tenant(tenant_id: str) -> WorkerJob | None:
    supervisor = _build_supervisor()
    graph_context, diagnosis = _require_services()
    providers = require_problem_providers()
    # 🔴 슬롯 최종본 저장소만 **요청 테넌트로 스코프**한다(09 §2-20.3) — Protocol에
    #  `tenant_id`가 없어 인스턴스가 그 축을 든다. 러너는 `lease_next(tenant_id, …)`로만
    #  잡을 집으므로 다른 테넌트의 잡을 이 저장소로 실행할 경로가 없다.
    #  ⚠ `store_backend=memory`(기본)면 `None`이라 **`_stores`가 그대로 간다** — 주입 seam
    #   무변경. 교체할 때도 나머지 셋은 `_stores`에서 그대로 옮긴다(덮어쓰지 않는다).
    tenant_items = build_tenant_scoped_item_store(tenant_id=tenant_id)
    stores = (
        _stores
        if tenant_items is None
        else problem_runtime_stores(
            request_store=_stores.requests,
            result_store=_stores.results,
            candidate_store=_stores.candidates,
            item_store=tenant_items,
        )
    )
    async with open_problem_generation_runner(
        supervisor=supervisor,
        providers=providers,
        graph_context=graph_context,
        diagnosis=diagnosis,
        stores=stores,
        lease_owner=_LEASE_OWNER,
        run_store=_run_store,
    ) as runner:
        return await runner.run_next(tenant_id=tenant_id)


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
        # 🔴 `status`를 같이 싣는다 — counsel 202와 대칭이고(04 §3.9) **BE가 통지를 기다릴지
        #    바로 GET할지를 이 값 하나로 정한다**(런북 §2 규칙). 종전에는 `job_id`만 실려서
        #    ⓐ 인라인 실행으로 이미 종단인 잡을 두고 BE가 Kafka를 기다리거나
        #    ⓑ 아직 `queued`인 잡을 종단으로 오해하거나 — 어느 쪽인지 응답만으로는 알 수 없었다.
        #    ⚠ `run_next()`가 **자기 잡을 처리한다는 보장이 없다**(우선순위·aging 순) —
        #    202가 `queued`로 나가는 경로가 실재한다.
        data={"job_id": view.job_id, "status": view.status.value},
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
async def get_problem(
    job_id: str, request: Request, response: Response
) -> dict[str, Any]:
    """테넌트 범위에서 잡 현재 phase와 확정 결과를 회수한다.

    🔴 **잡 원장이 정본이다** — 종전에는 `_views` 캐시에 없으면 잡을 조회조차 하지 않고
    404였다. 그래서 POST를 받지 않은 프로세스에서는 **영속 잡이 살아 있어도 404**였고,
    그게 BE가 물은 "다중 인스턴스 지원" 질문의 실제 답이었다(2026-08-12 해소).
    """

    tenant_id = request.headers.get("X-Tenant-Id")
    if not tenant_id:
        raise SnapshotInvalid("필수 헤더 누락", {"missing_headers": ["X-Tenant-Id"]})
    job = await _require_job(job_id, tenant_id=tenant_id)
    view = await _view_for(job, tenant_id=tenant_id)
    versions = await _versions_for(job, tenant_id=tenant_id, job_id=job_id)
    _views[(tenant_id, job_id)] = _CachedView(view=view, versions=versions)
    if job.phase not in TERMINAL_PHASES:
        # 🔴 **폴링 종료 조건은 `data.status`가 종단인지 하나다.** 이 헤더는 *"언제 다시
        #    오라"* 만 말한다 — 종단 응답에는 붙지 않으므로 헤더의 유무로도 갈린다.
        response.headers["Retry-After"] = str(
            ProblemRouterSettings().poll_retry_after_seconds
        )
    return success_envelope(
        data=view.model_dump(mode="json"),
        execution_id=str(uuid.uuid4()),
        versions=versions,
    )


@router.get("/v1/problems/{job_id}/items")
async def get_problem_items(job_id: str, request: Request) -> dict[str, Any]:
    """세트의 문항 목록과 **본문**을 회수한다 — Step 3 검토 화면의 원천.

    🔴 **이 엔드포인트가 없어서 Step 3이 막혀 있었다.** `GET /v1/problems/{job_id}`의
    `result`는 세트 요약(`item_id`·상태·수량)이라 발문·선지·해설이 없다. 본문은 문항
    최종본 저장소에만 있었고 노출 경로가 0건이었다.

    ⚠ **승인·발행 상태는 여기 없다** — 그건 백엔드 소유 도메인이다(HITL). 이 응답의
    `status`는 **AI 내부 검증 상태**(`ProblemItemStatus`)일 뿐이다.
    """

    tenant_id = request.headers.get("X-Tenant-Id")
    if not tenant_id:
        raise SnapshotInvalid("필수 헤더 누락", {"missing_headers": ["X-Tenant-Id"]})
    job = await _require_job(job_id, tenant_id=tenant_id)
    view = await _items_view(job, tenant_id=tenant_id)
    versions = await _versions_for(job, tenant_id=tenant_id, job_id=job_id)
    return success_envelope(
        data=view.model_dump(mode="json"),
        execution_id=str(uuid.uuid4()),
        versions=versions,
    )


__all__ = [
    "VERSION_SCOPE",
    "ProblemItemView",
    "ProblemItemsView",
    "ProblemJobView",
    "ProblemProviderNotWired",
    "ProblemServicesNotWired",
    "bootstrap_problem_services",
    "bootstrap_problem_providers",
    "get_problem",
    "get_problem_items",
    "post_problem",
    "problem_drain_running",
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
