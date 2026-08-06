"""counsel 초안 라우터 — `/v1/counsel/drafts` (인박스 계약 v1 §4 · 04 §3.9).

소유: 박진희 (composition 라우터). 계약(`contracts/counsel`)과 기존 counsel_pack 워커를
HTTP로 노출한다. **요청 단위 = 문의 1건**이며 워커는 새로 만들지 않는다 — 학생 묶음 워커를
**N=1 축퇴 사례로 재사용**한다(99 D ㉛). 저장(`pack://`·`draft://`)·재개·수렴·서킷 경로는
그대로다.

**동기 실행(선례: `imports` 라우터).** enqueue → 같은 요청 안에서 러너를 1회 돌려 결과를
확정하고 202를 낸다. 실 비동기 워커 루프·Kafka 완료 통지는 후속이며, 그때 이 라우터는
enqueue까지만 하고 GET이 잡 상태를 읽는 형태로 좁아진다(계약은 그대로다 — BE는 이미
202 → Kafka → GET 순서로 쓴다).

🔴 **provider에 기본값이 없다** — 조립 루트가 `set_counsel_provider()`를 부르지 않으면
**기동이 실패한다**. 종전 기본값은 `FakeCounselProvider()`였고, 그건 배선 실수를 조용한
날조 산출로 바꿨다(Fake의 `fallback_text`는 게이트를 통과하고 원장에도 안 남는다).
CI·테스트는 Fake를 **명시적으로** 꽂고(`reset_counsel_stores`), 실 경로는
`GatewayPlanner`+`GatewayDraftWriter`를 만족하는 한 객체를 꽂는다.

**러너는 `assembly.open_counsel_pack_runner`로만 만든다** — 직접 생성하면
`require_tracing_disabled`(불변식 3)와 체크포인터 선택이 서비스 경로에서만 빠진다.

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
from typing import Any, Final

from fastapi import APIRouter, Request, Response
from pydantic import ValidationError

from ai.agents.supervisor import Supervisor, system_utc_now
from ai.api.envelope import success_envelope
from ai.composition.counsel.assembly import (
    build_counsel_llm_provider,
    build_counsel_provider,
    open_counsel_pack_runner,
)
from ai.composition.counsel.enqueue import CounselPackEnqueuer
from ai.composition.counsel.labels import LabelVocabularyError, snapshot_from_labels
from ai.composition.counsel.provider import (
    COUNSEL_GEN_PARAMS,
    CounselPlanner,
    DraftWriter,
    FakeCounselProvider,
)
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
    InquiryTopic,
    RefineRequest,
    RefineResponse,
    WireDraftStatus,
    wire_status_for,
)
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.gates import BlockedReason
from ai.contracts.llm import LlmError
from ai.db.repositories.idempotency import IdempotencyStore
from ai.db.repositories.run_store import (
    RunStore,
    default_llm_call_collector,
)
from ai.db.store_factory import (
    build_agent_job_store,
    build_idempotency_store,
    build_run_store,
)
from ai.runtime.errors import (
    IdempotencyConflict,
    NotFound,
    SnapshotInvalid,
    domain_error_for,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_PIPELINE_VERSION = "0.1.0"
_ENGINE_VERSION = "counsel-pack-0.1"
_SCHEMA_VERSION = "0.1"
_CONTRACT_VERSION = "0.1"

_REQUIRED_HEADERS = ("X-Tenant-Id", "X-Request-Id", "Idempotency-Key")
_POST_ENDPOINT = "POST /v1/counsel/drafts"

#: 학습 데이터가 필요 없는 문의 유형 — `template_only` 경로(error_codes §2.1 · 03 §C 상황 2).
#: ⚠ **`schedule` 한 종만이다.** `etc`를 넣지 않는 이유는 **오분류의 비대칭**이다 —
#: 데이터 유관 문의를 무관으로 잘못 보면 근거가 있는데도 일반 안내만 나가 **기능이 사라지고**,
#: 반대로 무관 문의를 유관으로 보면 근거 부족 → `rejected_insufficient`(정직한 거부)로
#: **안전하게 수렴**한다. 넓히려면 이 상수만 고치면 된다.
_NO_DATA_TOPICS: Final[frozenset[InquiryTopic]] = frozenset({InquiryTopic.SCHEDULE})

#: 게이트 실패 재생성 상한 — ERD DRAFT_BLOCK "≤3"(불변식 6).
_REGEN_MAX = 3
_LEASE_OWNER = "counsel-router"

#: `result`를 실을 수 있는 phase — **결과 계약이 확정된 상태**만이다(error_codes §2.5).
#: 나머지(queued·leased·running·paused·cancelled)는 결과가 **아직/영영 없는** 것이고,
#: 🔴 그걸 `llm_failed`로 보고하면 `status="queued"` + `draft_status="llm_failed"`라는
#: **모순 조합**이 나간다 — 잡은 살아 있는데 BE는 "다시 시도"를 그리고, 강사가 누르면
#: 초안이 2개 생긴다. 계약의 `result`가 옵셔널인 이유가 이것이다.
_REPORTABLE_PHASES: Final[frozenset[JobPhase]] = frozenset(
    {JobPhase.SUCCEEDED, JobPhase.FAILED}
)

#: 시계 주입점 — `datetime.now()` 직접 호출 금지(03 §3).
_clock = system_utc_now

_idempotency_store: IdempotencyStore = build_idempotency_store()
#: 실행 원장(AI_RUN·LLM_CALL) — refine 턴이 직접 쓴다. POST 경로는 워커가 쓴다
#: (LLM 호출이 `job.execution_id` 아래에서 일어나므로 그 실행의 주인이 워커다).
_run_store: RunStore = build_run_store()
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

#: LLM 접점 — 🔴 **기본값이 없다.** 조립부가 주입하지 않으면 서비스는 뜨지 않는다.
#:
#: ⚠ 종전 기본값은 `FakeCounselProvider()`였다. Fake는 시나리오가 없으면
#: `context.fallback_text`("이번 기간 학습 상황을 정리해 보내드립니다.")를 돌려주는데, 그
#: 문장은 숫자·금칙어가 없어 **게이트를 그대로 통과**한다 — `draft_status=generated` +
#: `citations`가 붙은 정상 초안으로 학부모에게 나가고, LLM 호출이 0건이라
#: **AI_RUN·LLM_CALL에도 남지 않아 사후 추적으로도 구분되지 않는다.**
#: `assembly.open_counsel_pack_runner`가 같은 이유로 이미 폴백을 제거했고
#: ("조용한 Fake가 최악"), 이 라우터에만 남아 있었다.
_provider: Any = None


class CounselProviderNotWired(RuntimeError):
    """counsel provider 미배선·부분 배선 — **기동 시점**에 터뜨린다.

    첫 요청까지 미루면 Fake 산출이 나가거나(구 기본값) `worker_internal_error`가 되는데,
    둘 다 배포 후에야 드러난다.
    """


#: provider가 만족해야 할 Protocol — 워커가 **plan·write 둘 다** 호출한다.
#: ⚠ `runtime_checkable`은 **메서드 존재만** 본다(시그니처는 안 본다). 그래도
#: "planner 없는 writer만 꽂혔다"는 이 PR이 잡으려는 실수는 전부 걸린다.
_PROVIDER_PROTOCOLS: Final = (("plan", CounselPlanner), ("write", DraftWriter))


def set_counsel_provider(provider: Any) -> None:  # noqa: ANN401 — Planner+Writer 이중 Protocol
    """plan·write provider 주입 — 테스트 시나리오·실 LLM 배선의 seam.

    🔴 한 객체가 `CounselPlanner`·`DraftWriter` **양쪽**을 만족해야 한다. 종전에는 검사가
    없어 `write`만 있는 객체를 꽂아도 기동이 통과하고 **첫 요청에서** `worker_internal_error`가
    났다 — 배선 실수는 배선 시점에 터지는 게 맞다.
    """
    missing = [
        name for name, protocol in _PROVIDER_PROTOCOLS
        if not isinstance(provider, protocol)
    ]
    if missing:
        raise CounselProviderNotWired(
            f"counsel provider가 {', '.join(missing)}을(를) 구현하지 않는다 "
            f"({type(provider).__name__}) — 워커가 plan·write 둘 다 호출한다"
        )
    global _provider
    _provider = provider


def require_counsel_provider() -> Any:  # noqa: ANN401 — Planner+Writer 이중 Protocol
    """배선 확인 — 미배선이면 **기동을 막는다**(아래 startup 훅이 부른다).

    ⚠ `api/app.py`는 양자 승인 파일이라 라우터가 자기 startup 훅을 들고 간다
    (`include_router`가 앱으로 옮겨 준다). 앱 조립부를 고치지 않고도 같은 시점에 터진다.
    """
    if _provider is None:
        raise CounselProviderNotWired(
            "counsel provider가 배선되지 않았다 — `set_counsel_provider()`를 부르지 않으면 "
            "초안 경로를 띄우지 않는다. 조용한 Fake 폴백은 제거됐다(날조 산출 저장 방지). "
            "테스트·CI는 `reset_counsel_stores()`가 Fake를 명시적으로 꽂는다."
        )
    return _provider


def bootstrap_counsel_provider() -> None:
    """조립 루트 — 기동 시 env를 보고 provider를 만들어 꽂는다(`LLM_PROVIDER`).

    🔴 **이미 배선돼 있으면 덮지 않는다.** 테스트·평가 러너는 자기 시나리오 provider를
    명시 주입하는데, 조립 루트가 그걸 갈아치우면 주입 seam이 무의미해진다.

    ⚠ fake가 선택되는 것은 **사고가 아니라 선택**이다 — `build_counsel_llm_provider`가
    경고 로그를 남기고 산출물에도 `fake-counsel`이 적힌다. 반면 이 함수가 아예 안 돌면
    아래 `require_counsel_provider`가 기동을 막는다. 둘은 다른 사건이다.
    """
    if _provider is not None:
        return
    set_counsel_provider(build_counsel_provider(build_counsel_llm_provider()))


def _startup() -> None:
    """기동 순서 = **조립 → 확인**. 확인이 뒤라 조립 루트가 없거나 실패하면 걸린다.

    ⚠ 조립 루트를 **전역 이름으로** 부른다 — 테스트가 `bootstrap_counsel_provider`를
    비워 "조립 루트 부재"를 재현할 수 있어야 하기 때문이다(핸들러가 함수 객체를 잡아
    두면 monkeypatch가 안 먹는다).
    """
    bootstrap_counsel_provider()
    require_counsel_provider()


router.add_event_handler("startup", _startup)


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


def set_counsel_run_store(store: RunStore) -> None:
    """실행 원장 주입 — 테스트가 AI_RUN·LLM_CALL 적재를 관측하는 seam."""
    global _run_store
    _run_store = store


def reset_counsel_stores() -> None:
    """테스트 격리용 — 저장소·읽기 모델·provider를 기본값으로 되돌린다."""
    global _idempotency_store, _context_store, _draft_store, _pack_store, _step_sink
    global _run_store
    _run_store = build_run_store()
    default_llm_call_collector().reset()
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
        # 🔴 학부모가 실제로 물은 것 — 종전엔 아무도 안 읽어서 같은 학생·같은 라벨이면
        # 어떤 문의든 바이트 동일한 프롬프트가 나왔다. BE 1차 마스킹분이고, 전송 직전
        # `redact()`를 한 번 더 탄다(fail-closed · 불변식 3).
        inquiry_text=request.inquiry.text_masked,
    )


#: 차단 사유별 강사 문구 — 원본은 `part_a/06_refine_policy.md` §4 표다(여기서 새로 만들지
#: 않는다). 🔴 **응답에는 싣지 않는다**(8/5) — BE가 `blocked_reason`으로 이 표를 조회해
#: 문구를 붙인다. 여기 남겨 둔 것은 골든 테스트가 그 매핑의 기대값으로 쓰기 때문이다.
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

    # ① 🔴 데이터 무관 문의 — **근거 선검사보다 앞**이다(순서가 계약이다).
    #    시간표 문의 + 신규생(근거 0건)이면 뒤에 두었을 때 "아직 데이터를 모으는
    #    중이에요"가 나가는데, **시간표 답변에 학습 데이터는 애초에 필요 없다.**
    #    근거 유무와 무관하게 template_only여야 한다(03 §C 상황 2).
    #
    #    ⚠ `text`를 비운다 — **안내 문구는 AI가 만들지 않는다.** 근거 3겹:
    #      ⓐ LLM이 문장을 지어내면 **근거 0건 산출물**이라 불변식 2 위반이고, 통과시킬
    #        근거가 없어 게이트를 세울 수 없다
    #      ⓑ `error_codes` §2.1의 **"백엔드 표시 문구"** 열이 원본이라 BE 소유다
    #      ⓒ §2.7 **규칙 ③ "표시 문구는 AI가 주지 않는다"** — 8/5에 `RefineResponse.message`를
    #        제거해 세운 전 엔드포인트 규약이다. 여기 문구를 실으면 그걸 되돌리게 된다
    #
    #    ⚠ **`confidence`를 보지 않는다** — 강등 판단의 주체는 **BE**다(04 §3.5).
    #    요청의 `inquiry`에 `confidence` 필드 자체가 없고, AI는 받은 `topic`을
    #    **확정값으로 신뢰**한다. 오분류였다면 정정 경로(§3.9)가 되돌린다.
    #
    #    LLM 0회 · 잡 적재 없음 · `_drafts` 미등록(초안이 없으니 refine 대상이 아니다).
    if request.inquiry.topic in _NO_DATA_TOPICS:
        return CounselDraftJobView(
            job_id=str(uuid.uuid4()),
            status=JobPhase.SUCCEEDED.value,
            result=CounselDraftResult(
                draft_status=WireDraftStatus.TEMPLATE_ONLY,
                status_reason="no_data_topic",  # error_codes §2.1 정본
                labels_applied=applied,
                generated_at=_clock(),
            ),
        )

    # ② 근거 0건 — 여기부터는 **데이터 유관 문의**다.
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
    provider = require_counsel_provider()
    # 🔴 조립부를 경유한다 — 러너를 여기서 직접 만들면 `require_tracing_disabled`와
    # 체크포인터 선택(`_open_saver`)이 **서비스 경로에서만 빠진다**. 실제로 그랬다:
    # `LANGSMITH_TRACING=true`여도 이 엔드포인트는 그냥 돌았다.
    async with open_counsel_pack_runner(
        supervisor=supervisor,
        context_store=_context_store,
        step_sink=_step_sink,
        draft_store=_draft_store,
        pack_store=_pack_store,
        planner=provider,
        writer=provider,
        regen_max=_REGEN_MAX,
        lease_owner=_LEASE_OWNER,
        # 실행 원장은 라우터와 **같은 인스턴스**를 쓴다 — 워커가 기본 팩토리로 따로 만들면
        # 테스트가 주입한 저장소를 우회해 적재를 관측할 수 없다.
        run_store=_run_store,
    ) as runner:
        # 🔴 `run_next`는 `worker_kind + tenant_id`로만 lease한다 — **job_id를 지정해 집을
        # 수 없다.** 큐에 남의 잡이 남아 있으면 이게 집어오는 건 내 잡이 아니다. 그래도
        # 호출은 유지한다(큐를 비우는 역할이 있다) — 바꾼 것은 **결과를 어디서 읽는가**다.
        ran = await runner.run_next(tenant_id=tenant_id)
        if ran is not None and ran.job_id != job.job_id:
            logger.info(
                "counsel 러너가 다른 잡을 실행했다 mine=%s ran=%s — 결과는 내 잡에서 읽는다",
                job.job_id,
                ran.job_id,
            )
        # 🔴 **이 요청은 자기 잡의 결과만 읽는다.** 종전에는 `ran`(남의 잡일 수 있다)의
        # `result_ref`에서 본문을 꺼내 내 `job_id`·`citations`와 함께 반환했다 —
        # 다른 학생의 초안이 나가고 `_drafts`에도 등록돼 refine까지 오염됐다.
        mine = await supervisor.get(tenant_id=tenant_id, job_id=job.job_id) or job
        view_job_id = str(job.job_id)
        if mine.phase not in _REPORTABLE_PHASES:
            # 아직 안 돌았다(queued·leased·running·paused) 또는 취소됐다. **결과가 없다는
            # 것과 실패는 다르다** — `result=None` + 잡 phase가 정직한 표현이다
            # (error_codes §2.1 "queued/generating → 스피너", 계약 `result`가 옵셔널).
            return CounselDraftJobView(
                job_id=view_job_id, status=mine.phase.value, result=None
            )
        result = await _wire_result(
            runner, mine, request, applied, job_id=view_job_id, tenant_id=tenant_id
        )
        return CounselDraftJobView(
            job_id=view_job_id, status=mine.phase.value, result=result
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
        await _draft_store.get(
            make_ref(DRAFT_SCHEME, student.draft_id), tenant_id=tenant_id
        )
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


async def _record_refine_run(
    refine_context: ExecutionContext,
    execution_id: uuid.UUID,
    *,
    swallow_errors: bool = False,
) -> None:
    """refine 턴 1회를 원장에 남긴다 — 성공·차단·**장애** 전부.

    ⚠ `take(execution_id)`는 **어느 경로에서도 반드시** 불려야 한다. 안 부르면 수집기에
    레코드가 남아 다음 실행에 섞인다(누수).

    `swallow_errors`는 장애 경로 전용이다 — 적재 실패가 원인 예외를 덮으면 진단이
    뒤집힌다("LLM이 죽었다" → "원장이 죽었다"). 정상 경로에서는 fail-closed 그대로 올린다.
    """
    calls = default_llm_call_collector().take(execution_id)
    last = calls[-1].record if calls else None
    try:
        await _run_store.record_run(
            refine_context.to_run_metadata(
                created_at=_clock(),
                model_provider=last.provider if last is not None else None,
                model_name=last.model if last is not None else None,
                generation_params=COUNSEL_GEN_PARAMS,
            ),
            calls,
        )
    except Exception:  # noqa: BLE001 — 원인 예외를 덮지 않는다(위 docstring)
        if not swallow_errors:
            raise
        logger.exception("refine 장애 턴의 원장 적재 실패 — 원인 예외를 유지한다")


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
    refine_context = _refine_execution_context(execution_id, tenant_id)
    #: 🔴 `finally`가 성공·차단·**모든 종류의 실패**를 지나게 하려고 둔 플래그다.
    #:  실패 경로에서만 적재 오류를 삼킨다(성공·차단은 fail-closed 그대로).
    failed = True
    try:
        outcome = await refine_draft(
            context=state.context,
            instruction=refine_request.instruction,
            # 🔴 전역 `_provider`를 직접 읽지 않는다. POST 경로는 이미 이걸 쓰는데
            # **refine만 우회**하고 있었다 — #108이 "조용한 Fake 금지"를 세웠는데 이 한 줄이
            # 빠져 CI는 초록이었다(같은 패턴 5번째). 가드는 `test_provider_access_guard`.
            writer=require_counsel_provider(),
            execution_context=refine_context,
            regen_max=_REGEN_MAX,
            # 🔴 **누적의 배선.** `state.text`는 종전에 write-only였다(읽는 코드 0곳) —
            # 매 턴 원본에서 새로 써서 턴1의 반영이 턴2에서 되살아났다. 팀 공유본
            # (와이어프레임 v3.5·프로토타입·데이터계약)이 전부 누적을 전제로 만들어져 있다.
            previous_text=state.text,
        )
        failed = False
    except LlmError as exc:
        # 변환은 **LLM 예외만의 일**이다 — `RedactionUncertain`은 이미 `DomainException`이라
        # 변환 없이 그대로 500으로 나간다(그래서 여기 절이 필요 없다).
        raise domain_error_for(exc) from exc
    finally:
        # 🔴 **성공·차단·모든 종류의 실패가 여기를 지난다.** 예외 종류가 늘어도 원장은
        #   안 갈린다. 종전에는 이 부수효과가 `except LlmError` 절 **안에** 복제돼 있어서,
        #   그 절이 못 잡는 예외(`RedactionUncertain` — `DomainException`)만 원장에서
        #   사라졌다(8/7 실측: AI_RUN 0 · 수집기 잔존 1).
        #   ⚠ `except DomainException`을 **추가**해서 때우지 않았다 — 그건 같은 복제를 한 번
        #   더 하는 것이고 다음 예외에서 또 빠진다. 절이 아니라 구조를 고친다.
        # ⚠ `finally`는 예외가 위로 전파되기 **전에** 돈다 — 적재가 raise보다 먼저다.
        # 실행 원장 — refine 턴도 하나의 실행이다(불변식 8). 차단 턴도 남긴다: 차단은 에러가
        # 아니고(불변식 4) 어떤 호출이 무엇을 냈길래 게이트가 걸렸는지가 정확히 추적 대상이다.
        # ⚠ `quota_consumed`(pack state)에는 들어가지 않는다 — 이 경로는 그래프 밖이다.
        # ⚠ 실패 경로에서만 적재 오류를 삼킨다 — `LedgerWriteFailed`가 원인 예외를 덮으면
        #   "LLM이 죽었다"가 "원장이 죽었다"로 바뀌어 진단이 뒤집힌다.
        await _record_refine_run(
            refine_context, execution_id, swallow_errors=failed
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
        # ⚠ 문구(`REFINE_BLOCK_MESSAGES`)는 **응답에 싣지 않는다**(8/5) — 표시 문구는
        # BE 소유다(error_codes §2.6 규칙 3). 표 자체는 `part_a/06` §4의 투영이라
        # 남겨 둔다(골든 테스트가 BE 매핑의 기대값으로 참조한다).
        response = RefineResponse(
            applied=False, blocked_reason=outcome.blocked_reason
        )
    return success_envelope(
        data=response.model_dump(mode="json"),
        execution_id=str(execution_id),
        versions=counsel_versions(),
    )


__all__ = [
    "CounselProviderNotWired",
    "bootstrap_counsel_provider",
    "counsel_versions",
    "require_counsel_provider",
    "set_counsel_run_store",
    "reset_counsel_stores",
    "router",
    "set_counsel_provider",
    "set_counsel_stores",
]
