"""counsel 초안 라우터 — `/v1/counsel/drafts` (인박스 계약 v1 §4 · 04 §3.9).

소유: 박진희 (composition 라우터). 계약(`contracts/counsel`)과 기존 counsel_pack 워커를
HTTP로 노출한다. **요청 단위 = 문의 1건**이며 워커는 새로 만들지 않는다 — 학생 묶음 워커를
**N=1 축퇴 사례로 재사용**한다(99 D ㉛). 저장(`pack://`·`draft://`)·재개·수렴·서킷 경로는
그대로다.

**동기 실행(선례: `imports` 라우터).** enqueue → 같은 요청 안에서 러너를 1회 돌려 결과를
확정하고 202를 낸다. 실 비동기 워커 루프·Kafka 완료 통지는 후속이며, 그때 이 라우터는
enqueue까지만 하고 GET이 잡 상태를 읽는 형태로 좁아진다(계약은 그대로다 — BE는 이미
202 → Kafka → GET 순서로 쓴다).

**CI 기본은 Fake provider** — 실 LLM 호출 0. 실 경로는 `build_gateway_writer`로 교체한다.

멱등: 감지·Import 라우터 선례 재사용 — `(tenant_id, endpoint, idempotency_key)` 스코프,
바디 동일성은 canonical 해시. 캐시 fail-open.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Request, Response
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from ai.agents.supervisor import Supervisor, system_utc_now
from ai.api.envelope import success_envelope
from ai.composition.counsel.enqueue import CounselPackEnqueuer
from ai.composition.counsel.labels import LabelVocabularyError, snapshot_from_labels
from ai.composition.counsel.provider import FakeCounselProvider
from ai.composition.counsel.refine import refine_draft
from ai.composition.counsel.settings import get_counsel_settings
from ai.composition.counsel.stores import (
    DRAFT_SCHEME,
    AgentStepSink,
    ContextStore,
    DraftResultStore,
    InMemoryAgentStepSink,
    InMemoryContextStore,
    InMemoryDraftResultStore,
    InMemoryPackResultStore,
    PackResultStore,
    make_ref,
)
from ai.composition.counsel.worker import CounselPackRunner
from ai.contracts.agents import JobPhase, WorkerJob
from ai.contracts.composition import DraftContext, EvidenceFact
from ai.contracts.counsel import (
    Citation,
    CounselDraftJobView,
    CounselDraftRequest,
    CounselDraftResult,
    RefineRequest,
    RefineResponse,
    WireDraftStatus,
    wire_status_for,
)
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.gates import BlockedReason
from ai.db.repositories.idempotency import IdempotencyStore
from ai.db.store_factory import build_agent_job_store, build_idempotency_store
from ai.runtime.errors import IdempotencyConflict, NotFound, SnapshotInvalid

logger = logging.getLogger(__name__)

router = APIRouter()

_PIPELINE_VERSION = "0.1.0"
_ENGINE_VERSION = "counsel-pack-0.1"
_SCHEMA_VERSION = "0.1"
_CONTRACT_VERSION = "0.1"

_REQUIRED_HEADERS = ("X-Tenant-Id", "X-Request-Id", "Idempotency-Key")
_POST_ENDPOINT = "POST /v1/counsel/drafts"

#: 게이트 실패 재생성 상한 — ERD DRAFT_BLOCK "≤3"(불변식 6).
_REGEN_MAX = 3
_LEASE_OWNER = "counsel-router"

#: 시계 주입점 — `datetime.now()` 직접 호출 금지(03 §3).
_clock = system_utc_now

_idempotency_store: IdempotencyStore = build_idempotency_store()
_context_store: ContextStore = InMemoryContextStore()
_draft_store: DraftResultStore = InMemoryDraftResultStore()
_pack_store: PackResultStore = InMemoryPackResultStore()
_step_sink: AgentStepSink = InMemoryAgentStepSink()

#: 와이어 읽기 모델 — `(tenant_id, job_id) → 계약 뷰`. GET의 **유일한 출처**다.
#: 잡 원장(`WorkerJob`)은 phase·lease·재개용 내부 상태이고, 계약 응답은 그 투영이다.
_views: dict[tuple[str, str], CounselDraftJobView] = {}


@dataclass
class _DraftState:
    """refine 대상 초안의 현재 상태 — `(tenant_id, job_id)`로 찾는다.

    `context`는 게이트 재통과에 필요하고(허용 숫자·금칙·길이 상한이 전부 여기서 나온다),
    `citations`는 반영 턴 응답에 다시 실린다. **영속은 후속**이다 — v1은 `_views`와 같은
    인메모리 읽기 모델이며 PG 이관 시 DRAFT_REVISION(ERD)이 자리를 받는다(06 §7).
    """

    context: DraftContext
    citations: tuple[Citation, ...]
    text: str


#: refine 읽기 모델 — `(tenant_id, job_id) → 초안 상태`. 키가 job_id인 이유는
#: 문의 1건 = 잡 1개 = 초안 1개(pack N=1 · 99 D ㉛)라 별도 draft_id를 노출할 필요가
#: 없고, BE가 Kafka 완료 통지로 이미 받은 값을 그대로 쓸 수 있어서다(04 §3.9).
_drafts: dict[tuple[str, str], _DraftState] = {}

#: LLM 접점 — CI 기본은 결정론 Fake다(실 호출 0). 실 경로는 주입으로 교체한다.
_provider: Any = FakeCounselProvider()


def set_counsel_provider(provider: Any) -> None:  # noqa: ANN401 — Planner+Writer 이중 Protocol
    """plan·write provider 주입 — 테스트 시나리오·실 LLM 배선의 seam."""
    global _provider
    _provider = provider


def set_counsel_stores(
    *,
    idempotency_store: IdempotencyStore | None = None,
    context_store: ContextStore | None = None,
    draft_store: DraftResultStore | None = None,
    pack_store: PackResultStore | None = None,
    step_sink: AgentStepSink | None = None,
) -> None:
    """저장소 주입 — 합성 루트·테스트에서 특정 인스턴스를 꽂는다."""
    global _idempotency_store, _context_store, _draft_store, _pack_store, _step_sink
    if idempotency_store is not None:
        _idempotency_store = idempotency_store
    if context_store is not None:
        _context_store = context_store
    if draft_store is not None:
        _draft_store = draft_store
    if pack_store is not None:
        _pack_store = pack_store
    if step_sink is not None:
        _step_sink = step_sink


def reset_counsel_stores() -> None:
    """테스트 격리용 — 저장소·읽기 모델·provider를 기본값으로 되돌린다."""
    global _idempotency_store, _context_store, _draft_store, _pack_store, _step_sink
    _idempotency_store = build_idempotency_store()
    _context_store = InMemoryContextStore()
    _draft_store = InMemoryDraftResultStore()
    _pack_store = InMemoryPackResultStore()
    _step_sink = InMemoryAgentStepSink()
    _views.clear()
    _drafts.clear()
    set_counsel_provider(FakeCounselProvider())


def counsel_versions() -> VersionSet:
    """이 엔드포인트의 버전 세트 — 실패 응답에도 실린다(04 §2.2 A판정)."""
    return VersionSet(
        pipeline_version=_PIPELINE_VERSION,
        engine_version=_ENGINE_VERSION,
        schema_version=_SCHEMA_VERSION,
        contract_version=_CONTRACT_VERSION,
    )


def _format_validation_error(exc: ValidationError) -> list[dict[str, str]]:
    """필드 경로만 — 값은 싣지 않는다(04 §2.3 · 개인정보가 detail로 새지 않게)."""
    return [
        {"field": ".".join(str(part) for part in error["loc"]), "type": error["type"]}
        for error in exc.errors()
    ]


def _canonical_hash(body: dict[str, Any]) -> str:
    """바디 동일성 판정용 canonical 해시 — Import 라우터와 같은 방식."""
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _citations_of(request: CounselDraftRequest) -> tuple[Citation, ...]:
    """인용 목록 — **근거 우주 파생**(계약 §4-③ `citations` ≥1).

    `record_id`가 있는 fact 전수다. `DraftContext.cited_record_ids()`(⑱)와 같은 개념의
    요청측 대칭이며 새 파생을 발명하지 않는다. LLM을 쓰지 않는 결정론이다.

    ⚠ **v1은 본문 인라인 앵커(`#L1`)를 지원하지 않는다**(99 D ㊳) — `cite_id`는 목록 안의
    순서 키다. 계약 §4-③ 예시의 `#L1` 문면은 v1.1에서 유효해진다(BE·FE 통보 완료).
    """
    return tuple(
        Citation(cite_id=f"L{index}", record_id=fact.record_id, summary=fact.summary)
        for index, fact in enumerate(request.context.citable_facts(), start=1)
        if fact.record_id
    )


def _draft_context(request: CounselDraftRequest) -> DraftContext:
    """계약 요청 → 워커 입력. 문의 1건이므로 학생 1명짜리 컨텍스트 하나다.

    `guardian_ref`에는 `parent_ref`가 들어간다 — 둘 다 가명이며 이름만 다르다.
    """
    snapshot, _applied = snapshot_from_labels(request.labels)
    facts = tuple(
        EvidenceFact(label="근거", value=fact.summary, record_id=fact.record_id)
        for fact in request.context.facts
    )
    return DraftContext(
        student_ref=request.student_ref,
        guardian_ref=request.parent_ref,
        label_snapshot=snapshot,
        facts=facts,
        evidence_summaries=(),
        period_label=request.context.period_label,
        fallback_text="이번 기간 학습 상황을 정리해 보내드립니다.",
    )


#: 차단 사유별 강사 문구 — 원본은 `part_a/06_refine_policy.md` §4 표다(여기서 새로 만들지
#: 않는다). BE는 이 문구를 그대로 중계한다(계약 §4-④ `message`).
REFINE_BLOCK_MESSAGES: dict[BlockedReason, str] = {
    BlockedReason.EVIDENCE_MISSING: "요청하신 내용은 기록에서 확인되지 않아 반영하지 못했어요",
    BlockedReason.COMPARISON_EXPOSURE: "반 평균·석차는 학부모 문서에 포함할 수 없어요(내부 지표)",
    BlockedReason.TONE_VIOLATION: "해당 표현은 안전 기준에 걸려 완곡한 표현으로 제안했어요",
    BlockedReason.PII_EXPOSURE: "개인정보는 초안에 넣을 수 없어요",
    BlockedReason.OUT_OF_SCOPE: "이 초안의 다듬기와 무관한 요청이에요",
}


def _refine_execution_context(execution_id: uuid.UUID, tenant_id: str) -> ExecutionContext:
    """refine 턴의 실행 컨텍스트 — LLM 호출 기록이 함께 받는다(불변식 8)."""
    return ExecutionContext(
        execution_id=execution_id,
        tenant_id=tenant_id,
        capability=Capability.COMPOSITION,
        input_snapshot_hash="sha256:refine",
        versions=counsel_versions(),
    )


def _build_supervisor() -> Supervisor:
    settings = get_counsel_settings()
    return Supervisor(
        store=build_agent_job_store(),
        lease_duration=timedelta(seconds=settings.counsel_lease_seconds),
        priority_aging_interval=timedelta(
            seconds=settings.counsel_priority_aging_seconds
        ),
        clock=_clock,
    )


async def _generate(
    request: CounselDraftRequest, *, tenant_id: str
) -> CounselDraftJobView:
    """문의 1건 → 초안 1건. 워커는 기존 counsel_pack을 N=1로 재사용한다(99 D ㉛).

    🔴 **근거 선검사가 LLM 호출보다 앞이다.** 인용 가능한 근거(`record_id` 있는 fact)가
    0건이면 게이트를 통과한 초안을 만들어 놓고 `citations`가 비어 버리는 낭비가 되므로,
    **호출 전에** `rejected_insufficient`로 끊는다(04 §3.9 규약 · 불변식 2).
    """
    _snapshot, applied = snapshot_from_labels(request.labels)
    if not request.context.citable_facts():
        return CounselDraftJobView(
            job_id=str(uuid.uuid4()),
            status=JobPhase.SUCCEEDED.value,
            result=CounselDraftResult(
                draft_status=WireDraftStatus.REJECTED_INSUFFICIENT,
                status_reason="no_citable_evidence",
                labels_applied=applied,
                generated_at=_clock(),
            ),
        )

    supervisor = _build_supervisor()
    context = _draft_context(request)
    job = await CounselPackEnqueuer(
        supervisor=supervisor, context_store=_context_store, now=_clock
    ).enqueue(
        tenant_id=tenant_id,
        class_ref=request.class_ref,
        contexts={request.student_ref: context},
    )
    runner = CounselPackRunner(
        supervisor=supervisor,
        context_store=_context_store,
        step_sink=_step_sink,
        draft_store=_draft_store,
        pack_store=_pack_store,
        planner=_provider,
        writer=_provider,
        checkpointer=InMemorySaver(),
        regen_max=_REGEN_MAX,
        lease_owner=_LEASE_OWNER,
        now=_clock,
    )
    ran = await runner.run_next(tenant_id=tenant_id) or job
    # 🔴 뷰가 싣는 job_id와 refine 등록 키는 **같은 값이어야 한다** — 그래서 한 곳에서
    # 만들어 양쪽에 넘긴다. `ran`은 `run_next`가 집어온 잡이라 `job`과 다를 수 있으므로
    # `ran.job_id`를 쓰면 응답의 job_id로 refine을 못 찾는 조합이 생긴다.
    view_job_id = str(job.job_id)
    result = await _wire_result(
        runner, ran, request, applied, job_id=view_job_id, tenant_id=tenant_id
    )
    return CounselDraftJobView(
        job_id=view_job_id, status=ran.phase.value, result=result
    )


async def _wire_result(
    runner: CounselPackRunner,
    job: WorkerJob,
    request: CounselDraftRequest,
    applied: tuple[str, ...],
    *,
    job_id: str,
    tenant_id: str,
) -> CounselDraftResult:
    """잡 결과 → 계약 `result`. 판정 파생은 `wire_status_for` 한 곳이 한다.

    **잡 성공 ≠ 초안 존재**(불변식 4) — 결과 계약이 저장됐어도 학생 판정은 거부일 수 있다.
    """
    pack = await runner.result_of(job.result_ref) if job.result_ref else None
    if pack is None:
        # 결과 계약 **자체가 없다** = 잡 장애(error_codes §2.5의 failed 정의).
        return CounselDraftResult(
            draft_status=WireDraftStatus.LLM_FAILED,
            status_reason=job.error_code or "job_no_result",
            labels_applied=applied,
            generated_at=_clock(),
        )
    student = pack.results[0] if pack.results else None
    if student is None:
        # 🔴 결과 계약은 있는데 **학생 결과가 0건** — 묶음에 그 학생이 없었다는 뜻이다
        # (`student_refs = sorted(bundle.contexts)`). N=1에서 이건 장애가 아니라
        # **컨텍스트 부재**다. 실패로 내면 화면이 "아직 데이터를 모으는 중이에요"가 아니라
        # "다시 시도"를 그린다 — 인박스 계약 §4 매핑 표가 깨진다(점검 B-4).
        return CounselDraftResult(
            draft_status=WireDraftStatus.REJECTED_INSUFFICIENT,
            status_reason="context_missing",
            labels_applied=applied,
            generated_at=_clock(),
        )
    wire, reason = wire_status_for(student.status, student.fail_reason)
    if wire is not WireDraftStatus.GENERATED:
        return CounselDraftResult(
            draft_status=wire,
            status_reason=reason,
            labels_applied=applied,
            generated_at=_clock(),
        )
    record = (
        await _draft_store.get(make_ref(DRAFT_SCHEME, student.draft_id))
        if student.draft_id
        else None
    )
    citations = _citations_of(request)
    if record is not None:
        # refine 대상 등록 — 키는 **응답이 싣는 job_id**다(04 §3.9). 종전에는 내부
        # `record.id`로 등록했는데 그 값은 어떤 응답에도 실리지 않아, BE가 refine 대상
        # 키를 얻을 계약 경로가 없었다(호출하면 404 확정 · 99 D).
        _drafts[(tenant_id, job_id)] = _DraftState(
            context=_draft_context(request), citations=citations, text=record.content
        )
    return CounselDraftResult(
        draft_status=WireDraftStatus.GENERATED,
        text=record.content if record else None,
        citations=citations,
        labels_applied=applied,
        generated_at=_clock(),
    )


@router.post("/v1/counsel/drafts", status_code=202)
async def post_counsel_draft(request: Request, response: Response) -> dict[str, Any]:
    """초안 생성 — 헤더·바디 검증 → 멱등 → 근거 선검사 → 워커(N=1) → 계약 뷰 저장."""
    missing = [name for name in _REQUIRED_HEADERS if not request.headers.get(name)]
    if missing:
        raise SnapshotInvalid("필수 헤더 누락", {"missing_headers": missing})

    tenant_id = request.headers["X-Tenant-Id"]
    idempotency_key = request.headers["Idempotency-Key"]

    try:
        raw_body = await request.json()
    except ValueError as exc:
        raise SnapshotInvalid("요청 바디가 유효한 JSON이 아님", str(exc)) from exc

    try:
        draft_request = CounselDraftRequest.model_validate(raw_body)
    except ValidationError as exc:
        raise SnapshotInvalid(
            "요청 바디 스키마 위반", _format_validation_error(exc)
        ) from exc

    try:
        snapshot_from_labels(draft_request.labels)
    except LabelVocabularyError as exc:
        raise SnapshotInvalid(str(exc), {"labels": list(exc.offending)}) from exc

    body_hash = _canonical_hash(raw_body)
    hit = await _idempotency_store.get(
        tenant_id=tenant_id, endpoint=_POST_ENDPOINT, idempotency_key=idempotency_key
    )
    if hit is not None:
        if hit.snapshot_hash == body_hash:
            response.status_code = 202
            return hit.response_body
        raise IdempotencyConflict(
            "같은 Idempotency-Key에 다른 바디", {"idempotency_key": idempotency_key}
        )

    execution_id = uuid.uuid4()
    view = await _generate(draft_request, tenant_id=tenant_id)
    _views[(tenant_id, view.job_id)] = view

    envelope = success_envelope(
        data={"job_id": view.job_id, "status": view.status},
        execution_id=str(execution_id),
        versions=counsel_versions(),
    )
    await _idempotency_store.put(
        tenant_id=tenant_id,
        endpoint=_POST_ENDPOINT,
        idempotency_key=idempotency_key,
        snapshot_hash=body_hash,
        response_body=envelope,
    )
    return envelope


@router.get("/v1/counsel/drafts/{job_id}")
async def get_counsel_draft(job_id: str, request: Request) -> dict[str, Any]:
    """결과 회수 — 잡 성공 ≠ 초안 존재(불변식 4)."""
    tenant_id = request.headers.get("X-Tenant-Id")
    if not tenant_id:
        raise SnapshotInvalid("필수 헤더 누락", {"missing_headers": ["X-Tenant-Id"]})
    view = _views.get((tenant_id, job_id))
    if view is None:  # 존재 은닉 — 다른 테넌트의 job_id도 여기로 떨어진다
        raise NotFound("job_id 부재", {"job_id": job_id})
    return success_envelope(
        data=view.model_dump(mode="json"),
        execution_id=str(uuid.uuid4()),
        versions=counsel_versions(),
    )


@router.post("/v1/counsel/drafts/{job_id}/refine")
async def post_counsel_refine(job_id: str, request: Request) -> dict[str, Any]:
    """다듬기 1턴 — 동기 · **매 턴 게이트 전체 재통과**(06 §1).

    🔴 **차단도 200이다**(`applied:false` + 사유 + 문구) — 게이트 거부는 에러가 아니다
    (불변식 4 · error_codes §4 "GateRejected를 5xx로 올리는 코드는 리뷰 반려").

    대상 키는 `job_id`다 — POST 202 응답·Kafka 완료 통지가 싣는 그 값이다.
    FE 계약 §3-③의 `inquiry_id`는 BE가 중계 매핑한다(04 §3.9).
    턴 상한을 판정하지 않는다: `turn_no`는 받아서 로그로만 쓴다(쿼터는 전부 백엔드).
    """
    tenant_id = request.headers.get("X-Tenant-Id")
    if not tenant_id:
        raise SnapshotInvalid("필수 헤더 누락", {"missing_headers": ["X-Tenant-Id"]})

    try:
        raw_body = await request.json()
    except ValueError as exc:
        raise SnapshotInvalid("요청 바디가 유효한 JSON이 아님", str(exc)) from exc
    try:
        refine_request = RefineRequest.model_validate(raw_body)
    except ValidationError as exc:
        raise SnapshotInvalid(
            "요청 바디 스키마 위반", _format_validation_error(exc)
        ) from exc

    state = _drafts.get((tenant_id, job_id))
    if state is None:  # 존재 은닉 — 다른 테넌트의 job_id도 여기로 떨어진다
        raise NotFound("job_id 부재", {"job_id": job_id})

    execution_id = uuid.uuid4()
    outcome = await refine_draft(
        context=state.context,
        instruction=refine_request.instruction,
        writer=_provider,
        execution_context=_refine_execution_context(execution_id, tenant_id),
        regen_max=_REGEN_MAX,
    )
    if outcome.applied and outcome.text:
        state.text = outcome.text  # 반영분만 승격 — 차단 턴은 직전 버전 유지(계약 §6)
        response = RefineResponse(
            applied=True, text=outcome.text, citations=state.citations
        )
    else:
        logger.info(
            "refine 차단 turn=%d reason=%s",
            refine_request.turn_no,
            outcome.blocked_reason.value if outcome.blocked_reason else "unknown",
        )
        response = RefineResponse(
            applied=False,
            blocked_reason=outcome.blocked_reason,
            message=REFINE_BLOCK_MESSAGES[outcome.blocked_reason]
            if outcome.blocked_reason
            else None,
        )
    return success_envelope(
        data=response.model_dump(mode="json"),
        execution_id=str(execution_id),
        versions=counsel_versions(),
    )


__all__ = [
    "counsel_versions",
    "reset_counsel_stores",
    "router",
    "set_counsel_provider",
    "set_counsel_stores",
]
