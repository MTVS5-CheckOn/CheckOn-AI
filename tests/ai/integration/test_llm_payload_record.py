"""LLM 전송 본문 적재 — `LLM_PAYLOAD` 종단 (99 ㉝ · 불변식 3·8).

정본: `docs/06_erd.md` LLM_PAYLOAD · `docs/policies/masking_redaction.md` §3.

🔴 **이 파일의 핵심은 두 가지다.**
① **원문이 저장본에 없다** — 요청은 조립부 (a) redaction이 가리고, 응답은 포착 시점에
   가린다. 환각으로 실명을 만들어도 원장에는 토큰만 남아야 한다.
② **저장본 = 전송분** — `request_masked`를 저장 시점에 다시 마스킹하지 않는다.
   `redact()`가 멱등이 아니라서(2차가 `학생의`를 인명으로 잡는다) 변형하면 **보낸 적 없는
   문면**이 원장에 남고 재현이 깨진다.

실 LLM·실 PG 없음(F-4의 FK 위반만 `integration` 마커로 실 PG에서 돈다).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from ai.agents.job_store import InMemoryJobStore
from ai.agents.supervisor import Supervisor
from ai.api.app import create_app
from ai.api.routers.classify import (
    reset_inquiry_class_store,
    set_classify_run_store,
    set_inquiry_class_store,
)
from ai.api.routers.counsel import reset_counsel_stores
from ai.api.routers.detect import (
    reset_brief_provider,
    reset_detection_store,
    reset_idempotency_store,
    set_brief_provider,
    set_detect_run_store,
)
from ai.composition.counsel.assembly import DEFAULT_REGEN_MAX, build_counsel_gateway
from ai.composition.counsel.enqueue import CounselPackEnqueuer
from ai.composition.counsel.prompt import assemble_prompt
from ai.composition.counsel.provider import (
    FakeCounselLlmProvider,
    GatewayDraftWriter,
    GatewayPlanner,
)
from ai.composition.counsel.stores import (
    InMemoryAgentStepSink,
    InMemoryContextStore,
    InMemoryDraftResultStore,
    InMemoryPackResultStore,
)
from ai.composition.counsel.worker import CounselPackRunner
from ai.contracts.composition import (
    CommStyle,
    DraftContext,
    EvidenceFact,
    Frequency,
    Interest,
    LabelSnapshot,
    Sensitivity,
)
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import CallOutcome, LLMRequest, LLMResult, ModelRole, TokenUsage
from ai.db.models import LlmCall, LlmPayload
from ai.db.repositories.inquiry_class_store import InMemoryInquiryClassStore
from ai.db.repositories.llm_payload import (
    REFUSED_REQUEST_RESIDUE,
    REFUSED_RESPONSE_UNCERTAIN,
    CollectedPayload,
    PayloadCapturingProvider,
    capture_payloads,
    payload_refusal_reason,
)
from ai.db.repositories.run_store import (
    CollectedCall,
    InMemoryRunStore,
    LlmCallCollector,
    accepted_payloads,
    default_llm_call_collector,
)
from ai.db.settings import DbSettings
from ai.db.store_factory import reset_shared_agent_runtime
from ai.evaluation.fake_snapshot import fixture_composite_risk, to_payload
from ai.llm.gateway import LlmCallRecord
from ai.runtime.redaction import redact

_NOW = datetime(2026, 8, 6, tzinfo=UTC)
_HASH = "sha256:" + "a" * 64

#: 🔴 이 두 값이 저장본에 나타나면 마스킹 경계가 뚫린 것이다.
DIRTY_NAME = "김서연"
DIRTY_PHONE = "010-1234-5678"

_DETECT_HEADERS = {
    "X-Tenant-Id": "teacher_alias_001",
    "X-Request-Id": "req-1",
    "Idempotency-Key": "teacher_alias_001:payload",
}
_CLASSIFY_HEADERS = {"X-Tenant-Id": "t1", "X-Request-Id": "req-1"}
_CLASSIFY_BODY = {
    "inquiry_ref": "iq_payload_1",
    "body_text": "이번 모의고사 성적이 어떤지 궁금해서 문의드립니다.",
}

#: `redact()`가 `⟪확인필요⟫`를 내는 문장 — "반 평균"의 `평균`이 인명 후보로 잡힌다
#: (99 D4에 등재된 오탐). 저장 거부 경로를 실제로 태우는 데 쓴다.
#: 🔴 한 문장에 인명 후보 **둘** — 밀도 규칙상 그래야 `uncertain`이 선다
#: (`policies/masking_redaction.md` §2 [A 확정 7/23] · 후보 1개는 토큰만 바꾸고 전송은
#: 막지 않는다). 종전 문면은 후보 1개짜리였다. 이 검사가 보려는 것(「불확실 응답은 저장
#: 거부, HTTP는 200」)은 그대로 두고 불확실을 만드는 조건만 맞췄다.
UNCERTAIN_RESPONSE = "서연이가 민준이랑 비교하면 조금 낮지만 꾸준히 오르고 있습니다."


def _run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


def _counter() -> Callable[[], UUID]:
    state = {"n": 0}

    def _next() -> UUID:
        state["n"] += 1
        return UUID(int=state["n"])

    return _next


def _context(*, dirty: bool = False) -> DraftContext:
    """근거 1건 + (선택) 실명·연락처가 든 상담 메모."""
    facts = [EvidenceFact(label="이번 주 정답률", value="62%")]
    if dirty:
        facts.insert(
            0,
            EvidenceFact(
                label="상담 메모", value=f"{DIRTY_NAME} 어머니 {DIRTY_PHONE} 통화"
            ),
        )
    return DraftContext(
        student_ref="st_1",
        guardian_ref="gd_st_1",
        label_snapshot=LabelSnapshot(
            comm=CommStyle.DATA,
            sensitivity=Sensitivity.ANXIOUS,
            interest=Interest.GRADE,
            frequency=Frequency.FREQUENT,
        ),
        facts=tuple(facts),
        evidence_summaries=(),
        period_label="2026년 7월",
        fallback_text="이번 주 학습 상황을 정리해 보내드립니다.",
    )


def _execution_context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=uuid4(),
        tenant_id="t1",
        capability=Capability.COMPOSITION,
        input_snapshot_hash=_HASH,
        versions=VersionSet(
            pipeline_version="0.1",
            engine_version="counsel-pack-0.1",
            schema_version="0.1",
            contract_version="0.1",
        ),
    )


class _GatewayCounselProvider:
    """plan+write를 같은 게이트웨이로 — 프로덕션 조립부를 그대로 탄다."""

    def __init__(self, text: str = "정답률은 62%였습니다.") -> None:
        gateway = build_counsel_gateway(FakeCounselLlmProvider(text))
        self._planner = GatewayPlanner(gateway)
        self._writer = GatewayDraftWriter(gateway)

    async def plan(self, **kwargs: Any) -> dict[str, list[str]]:  # noqa: ANN401
        return await self._planner.plan(**kwargs)

    async def write(self, **kwargs: Any) -> str:  # noqa: ANN401
        return await self._writer.write(**kwargs)


class _BriefProvider:
    """브리핑 provider 대역 — 응답 문면을 시나리오로 준다."""

    def __init__(self, text: str) -> None:
        self._text = text

    @property
    def name(self) -> str:
        return "fake-brief-payload"

    async def complete(
        self, request: LLMRequest, context: ExecutionContext
    ) -> LLMResult:
        del request, context
        return LLMResult(
            outcome=CallOutcome.OK,
            text=self._text,
            provider=self.name,
            model="template",
            usage=TokenUsage(tokens_in=1, tokens_out=2, cost_usd=0.0),
            latency_ms=1,
        )


class _BoomProvider:
    """전송 실패 대역 — 응답이 없어도 요청 본문은 남아야 한다."""

    @property
    def name(self) -> str:
        return "boom"

    async def complete(
        self, request: LLMRequest, context: ExecutionContext
    ) -> LLMResult:
        del request, context
        raise TimeoutError("전송 실패 시나리오")


def _run_counsel_pack(
    refs: Sequence[str],
    *,
    provider: Any,  # noqa: ANN401
    run_store: InMemoryRunStore,
    contexts: dict[str, DraftContext] | None = None,
) -> tuple[InMemoryAgentStepSink, Any]:
    supervisor = Supervisor(
        store=InMemoryJobStore(),
        lease_duration=timedelta(minutes=5),
        priority_aging_interval=timedelta(minutes=1),
        clock=lambda: _NOW,
    )
    context_store = InMemoryContextStore()
    sink = InMemoryAgentStepSink()
    runner = CounselPackRunner(
        supervisor=supervisor,
        context_store=context_store,
        draft_store=InMemoryDraftResultStore(),
        pack_store=InMemoryPackResultStore(),
        step_sink=sink,
        planner=provider,
        writer=provider,
        checkpointer=InMemorySaver(),
        regen_max=DEFAULT_REGEN_MAX,
        lease_owner="worker-payload",
        new_id=_counter(),
        now=lambda: _NOW,
        run_store=run_store,
    )
    bundle = contexts if contexts is not None else {ref: _context() for ref in refs}

    async def scenario() -> Any:  # noqa: ANN401
        await CounselPackEnqueuer(
            supervisor=supervisor,
            context_store=context_store,
            new_id=_counter(),
            now=lambda: _NOW,
        ).enqueue(tenant_id="t1", class_ref="cl_a1", contexts=bundle)
        return await runner.run_next(tenant_id="t1")

    return sink, _run(scenario())


def _a_record(outcome: CallOutcome = CallOutcome.OK) -> LlmCallRecord:
    return LlmCallRecord(
        role=ModelRole.COUNSELOR,
        prompt_id="composition/counsel_pack",
        prompt_version="0.1",
        provider="fake",
        model="template",
        usage=TokenUsage(tokens_in=1, tokens_out=2, cost_usd=0.0),
        latency_ms=1,
        outcome=outcome,
    )


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    """공용 수집기·인계 슬롯 격리 — 슬롯이 새면 다음 호출이 남의 본문을 물려받는다."""
    default_llm_call_collector().reset()
    reset_idempotency_store()
    reset_detection_store()
    reset_brief_provider()
    reset_inquiry_class_store()
    reset_shared_agent_runtime()  # A·B 공용 잡 원장(99 ㊒)
    reset_counsel_stores()
    yield
    default_llm_call_collector().reset()
    reset_idempotency_store()
    reset_detection_store()
    reset_brief_provider()
    reset_inquiry_class_store()
    reset_shared_agent_runtime()  # A·B 공용 잡 원장(99 ㊒)
    reset_counsel_stores()


# ── F-1 종단: LLM_CALL : PAYLOAD = 1:1 ───────────────────────────


def test_counsel_draft_records_one_payload_per_call() -> None:
    """🔴 호출마다 본문 1건. 종전에는 `LlmPayload` 기록 주체가 `src/`에 0곳이었다(㉝)."""
    runs = InMemoryRunStore()
    _run_counsel_pack(["st_1"], provider=_GatewayCounselProvider(), run_store=runs)

    assert runs.calls, "LLM 호출이 적재되지 않았다 — ㊻ 배선이 깨졌다"
    assert len(runs.payloads) == len(runs.calls)
    for call in runs.calls:
        payload = runs.payload_of(call.id)
        assert payload is not None, f"call {call.id}의 전송 본문이 없다"
        assert payload.call_id == call.id  # FK 대상이 정확히 그 호출이다
        assert payload.request_masked  # 빈 프롬프트를 저장하지 않는다


def test_payload_distinguishes_prompts_that_share_one_prompt_version() -> None:
    """🔴 **㉝의 핵심 주장을 그대로 고정한다** — 같은 `prompt_id@version`이 **다른 문면**을
    보내고, 버전 축으로는 그 차이를 사후에 구분할 방법이 없다.

    게이트 실패 재생성(≤3)이 그 상황을 만든다: 2회차부터 직전 게이트 사유가 수정 지시로
    프롬프트에 붙는데(05 §6-2) `PROMPT_VERSION`은 움직이지 않는다. 근거에 없는 수치를
    내는 provider로 3회 재생성을 유발한다.
    """
    runs = InMemoryRunStore()
    _run_counsel_pack(
        ["st_1"],
        # 88%는 근거(62%)에 없다 → SourceGrounding 실패 → regen_max까지 재시도.
        provider=_GatewayCounselProvider("정답률이 88%까지 올랐습니다."),
        run_store=runs,
    )

    writes = [
        call for call in runs.calls if call.prompt_id == "composition/counsel_pack"
    ]
    # 재생성 3회 = 시도 4회(`range(regen_max + 1)`).
    assert len(writes) == DEFAULT_REGEN_MAX + 1, "재생성이 실제로 일어나지 않았다"
    versions = {(call.prompt_id, call.prompt_version) for call in writes}
    bodies = {runs.payload_of(call.id).request_masked for call in writes}  # type: ignore[union-attr]

    assert len(versions) == 1, "이 테스트는 버전이 하나인 경우를 본다"
    assert len(bodies) > 1, (
        "같은 버전에서 문면이 달라졌는데 본문 기록이 그 차이를 담지 못했다 — ㉝의 공백"
    )


def test_stored_request_is_byte_identical_to_what_was_sent() -> None:
    """🔴 **저장본 = 전송분.** 저장 시점에 다시 마스킹하면 재현이 깨진다.

    `redact()`가 멱등이 아니므로(모듈 docstring) 2차 산출을 저장하면 보낸 적 없는 문면이
    원장에 남는다. 이 단정이 그 유혹을 막는다.
    """
    collector = default_llm_call_collector()
    context = _execution_context()
    writer = GatewayDraftWriter(
        build_counsel_gateway(FakeCounselLlmProvider("정답률은 62%였습니다."))
    )
    draft_context = _context()

    _run(writer.write(context=draft_context, execution_context=context))

    calls = collector.take(context.execution_id)
    assert len(calls) == 1
    payload = calls[0].payload
    assert payload is not None
    assert payload.request_masked == assemble_prompt(draft_context, (), "", "")


def test_failed_call_still_records_the_request_body() -> None:
    """응답이 없어도 **무엇을 보냈는지**는 남는다 — 타임아웃 진단이 프롬프트에 달렸다.

    "응답 없음"과 "빈 응답"의 구분은 `LLM_CALL.outcome`이 한다(㊻의 tokens 0 규약과 동일).
    """
    collector = default_llm_call_collector()
    context = _execution_context()
    writer = GatewayDraftWriter(build_counsel_gateway(_BoomProvider()))

    with pytest.raises(Exception, match="전송 실패"):
        _run(writer.write(context=_context(), execution_context=context))

    calls = collector.take(context.execution_id)
    assert len(calls) == 1
    payload = calls[0].payload
    assert payload is not None
    assert payload.request_masked  # 요청은 남았다
    assert payload.response_masked == ""  # 응답은 없다


# ── F-2 🔴 마스킹 경계: 저장본에 원문 조각 0건 ────────────────────


def test_stored_bodies_contain_no_raw_name_or_phone() -> None:
    """🔴 **수용 기준 2.** 요청·응답 어디에도 원문 실명·연락처가 없다.

    요청은 조립부 (a) redaction이, 응답은 포착 시점 redaction이 가린다. 응답 쪽이 특히
    중요하다 — 프롬프트가 가려져도 **환각으로 실명을 만들 수 있다**(§3 신설 행의 근거).
    """
    runs = InMemoryRunStore()
    hallucinated = f"{DIRTY_NAME} 학생은 {DIRTY_PHONE}로 연락 가능합니다."
    _run_counsel_pack(
        ["st_1"],
        provider=_GatewayCounselProvider(hallucinated),
        run_store=runs,
        contexts={"st_1": _context(dirty=True)},
    )

    assert runs.payloads, "본문이 하나도 적재되지 않아 검증이 헛돈다"
    for payload in runs.payloads.values():
        for field, text in (
            ("request_masked", payload.request_masked),
            ("response_raw", payload.response_raw),
        ):
            assert DIRTY_NAME not in text, f"{field}에 원문 실명이 남았다"
            assert DIRTY_PHONE not in text, f"{field}에 원문 연락처가 남았다"


def test_response_hallucinated_name_is_masked_not_dropped() -> None:
    """환각 실명은 **가려서 남긴다** — 통째로 버리면 환각 자체를 관측할 수 없다."""
    collector = default_llm_call_collector()
    context = _execution_context()
    writer = GatewayDraftWriter(
        build_counsel_gateway(
            FakeCounselLlmProvider(f"{DIRTY_NAME} 학생의 정답률은 62%입니다.")
        )
    )

    _run(writer.write(context=_context(), execution_context=context))

    payload = collector.take(context.execution_id)[0].payload
    assert payload is not None
    assert DIRTY_NAME not in payload.response_masked
    assert "⟪" in payload.response_masked  # 마스킹 토큰으로 남았다
    assert "62%" in payload.response_masked  # 수치는 보존된다(관측 가치)


def test_writer_returns_the_unmasked_text_while_payload_stores_the_masked_one() -> None:
    """🔴 비대칭이 의도다 — 초안 본문은 게이트·DRAFT가 다루고, **원장에는 마스킹본**이 간다.

    두 값을 같게 만들면 하나가 틀린다: 게이트에 마스킹본을 주면 금칙·근거 검사가 어긋나고,
    원장에 원문을 넣으면 불변식 3이 깨진다.
    """
    collector = default_llm_call_collector()
    context = _execution_context()
    hallucinated = f"{DIRTY_NAME} 학생의 정답률은 62%입니다."
    writer = GatewayDraftWriter(
        build_counsel_gateway(FakeCounselLlmProvider(hallucinated))
    )

    returned = _run(writer.write(context=_context(), execution_context=context))

    payload = collector.take(context.execution_id)[0].payload
    assert returned == hallucinated  # 게이트가 볼 것은 원문이다
    assert payload is not None
    assert payload.response_masked != hallucinated  # 원장에 가는 것은 마스킹본이다


# ── F-3 저장 거부: 관측이 기능을 이기지 않는다 ────────────────────


def test_uncertain_response_is_refused_but_the_request_succeeds() -> None:
    """🔴 **수용 기준 4.** `⟪확인필요⟫`가 남은 응답은 저장 거부, HTTP는 **200**."""
    runs = InMemoryRunStore()
    set_detect_run_store(runs)
    set_brief_provider(_BriefProvider(UNCERTAIN_RESPONSE))

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/detect",
            json=to_payload(fixture_composite_risk()),
            headers=_DETECT_HEADERS,
        )

    assert response.status_code == 200, response.text
    assert runs.calls, "호출 메타는 남아야 한다"
    assert runs.refused_payloads >= 1, "uncertain 응답이 거부되지 않았다"
    assert runs.payloads == {}, "거부됐는데 본문이 저장됐다"


def test_refusal_reasons_are_a_closed_set() -> None:
    """저장 직전 훅의 판정 — 요청 잔여와 응답 불확실 두 사유뿐이다."""
    clean = CollectedPayload(UUID(int=1), "정답률은 62%였습니다.", "네", False)
    assert payload_refusal_reason(clean) is None

    residue = CollectedPayload(UUID(int=2), f"{DIRTY_NAME} 학생", "네", False)
    assert payload_refusal_reason(residue) == REFUSED_REQUEST_RESIDUE

    uncertain = CollectedPayload(UUID(int=3), "정답률은 62%였습니다.", "가림", True)
    assert payload_refusal_reason(uncertain) == REFUSED_RESPONSE_UNCERTAIN


def test_request_residue_is_refused_because_the_tripwire_should_have_caught_it() -> None:
    """요청에 `findings`가 남았다면 **트립와이어를 우회한 경로가 있다**는 뜻이다.

    조립부 (a) + `RedactionTripwireTraceHook` 둘을 통과한 프롬프트는 `redact` 결과가
    완전히 깨끗함이 보증된다 — 여기서 걸리는 것은 결함 신호이므로 저장하지 않는다.
    """
    assert redact(f"{DIRTY_NAME} 학생").findings, "테스트 전제가 깨졌다"
    calls = (
        CollectedCall(
            UUID(int=1),
            _a_record(),
            CollectedPayload(UUID(int=1), f"{DIRTY_NAME} 학생", "네", False),
        ),
    )
    accepted, refused = accepted_payloads(calls)
    assert accepted == ()
    assert refused == 1


def test_refusal_does_not_raise_from_the_store() -> None:
    """거부는 예외가 아니다 — 카운터로 남고 호출 기록은 그대로 들어간다(fail-open)."""
    runs = InMemoryRunStore()
    calls = (
        CollectedCall(
            UUID(int=7),
            _a_record(),
            CollectedPayload(UUID(int=7), "정답률은 62%였습니다.", "가림", True),
        ),
    )

    _run(runs.record_calls(execution_id=uuid4(), calls=calls))

    assert len(runs.calls) == 1  # 메타는 남는다
    assert runs.payloads == {}  # 본문은 거부됐다
    assert runs.refused_payloads == 1


# ── F-4 순서 계약: PAYLOAD는 LLM_CALL 뒤 ─────────────────────────


def test_payload_pk_is_a_foreign_key_to_llm_call() -> None:
    """스키마가 순서를 강제한다 — `call_id`가 PK 겸 `llm_call.id` FK다.

    ⇒ LLM_CALL 없이 PAYLOAD를 넣을 수 없다. 인메모리 저장소는 FK를 강제하지 않으므로
    **스키마 사실**과 **배치 불변식**(아래)을 여기서 고정하고, 실제 위반은 아래 `integration`
    테스트가 실 PG에서 본다.
    """
    column = LlmPayload.__table__.c.call_id
    assert column.primary_key
    targets = {fk.target_fullname for fk in column.foreign_keys}
    assert targets == {"llm_call.id"}


def test_every_persisted_payload_has_its_call_in_the_same_batch() -> None:
    """배치 불변식 — 저장된 본문의 `call_id`는 같은 배치의 LLM_CALL 중에 있다."""
    runs = InMemoryRunStore()
    _run_counsel_pack(
        ["st_1", "st_2"], provider=_GatewayCounselProvider(), run_store=runs
    )

    call_ids = {call.id for call in runs.calls}
    assert runs.payloads
    assert set(runs.payloads) <= call_ids


@pytest.mark.integration
def test_payload_without_llm_call_violates_fk_on_real_pg() -> None:
    """🔴 실 PG — LLM_CALL 없이 PAYLOAD를 넣으면 FK 위반이다(순서가 계약이다).

    sqlite로 대체하지 않는다(JSONB·Uuid 미지원 — 프로덕션과 다른 DB로 통과시키면 거짓 초록).
    PG 미가용이면 skip한다.
    """
    from sqlalchemy import text as sql_text
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from ai.db.models import Base
    from ai.db.settings import get_db_settings

    async def scenario() -> str:
        engine = create_async_engine(
            get_db_settings().database_url, poolclass=NullPool
        )
        try:
            try:
                async with engine.connect() as conn:
                    await conn.execute(sql_text("SELECT 1"))
            except Exception:  # noqa: BLE001 — PG 미가용은 skip 사유다
                return "skip"
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            from sqlalchemy.ext.asyncio import async_sessionmaker

            maker = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with maker() as session, session.begin():
                    session.add(
                        LlmPayload(
                            call_id=uuid4(),  # 존재하지 않는 호출
                            request_masked="p",
                            response_raw="r",
                        )
                    )
            except IntegrityError:
                return "fk_violation"
            return "accepted"
        finally:
            await engine.dispose()

    outcome = _run(scenario())
    if outcome == "skip":
        pytest.skip("PG 미가용 — 실 FK 검증은 DB가 있을 때만 돈다")
    assert outcome == "fk_violation"


# ── F-5 상한: 호출 상한과 본문 길이 상한은 다른 것이다 ───────────


def test_oversized_body_is_dropped_but_the_call_meta_survives() -> None:
    """🔴 본문 길이 상한 — **본문만** 버리고 메타는 남긴다 + 카운터.

    ⚠ 절단하지 않는다. 잘린 프롬프트는 "재현 가능해 보이지만 아닌" 기록이라 조용히 틀린다.
    """
    collector = LlmCallCollector()
    tiny = DbSettings(llm_payload_max_chars=10)
    provider = PayloadCapturingProvider(
        FakeCounselLlmProvider("정답률은 62%였습니다."), settings=tiny
    )
    gateway = build_counsel_gateway(provider, recorder=collector)
    context = _execution_context()

    _run(
        GatewayDraftWriter(gateway).write(
            context=_context(), execution_context=context
        )
    )

    calls = collector.take(context.execution_id)
    assert len(calls) == 1  # 메타는 남았다
    assert calls[0].payload is None  # 본문은 버렸다
    assert collector.dropped_payloads == 1


def test_call_limit_drops_the_whole_call_not_just_the_body() -> None:
    """호출 상한(㊻)과 본문 상한은 **다른 상한**이다 — 전자는 호출 자체를 버린다.

    지시서 F-5는 둘을 한 문장으로 묶었지만 동작이 다르다: 호출 상한을 넘긴 건은 메타도
    남지 않는다(㊻에서 확정한 거동이고 `dropped_calls`가 센다).
    """
    collector = LlmCallCollector(max_calls_per_run=1)
    context = _execution_context()
    for _ in range(3):
        collector(_a_record(), context)

    assert len(collector.peek(context.execution_id)) == 1
    assert collector.dropped_calls == 2
    assert collector.dropped_payloads == 0  # 본문 상한과 무관하다


def test_dropped_call_does_not_leak_its_body_to_the_next_call() -> None:
    """🔴 호출 상한으로 버린 건의 포착 본문이 다음 호출에 붙으면 **짝이 틀린 원장**이 된다.

    없는 원장보다 나쁘다 — 수집기는 슬롯을 먼저 비운다.
    """
    collector = LlmCallCollector(max_calls_per_run=1)
    context = _execution_context()
    provider = PayloadCapturingProvider(FakeCounselLlmProvider("첫 응답입니다."))
    gateway = build_counsel_gateway(provider, recorder=collector)
    writer = GatewayDraftWriter(gateway)

    _run(writer.write(context=_context(), execution_context=context))  # 1건째 — 통과
    _run(writer.write(context=_context(), execution_context=context))  # 2건째 — 버려짐

    kept = collector.peek(context.execution_id)
    assert len(kept) == 1
    assert collector.dropped_calls == 1
    # 버려진 2건째의 본문이 어디에도 남지 않는다(슬롯이 비워졌다).
    from ai.db.repositories.llm_payload import take_captured

    assert take_captured() is None


# ── F-6 3경로 전부 (㊻의 교훈) ────────────────────────────────────


def test_briefing_path_records_payload() -> None:
    """narrator 경로 — 감지 AI_RUN에 매달린 호출에도 본문이 붙는다."""
    runs = InMemoryRunStore()
    set_detect_run_store(runs)
    set_brief_provider(_BriefProvider("제출률이 최근 낮아졌습니다."))

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/detect",
            json=to_payload(fixture_composite_risk()),
            headers=_DETECT_HEADERS,
        )

    assert response.status_code == 200, response.text
    assert runs.calls
    assert len(runs.payloads) == len(runs.calls)


def test_classify_path_records_payload() -> None:
    """classifier 경로 — 분류 프롬프트도 본문으로 남는다."""
    runs = InMemoryRunStore()
    set_classify_run_store(runs)
    set_inquiry_class_store(InMemoryInquiryClassStore())

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/classify", json=_CLASSIFY_BODY, headers=_CLASSIFY_HEADERS
        )

    assert response.status_code == 200, response.text
    assert runs.calls
    assert len(runs.payloads) == len(runs.calls)
    payload = runs.payload_of(runs.calls[0].id)
    assert payload is not None
    assert "문의" in payload.request_masked  # 분류 프롬프트 문면이다


def test_counsel_path_records_payload() -> None:
    """counselor 경로 — F-1이 이미 보지만 3경로 표를 여기서 닫는다."""
    runs = InMemoryRunStore()
    _run_counsel_pack(["st_1"], provider=_GatewayCounselProvider(), run_store=runs)
    assert runs.payloads
    assert {call.role for call in runs.calls} == {"counselor"}


# ── 래퍼 계약 ─────────────────────────────────────────────────────


def test_wrapper_delegates_provider_name() -> None:
    """🔴 이름을 위임한다 — 게이트웨이가 provider **이름**으로 generator↔verifier 동일
    패밀리를 막는데, 래퍼가 자기 이름을 내면 그 검사가 무력해진다."""
    inner = FakeCounselLlmProvider()
    assert capture_payloads(inner).name == inner.name


def test_wrapper_does_not_alter_the_result() -> None:
    """관측이 값을 바꾸지 않는다 — 래핑 전후 `LLMResult`가 같아야 한다."""
    inner = FakeCounselLlmProvider("동일 응답")
    request = LLMRequest(
        role=ModelRole.COUNSELOR,
        prompt="프롬프트",
        prompt_id="composition/counsel_pack",
        prompt_version="0.1",
    )
    context = _execution_context()

    direct = _run(inner.complete(request, context))
    wrapped = _run(capture_payloads(inner).complete(request, context))

    assert direct == wrapped


def test_calls_and_payloads_stay_one_to_one_under_concurrency() -> None:
    """🔴 동시 호출이 서로의 본문을 물려받지 않는다 — 인계 슬롯이 태스크 로컬이다.

    브리핑은 신호별 호출을 동시에 3개까지 돌린다(`detect.py` 세마포어). 전역 슬롯이면
    짝이 뒤섞이고, 그건 없는 원장보다 나쁘다.
    """
    collector = LlmCallCollector()
    context = _execution_context()
    texts = [f"응답 {i}" for i in range(6)]

    async def scenario() -> None:
        await asyncio.gather(
            *(
                build_counsel_gateway(
                    FakeCounselLlmProvider(text), recorder=collector
                ).complete(
                    LLMRequest(
                        role=ModelRole.COUNSELOR,
                        prompt=f"프롬프트 {index}",
                        prompt_id="composition/counsel_pack",
                        prompt_version="0.1",
                    ),
                    context,
                )
                for index, text in enumerate(texts)
            )
        )

    _run(scenario())

    calls = collector.take(context.execution_id)
    assert len(calls) == len(texts)
    pairs = {
        (call.payload.request_masked, call.payload.response_masked)
        for call in calls
        if call.payload is not None
    }
    assert len(pairs) == len(texts), "동시 호출의 요청·응답 짝이 섞였다"
    for prompt, response in pairs:
        index = prompt.removeprefix("프롬프트 ")
        assert response == f"응답 {index}", "요청과 응답이 다른 호출의 것끼리 붙었다"


def test_llm_call_and_payload_orm_columns_are_covered() -> None:
    """ORM 컬럼을 빠뜨리지 않았는지 — `LlmPayload`는 3컬럼 전부를 채운다."""
    assert set(LlmPayload.__table__.columns.keys()) == {
        "call_id",
        "request_masked",
        "response_raw",
    }
    assert "created_at" not in LlmPayload.__table__.columns  # TTL은 llm_call 조인이다
    assert "created_at" in LlmCall.__table__.columns
