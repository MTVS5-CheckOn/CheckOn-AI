"""LLM 호출 관측 배선 종단 — recorder·quota·llm_call_id·seed (99 ㊻·ⓕ·㊼).

정본: `docs/06_erd.md` AI_RUN·LLM_CALL · `CLAUDE.md` 불변식 8 · §7(쿼터는 백엔드).

🔴 **이 파일이 막는 회귀는 "재료는 만들어지는데 아무 데도 안 남는다"다.** 실 LLM 스모크
(8/4)에서 호출 34건·토큰 19,814이 발생했는데 조립부가 `recorder`를 넘기지 않아 전량
폐기됐다. 배선은 눈에 보이지 않으므로 **경로별로** 고정한다 — 한 경로만 검증하면 다음
경로가 조용히 빠진다(그게 정확히 ㊻의 형태였다).

실 LLM·실 PG 없음: Fake provider + `InMemoryRunStore`로 결정론화한다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Iterator, Mapping, Sequence
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
    set_detect_run_store,
    set_detection_store,
)
from ai.composition.briefing import BRIEF_GEN_PARAMS
from ai.composition.classify.classifier import CLASSIFY_GEN_PARAMS
from ai.composition.counsel.assembly import DEFAULT_REGEN_MAX, build_counsel_gateway
from ai.composition.counsel.enqueue import CounselPackEnqueuer
from ai.composition.counsel.graph import build_counsel_graph
from ai.composition.counsel.prompt import assemble_prompt
from ai.composition.counsel.provider import (
    COUNSEL_GEN_PARAMS,
    FakeCounselLlmProvider,
    FakeCounselProvider,
    GatewayDraftWriter,
    GatewayPlanner,
)
from ai.composition.counsel.state import CounselPackState
from ai.composition.counsel.stores import (
    InMemoryAgentStepSink,
    InMemoryContextStore,
    InMemoryDraftResultStore,
    InMemoryPackResultStore,
)
from ai.composition.counsel.worker import CounselPackRunner
from ai.composition.determinism import LLM_SEED, deterministic_params
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
from ai.contracts.llm import CallOutcome, LLMRequest, LLMResult, TokenUsage
from ai.db.repositories.detection_store import InMemoryDetectionStore
from ai.db.repositories.inquiry_class_store import InMemoryInquiryClassStore
from ai.db.repositories.run_store import (
    CollectedCall,
    InMemoryRunStore,
    default_llm_call_collector,
)
from ai.db.store_factory import reset_shared_agent_runtime
from ai.evaluation.fake_snapshot import fixture_composite_risk, to_payload

_NOW = datetime(2026, 8, 5, tzinfo=UTC)
_HASH = "sha256:" + "a" * 64

_DETECT_HEADERS = {
    "X-Tenant-Id": "teacher_alias_001",
    "X-Request-Id": "req-1",
    "Idempotency-Key": "teacher_alias_001:obs",
}
_CLASSIFY_HEADERS = {"X-Tenant-Id": "t1", "X-Request-Id": "req-1"}
_CLASSIFY_BODY = {
    "inquiry_ref": "iq_obs_1",
    "body_text": "이번 모의고사 성적이 어떤지 궁금해서 문의드립니다.",
}


def _run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


def _counter() -> Callable[[], UUID]:
    state = {"n": 0}

    def _next() -> UUID:
        state["n"] += 1
        return UUID(int=state["n"])

    return _next


def _context(student_ref: str) -> DraftContext:
    return DraftContext(
        student_ref=student_ref,
        guardian_ref=f"gd_{student_ref}",
        label_snapshot=LabelSnapshot(
            comm=CommStyle.DATA,
            sensitivity=Sensitivity.ANXIOUS,
            interest=Interest.GRADE,
            frequency=Frequency.FREQUENT,
        ),
        facts=(EvidenceFact(label="이번 주 정답률", value="62%"),),
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
    """plan+write를 **같은 게이트웨이**로 낸다 — 워커가 요구하는 이중 Protocol.

    프로덕션 조립부(`build_counsel_gateway`)를 그대로 쓴다. 게이트웨이를 테스트에서 직접
    만들면 트레이스 마스킹 화이트리스트(정적 검사)를 우회하게 되고, 무엇보다 **조립부가
    recorder를 넘기는지**가 이 파일의 검증 대상이라 조립부를 건너뛰면 의미가 없다.
    """

    def __init__(self, text: str = "정답률은 62%였습니다.") -> None:
        gateway = build_counsel_gateway(FakeCounselLlmProvider(text))
        self.gateway = gateway
        self._planner = GatewayPlanner(gateway)
        self._writer = GatewayDraftWriter(gateway)

    async def plan(self, **kwargs: Any) -> dict[str, list[str]]:  # noqa: ANN401
        return await self._planner.plan(**kwargs)

    async def write(self, **kwargs: Any) -> str:  # noqa: ANN401
        return await self._writer.write(**kwargs)


class _SpyLlmProvider:
    """보낸 `LLMRequest`를 그대로 보관한다 — 프롬프트·파라미터 결정론 검증용."""

    def __init__(self, text: str = "정답률은 62%였습니다.") -> None:
        self.requests: list[LLMRequest] = []
        self._text = text

    @property
    def name(self) -> str:
        return "spy-counsel"

    async def complete(
        self, request: LLMRequest, context: ExecutionContext
    ) -> LLMResult:
        del context
        self.requests.append(request)
        return LLMResult(
            outcome=CallOutcome.OK,
            text=self._text,
            provider=self.name,
            model="spy",
            usage=TokenUsage(tokens_in=7, tokens_out=11, cost_usd=0.0),
            latency_ms=3,
        )


def _counsel_harness(
    refs: Sequence[str], *, provider: Any, run_store: InMemoryRunStore  # noqa: ANN401
) -> tuple[Supervisor, CounselPackRunner, InMemoryContextStore, InMemoryAgentStepSink]:
    supervisor = Supervisor(
        store=InMemoryJobStore(),
        lease_duration=timedelta(minutes=5),
        priority_aging_interval=timedelta(minutes=1),
        clock=lambda: _NOW,
    )
    contexts = InMemoryContextStore()
    sink = InMemoryAgentStepSink()
    runner = CounselPackRunner(
        supervisor=supervisor,
        context_store=contexts,
        draft_store=InMemoryDraftResultStore(),
        pack_store=InMemoryPackResultStore(),
        step_sink=sink,
        planner=provider,
        writer=provider,
        checkpointer=InMemorySaver(),
        regen_max=DEFAULT_REGEN_MAX,
        lease_owner="worker-obs",
        new_id=_counter(),
        now=lambda: _NOW,
        run_store=run_store,
    )
    del refs
    return supervisor, runner, contexts, sink


def _run_counsel_pack(
    refs: Sequence[str], *, provider: Any, run_store: InMemoryRunStore  # noqa: ANN401
) -> tuple[InMemoryAgentStepSink, UUID]:
    """enqueue → run_next 1회. (step_sink, job_id)를 돌려준다."""
    supervisor, runner, contexts, sink = _counsel_harness(
        refs, provider=provider, run_store=run_store
    )
    enqueuer = CounselPackEnqueuer(
        supervisor=supervisor, context_store=contexts, new_id=_counter(), now=lambda: _NOW
    )

    async def scenario() -> UUID:
        await enqueuer.enqueue(
            tenant_id="t1", class_ref="cl_a1", contexts={r: _context(r) for r in refs}
        )
        done = await runner.run_next(tenant_id="t1")
        assert done is not None
        return done.job_id

    return sink, _run(scenario())


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    """공용 수집기·저장소 격리 — 싱글턴 수집기라 버킷이 테스트 간에 새지 않게."""
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


# ── ① recorder 종단 — 경로별 role ─────────────────────────────────


def test_briefing_path_records_narrator_call() -> None:
    """🔴 브리핑 경로 — role=narrator로 LLM_CALL이 남는다(종전 전량 폐기 · ㊻ⓐ).

    브리핑 호출은 **감지 AI_RUN에 매달린다**(별 AI_RUN을 만들지 않는다) — 같은 실행이므로
    `run_id == execution_id`이고, 그래서 저장소는 `record_calls`(AI_RUN 없이)를 쓴다.
    """
    runs = InMemoryRunStore()
    ledger = InMemoryDetectionStore()
    set_detect_run_store(runs)
    set_detection_store(ledger)

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/detect", json=to_payload(fixture_composite_risk()), headers=_DETECT_HEADERS
        )
    assert response.status_code == 200, response.text
    execution_id = UUID(response.json()["meta"]["execution_id"])

    assert runs.calls, "브리핑 LLM 호출이 어디에도 적재되지 않았다(recorder 미배선)"
    assert {call.role for call in runs.calls} == {"narrator"}
    assert {call.run_id for call in runs.calls} == {execution_id}


def test_classify_path_records_classifier_call() -> None:
    """🔴 분류 경로 — role=classifier다. GENERATOR로 남으면 B-4 정정이 되돌아간 것이다."""
    runs = InMemoryRunStore()
    set_classify_run_store(runs)
    set_inquiry_class_store(InMemoryInquiryClassStore())

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/classify", json=_CLASSIFY_BODY, headers=_CLASSIFY_HEADERS
        )
    assert response.status_code == 200, response.text

    assert runs.calls, "분류 LLM 호출이 적재되지 않았다"
    assert {call.role for call in runs.calls} == {"classifier"}
    assert {call.prompt_id for call in runs.calls} == {"classify.inquiry.v1"}


def test_counsel_path_records_counselor_call() -> None:
    """🔴 상담 초안 경로 — role=counselor. 게이트웨이 writer를 꽂아야 호출이 실제로 난다."""
    runs = InMemoryRunStore()
    _sink, _job_id = _run_counsel_pack(
        ["st_1"], provider=_GatewayCounselProvider(), run_store=runs
    )

    assert runs.calls, "상담 초안 LLM 호출이 적재되지 않았다"
    assert {call.role for call in runs.calls} == {"counselor"}
    # plan(부가정보)과 write(초안)가 같은 role·같은 실행 아래 남는다.
    assert len(runs.calls) >= 2


def test_counsel_run_is_recorded_even_with_zero_llm_calls() -> None:
    """AI_RUN은 호출 0건이어도 남는다 — 불변식 8은 "모든 실행"을 기록한다.

    CI 기본 `FakeCounselProvider`는 게이트웨이를 타지 않아 호출이 **실제로** 0건이다.
    그래도 실행 자체는 재현 대상이므로 AI_RUN 행은 있어야 한다.
    """
    runs = InMemoryRunStore()
    _run_counsel_pack(
        ["st_1"],
        provider=FakeCounselProvider(drafts=["정답률은 62%였습니다."]),
        run_store=runs,
    )

    assert len(runs.runs) == 1
    assert runs.calls == []
    run = next(iter(runs.runs.values()))
    assert run.prompt_version is not None, "AI_RUN.prompt_version이 null이다(재현 불가)"


# ── ② FK 정합 — LLM_CALL.run_id가 실존 AI_RUN을 가리킨다 ───────────


def test_llm_call_run_id_resolves_to_existing_ai_run() -> None:
    """🔴 `llm_call.run_id`는 `ai_run.execution_id`를 **NOT NULL FK**로 참조한다.

    AI_RUN 없이 LLM_CALL을 넣으면 PG에서 FK 위반으로 죽는다. 인메모리 저장소는 FK를
    강제하지 않으므로 **여기서 순서를 고정**한다 — 이 단정이 깨지면 PG 백엔드에서 500이다.
    """
    runs = InMemoryRunStore()
    _run_counsel_pack(
        ["st_1", "st_2"], provider=_GatewayCounselProvider(), run_store=runs
    )

    assert runs.calls
    for call in runs.calls:
        assert call.run_id in runs.runs, (
            f"LLM_CALL이 존재하지 않는 AI_RUN({call.run_id})을 가리킨다 — FK 위반"
        )


def test_record_calls_does_not_invent_an_ai_run() -> None:
    """호출만 적재하는 경로(`record_calls`)는 AI_RUN을 만들지 않는다.

    감지 경로는 AI_RUN을 감지 원장이 쓴다 — 여기서 또 만들면 같은 PK를 두 번 넣는다.
    """
    runs = InMemoryRunStore()
    execution_id = uuid4()
    collector = default_llm_call_collector()
    collector(_a_record(), _ctx(execution_id))

    _run(runs.record_calls(execution_id=execution_id, calls=collector.take(execution_id)))

    assert runs.runs == {}
    assert [call.run_id for call in runs.calls] == [execution_id]


def _ctx(execution_id: UUID) -> ExecutionContext:
    return ExecutionContext(
        execution_id=execution_id,
        tenant_id="t1",
        capability=Capability.COMPOSITION,
        input_snapshot_hash=_HASH,
        versions=VersionSet(
            pipeline_version="0.1",
            engine_version="e",
            schema_version="0.1",
            contract_version="0.1",
        ),
    )


def _a_record(outcome: CallOutcome = CallOutcome.OK) -> Any:  # noqa: ANN401
    from ai.contracts.llm import ModelRole
    from ai.llm.gateway import LlmCallRecord

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


# ── ③ 적재 실패 내성 — 관측이 호출을 죽이지 않는다 ────────────────


def test_recorder_failure_does_not_fail_the_llm_call() -> None:
    """🔴 recorder가 터져도 LLM 호출은 성공한다(09 §1-10 ② · 게이트웨이 `_record`).

    관측은 게이트가 아니다. 다만 **조용히 삼키지도 않는다** — 실패 카운터가 오른다.
    프로덕션 조립부에 recorder를 주입해 검증한다(게이트웨이 직접 생성 금지).
    """

    def _boom(record: Any, context: Any) -> None:  # noqa: ANN401
        del record, context
        raise RuntimeError("적재 실패 시나리오")

    provider = _SpyLlmProvider()
    gateway = build_counsel_gateway(provider, recorder=_boom)
    writer = GatewayDraftWriter(gateway)

    text = _run(
        writer.write(context=_context("st_1"), execution_context=_execution_context())
    )

    assert text == "정답률은 62%였습니다."  # 호출은 성공
    assert gateway.record_failures == 1  # 실패는 카운터로 남는다


def test_collector_never_raises_on_bucket_overflow() -> None:
    """수집 상한 초과도 예외가 아니다 — 카운터+경고로 남긴다(불변식 6 · 조용한 누락 금지)."""
    from ai.db.repositories.run_store import LlmCallCollector

    collector = LlmCallCollector(max_calls_per_run=2)
    context = _ctx(uuid4())
    for _ in range(5):
        collector(_a_record(), context)

    assert len(collector.peek(context.execution_id)) == 2
    assert collector.dropped_calls == 3


# ── ④ quota_consumed — 단위는 "생성 1건" ──────────────────────────


def _invoke_graph(
    contexts: Mapping[str, DraftContext],
    refs: Sequence[str],
    *,
    provider: Any,  # noqa: ANN401
) -> dict[str, Any]:
    """그래프를 직접 돌려 최종 state를 본다 — `quota_consumed`는 state에만 있다."""
    graph = build_counsel_graph(
        planner=provider,
        writer=provider,
        contexts=contexts,
        execution_context=_execution_context(),
        checkpointer=InMemorySaver(),
        regen_max=DEFAULT_REGEN_MAX,
        llm_failure_circuit=99,
        draft_store=InMemoryDraftResultStore(),
        tenant_id="t1",
        agent_run_id=UUID(int=1),
        new_draft_id=_counter(),
        now=lambda: _NOW,
    )
    state = CounselPackState(
        tenant_id="t1",
        class_ref="cl_a1",
        student_refs=list(refs),
        context_ref=f"context://{UUID(int=9)}",
        context_hash=_HASH,
        plan_version="0.1",
    )
    result: dict[str, Any] = _run(
        graph.ainvoke(state, config={"configurable": {"thread_id": "obs-1"}})
    )
    return result


def test_quota_counts_one_per_generated_student() -> None:
    """생성 성공 학생 2명 → 2. 종전에는 증가 지점이 `src/`에 0개라 항상 0이었다(㊻ⓑ)."""
    refs = ["st_1", "st_2"]
    final = _invoke_graph(
        {r: _context(r) for r in refs},
        refs,
        provider=FakeCounselProvider(drafts=["정답률은 62%였습니다."]),
    )
    assert final["quota_consumed"] == 2


def test_quota_counts_one_even_when_gate_retries_three_times() -> None:
    """🔴 **단위는 호출 수가 아니라 생성 1건**이다 — 재생성 3회여도 1이다.

    호출 수는 LLM_CALL 행수로 이미 정확히 남는다. 여기서 또 세면 두 지표가 갈려
    어느 쪽이 원장인지 알 수 없게 된다.
    """
    refs = ["st_1"]
    # 근거에 없는 수치(88%) → SourceGrounding 실패 → regen_max까지 재시도 후 소진.
    provider = FakeCounselProvider(drafts=["정답률이 88%까지 올랐습니다."])
    final = _invoke_graph({r: _context(r) for r in refs}, refs, provider=provider)

    # 재생성 3회 = 시도 4회(`range(regen_max + 1)`) — 호출 수가 몇이든 quota는 1이다.
    assert len(provider.write_calls) == DEFAULT_REGEN_MAX + 1
    assert final["quota_consumed"] == 1  # 그래도 1


def test_quota_is_zero_when_nothing_was_sent() -> None:
    """컨텍스트 부재 = LLM 0회 = 원가 0. 없던 원가를 세면 미터링이 거짓말한다."""
    refs = ["st_missing"]
    final = _invoke_graph({}, refs, provider=FakeCounselProvider())
    assert final["quota_consumed"] == 0


# ── ⑤ llm_call_id 간선 — execution_id → 호출 도달 ─────────────────


def test_agent_step_links_to_the_llm_call_that_made_the_draft() -> None:
    """🔴 종전 상수 `None`(㊻ⓒ) — `execution_id`에서 LLM 호출로 갈 간선이 끊겨 있었다.

    값은 그 학생의 **마지막 성공 호출**이다: 산출물을 만든 호출을 가리켜야 한다.
    """
    runs = InMemoryRunStore()
    sink, job_id = _run_counsel_pack(
        ["st_1"], provider=_GatewayCounselProvider(), run_store=runs
    )

    steps = _run(sink.steps(job_id))
    assert len(steps) == 1
    linked = steps[0].llm_call_id
    assert linked is not None, "llm_call_id가 여전히 None이다 — 간선이 끊겼다"

    matched = [call for call in runs.calls if call.id == linked]
    assert len(matched) == 1, "llm_call_id가 실존하지 않는 LLM_CALL을 가리킨다"
    assert matched[0].outcome == CallOutcome.OK.value  # 성공 호출을 가리킨다
    assert matched[0].role == "counselor"  # plan이 아니라 초안 호출이다


def test_inquiry_class_links_to_the_classifying_call() -> None:
    """🔴 `inquiry_class.llm_call_id`는 실 FK다 — LLM_CALL 적재가 **먼저** 서야 한다(ⓕ)."""
    runs = InMemoryRunStore()
    store = InMemoryInquiryClassStore()
    set_classify_run_store(runs)
    set_inquiry_class_store(store)

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/classify", json=_CLASSIFY_BODY, headers=_CLASSIFY_HEADERS
        )
    assert response.status_code == 200, response.text

    record = _run(store.get(tenant_id="t1", inquiry_ref="iq_obs_1"))
    assert record is not None
    assert record.llm_call_id is not None, "llm_call_id가 NULL이다 — 간선이 끊겼다"
    matched = [call for call in runs.calls if call.id == record.llm_call_id]
    assert len(matched) == 1
    assert matched[0].outcome == CallOutcome.OK.value


def test_last_success_id_picks_the_final_successful_call() -> None:
    """재시도가 섞이면 **마지막 성공**을 고른다 — 버려진 시도를 가리키지 않는다."""
    from ai.db.repositories.run_store import last_success_id

    calls = (
        CollectedCall(UUID(int=1), _a_record(CallOutcome.PARSE_FAIL)),
        CollectedCall(UUID(int=2), _a_record(CallOutcome.OK)),
        CollectedCall(UUID(int=3), _a_record(CallOutcome.OK)),
        CollectedCall(UUID(int=4), _a_record(CallOutcome.TIMEOUT)),
    )
    assert last_success_id(calls) == UUID(int=3)
    assert last_success_id(()) is None
    assert last_success_id((CollectedCall(UUID(int=5), _a_record(CallOutcome.BAD_REF)),)) is None


# ── ⑥ seed — 요청이 재현 축을 싣는다 ─────────────────────────────


def test_all_paths_share_one_seed() -> None:
    """🔴 경로마다 상수를 따로 두면 재현 조건이 갈린다 — 정본은 `determinism.py` 하나다.

    종전에는 분류만 seed가 있고 브리핑·초안은 없었다(㊼) — 브리핑은 temperature조차
    미지정이라 어댑터 기본 0.7로 나갔다.
    """
    for params in (BRIEF_GEN_PARAMS, COUNSEL_GEN_PARAMS, CLASSIFY_GEN_PARAMS):
        assert params.seed == LLM_SEED
        assert params.temperature == 0.0
    assert deterministic_params().seed == LLM_SEED


def test_identical_input_produces_a_byte_identical_request() -> None:
    """같은 입력 → 같은 요청(프롬프트 + 생성 파라미터). 결정론은 여기까지가 AI 책임이다.

    ⚠ **같은 출력까지는 보증하지 않는다** — 서버가 seed를 존중하는지는 실측 전이다
    (99 ㊼ 부분 해소). 이 단정은 "요청이 흔들리지 않는다"만 고정한다.
    """
    provider = _SpyLlmProvider()
    writer = GatewayDraftWriter(build_counsel_gateway(provider))
    context = _context("st_1")

    for _ in range(2):
        _run(writer.write(context=context, execution_context=_execution_context()))

    first, second = provider.requests
    assert first.prompt == second.prompt
    assert first.generation_params == second.generation_params
    assert first.generation_params is not None
    assert first.generation_params.seed == LLM_SEED


def test_ai_run_records_the_seed() -> None:
    """seed가 AI_RUN.generation_params에 남는다 — 과거 실행의 재현 키를 원장에서 읽는다."""
    runs = InMemoryRunStore()
    _run_counsel_pack(
        ["st_1"], provider=_GatewayCounselProvider(), run_store=runs
    )

    run = next(iter(runs.runs.values()))
    assert run.generation_params is not None
    assert run.generation_params.seed == LLM_SEED


# ── ⑦ 관측 배선이 프롬프트 문면을 바꾸지 않는다(골든 보호) ────────


def test_observability_wiring_leaves_the_prompt_untouched() -> None:
    """🔴 recorder·seed 배선은 **프롬프트 바이트를 바꾸지 않는다** — 골든셋의 전제다.

    `generation_params`는 요청 필드이고 프롬프트 문면이 아니다. 여기가 깨지면 tone
    골든셋이 통째로 흔들리므로, 골든 파일을 고치기 전에 이 단정을 먼저 본다.
    """
    provider = _SpyLlmProvider()
    writer = GatewayDraftWriter(build_counsel_gateway(provider))
    context = _context("st_1")

    _run(writer.write(context=context, execution_context=_execution_context()))

    assert provider.requests[0].prompt == assemble_prompt(context, (), "", "")
