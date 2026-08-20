"""LLM 게이트웨이의 역할 라우팅·전송 재시도·관측 기록 검사."""

import asyncio
from collections.abc import Coroutine
from uuid import UUID

import pytest
from fake_provider import FakeProvider

from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import (
    CallOutcome,
    LLMRequest,
    LLMResult,
    LlmTimeout,
    LlmUnavailable,
    ModelRole,
    ParseFailed,
    TokenUsage,
)
from ai.llm.call_timeouts import CallTimeoutTable
from ai.llm.gateway import LlmCallRecord, LlmCallRecorder, LlmGateway
from ai.runtime.tracing import TRACING_ENV_SYNONYMS

_TRACE_MASKED_PROMPT = "[트레이스 마스킹 프롬프트]"
_REPRESENTATIVE_TRACING_ENV = TRACING_ENV_SYNONYMS[0]


def _run[ResultT](coroutine: Coroutine[object, object, ResultT]) -> ResultT:
    return asyncio.run(coroutine)


def _collect(records: list[LlmCallRecord]) -> LlmCallRecorder:
    """리스트에 기록을 모으는 recorder(2인자 시그니처 — 09 §1-10 ②). context는 미사용."""

    def _record(record: LlmCallRecord, _context: ExecutionContext) -> None:
        records.append(record)

    return _record


def _context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=UUID("00000000-0000-4000-8000-000000000102"),
        tenant_id="teacher_alias_001",
        capability=Capability.COMPOSITION,
        input_snapshot_hash="sha256:gateway",
        versions=VersionSet(
            pipeline_version="v2.1",
            engine_version="rules-1.0",
            schema_version="0.1",
            contract_version="0.1",
            prompt_version="v0.1",
        ),
    )


def _request(role: ModelRole = ModelRole.GENERATOR) -> LLMRequest:
    return LLMRequest(
        role=role,
        prompt="[마스킹 통과 프롬프트]",
        prompt_id="composition/reply",
        prompt_version="v0.1",
    )


def _result(outcome: CallOutcome = CallOutcome.OK) -> LLMResult:
    return LLMResult(
        outcome=outcome,
        text="정상 응답" if outcome is CallOutcome.OK else None,
        provider="fake",
        model="fake-model",
        usage=TokenUsage(tokens_in=11, tokens_out=7, cost_usd=0.0),
        latency_ms=4,
    )


class _ReplacingTraceMaskingHook:
    def __init__(self, prompt: str = _TRACE_MASKED_PROMPT) -> None:
        self._prompt = prompt
        self.calls = 0

    def mask(
        self,
        request: LLMRequest,
        context: ExecutionContext,
    ) -> LLMRequest:
        del context
        self.calls += 1
        return request.model_copy(update={"prompt": self._prompt})


class _FailingTraceMaskingHook:
    def mask(
        self,
        request: LLMRequest,
        context: ExecutionContext,
    ) -> LLMRequest:
        del request, context
        raise RuntimeError("트레이스 마스킹 실패")


class _IdentityMutatingTraceMaskingHook:
    def __init__(self, field: str, value: object) -> None:
        self._field = field
        self._value = value

    def mask(
        self,
        request: LLMRequest,
        context: ExecutionContext,
    ) -> LLMRequest:
        del context
        return request.model_copy(update={self._field: self._value})


def test_timeout_once_then_success_retries_once_and_records_both_attempts() -> None:
    records: list[LlmCallRecord] = []
    provider = FakeProvider([LlmTimeout("일시 장애"), _result()])
    gateway = LlmGateway({ModelRole.GENERATOR: provider}, recorder=_collect(records))

    result = _run(gateway.complete(_request(), _context()))

    assert result.outcome is CallOutcome.OK
    assert len(provider.requests) == 2
    assert [record.outcome for record in records] == [
        CallOutcome.TIMEOUT,
        CallOutcome.OK,
    ]


def test_two_timeouts_propagate_without_third_attempt() -> None:
    records: list[LlmCallRecord] = []
    provider = FakeProvider([LlmTimeout("첫 실패"), LlmTimeout("둘째 실패")])
    gateway = LlmGateway({ModelRole.GENERATOR: provider}, recorder=_collect(records))

    with pytest.raises(LlmTimeout, match="둘째 실패"):
        _run(gateway.complete(_request(), _context()))

    assert len(provider.requests) == 2
    assert [record.outcome for record in records] == [
        CallOutcome.TIMEOUT,
        CallOutcome.TIMEOUT,
    ]


def test_unavailable_is_retried_once() -> None:
    provider = FakeProvider([LlmUnavailable("연결 실패"), "복구 응답"])
    gateway = LlmGateway({ModelRole.GENERATOR: provider})

    result = _run(gateway.complete(_request(), _context()))

    assert result.text == "복구 응답"
    assert len(provider.requests) == 2


def test_parse_failed_is_propagated_without_retry() -> None:
    records: list[LlmCallRecord] = []
    provider = FakeProvider([ParseFailed("JSON 오류"), "호출되면 안 됨"])
    gateway = LlmGateway({ModelRole.GENERATOR: provider}, recorder=_collect(records))

    with pytest.raises(ParseFailed, match="JSON 오류"):
        _run(gateway.complete(_request(), _context()))

    assert len(provider.requests) == 1
    assert [record.outcome for record in records] == [CallOutcome.PARSE_FAIL]


def test_unregistered_role_raises_explicit_error() -> None:
    gateway = LlmGateway({ModelRole.GENERATOR: FakeProvider(["정상 응답"])})

    with pytest.raises(LookupError, match="verifier"):
        _run(gateway.complete(_request(ModelRole.VERIFIER), _context()))


def test_recorder_receives_non_sensitive_result_metadata() -> None:
    records: list[LlmCallRecord] = []
    gateway = LlmGateway(
        {ModelRole.GENERATOR: FakeProvider([_result()])},
        recorder=_collect(records),
    )

    _run(gateway.complete(_request(), _context()))

    assert records == [
        LlmCallRecord(
            role=ModelRole.GENERATOR,
            prompt_id="composition/reply",
            prompt_version="v0.1",
            provider="fake",
            model="fake-model",
            usage=TokenUsage(tokens_in=11, tokens_out=7, cost_usd=0.0),
            latency_ms=4,
            outcome=CallOutcome.OK,
        )
    ]
    assert "prompt" not in LlmCallRecord.model_fields


def test_non_ok_result_outcome_is_recorded_without_retry() -> None:
    records: list[LlmCallRecord] = []
    provider = FakeProvider([_result(CallOutcome.BAD_REF)])
    gateway = LlmGateway({ModelRole.GENERATOR: provider}, recorder=_collect(records))

    result = _run(gateway.complete(_request(), _context()))

    assert result.outcome is CallOutcome.BAD_REF
    assert [record.outcome for record in records] == [CallOutcome.BAD_REF]
    assert len(provider.requests) == 1


def test_single_provider_can_serve_generator_and_verifier() -> None:
    shared = FakeProvider(["생성 응답", "검증 응답"])
    gateway = LlmGateway(
        {
            ModelRole.GENERATOR: shared,
            ModelRole.VERIFIER: shared,
        }
    )

    assert _run(gateway.complete(_request(ModelRole.GENERATOR), _context())).text == "생성 응답"
    assert _run(gateway.complete(_request(ModelRole.VERIFIER), _context())).text == "검증 응답"


def test_multiple_providers_reject_same_generator_and_verifier_assignment() -> None:
    shared = FakeProvider([], name="shared-provider")
    fallback = FakeProvider([], name="fallback")

    with pytest.raises(ValueError, match="같은 provider"):
        LlmGateway(
            {
                ModelRole.GENERATOR: shared,
                ModelRole.VERIFIER: shared,
                ModelRole.MAPPER: fallback,
            }
        )


# ── 전송 재시도 파라미터(transport_retry) — 09 §1-10 ① (a)~(d) ──────


@pytest.mark.parametrize("role", [ModelRole.GENERATOR, ModelRole.VERIFIER])
@pytest.mark.parametrize("bad", [2, -1])
def test_transport_retry_out_of_range_rejected_at_construction(role: ModelRole, bad: int) -> None:
    """값 0..1 밖이면 기동 실패(b) — generator·verifier에 정책 밖 값 주입도 거부(c)."""
    with pytest.raises(ValueError, match="0..1"):
        LlmGateway({role: FakeProvider([])}, transport_retry={role: bad})


def test_unspecified_role_defaults_to_one_retry() -> None:
    """미지정 role은 재시도 1(총 2회) — 현행 보존(문제생성 무변)."""
    provider = FakeProvider([LlmTimeout("1"), LlmTimeout("2")])
    gateway = LlmGateway({ModelRole.GENERATOR: provider}, transport_retry={})

    with pytest.raises(LlmTimeout, match="2"):
        _run(gateway.complete(_request(), _context()))
    assert len(provider.requests) == 2  # 재시도 1회 = 2 시도


def test_zero_retry_single_attempt_even_on_timeout() -> None:
    """재시도 0 등록 시 LlmTimeout에도 재시도 없이 1회 시도 후 전파(d 회계: 시도별 기록)."""
    records: list[LlmCallRecord] = []
    provider = FakeProvider([LlmTimeout("한 번뿐"), "호출되면 안 됨"])
    gateway = LlmGateway(
        {ModelRole.GENERATOR: provider},
        recorder=_collect(records),
        transport_retry={ModelRole.GENERATOR: 0},
    )

    with pytest.raises(LlmTimeout, match="한 번뿐"):
        _run(gateway.complete(_request(), _context()))
    assert len(provider.requests) == 1  # 재시도 없음
    assert [record.outcome for record in records] == [CallOutcome.TIMEOUT]  # 시도별 기록


# ── recorder 실패 격리 — 09 §1-10 ② (관측이지 게이트 아님) ──────────


def test_recorder_failure_does_not_fail_call_and_increments_counter() -> None:
    """적재 실패가 LLM 호출을 실패시키지 않는다 — 경고+실패 카운터, 호출은 성공."""

    def _boom(record: LlmCallRecord, context: ExecutionContext) -> None:
        del record, context
        raise RuntimeError("적재 장애")

    gateway = LlmGateway({ModelRole.GENERATOR: FakeProvider([_result()])}, recorder=_boom)

    result = _run(gateway.complete(_request(), _context()))

    assert result.outcome is CallOutcome.OK  # 호출은 성공
    assert gateway.record_failures == 1  # 조용한 누락 금지 — 카운터 증가


# ── 트레이스 마스킹 훅·기동 가드 — 09 §1-10 ③ · §2-16 ───────────


def _enable_external_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_REPRESENTATIVE_TRACING_ENV, "true")


@pytest.mark.parametrize(
    ("tracing_active", "inject_hook"),
    [(False, False), (False, True), (True, False), (True, True)],
)
def test_trace_masking_hook_tracing_combinations(
    tracing_active: bool,
    inject_hook: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if tracing_active:
        _enable_external_tracing(monkeypatch)

    provider = FakeProvider([_result()])
    hook = _ReplacingTraceMaskingHook() if inject_hook else None

    if tracing_active and not inject_hook:
        with pytest.raises(ValueError):
            LlmGateway({ModelRole.GENERATOR: provider})
        assert provider.requests == []
        return

    gateway = LlmGateway(
        {ModelRole.GENERATOR: provider},
        trace_masking_hook=hook,
    )

    result = _run(gateway.complete(_request(), _context()))

    assert result.outcome is CallOutcome.OK
    expected_prompt = _TRACE_MASKED_PROMPT if inject_hook else _request().prompt
    assert provider.requests[0].prompt == expected_prompt
    if hook is not None:
        assert hook.calls == 1


@pytest.mark.parametrize("env_name", TRACING_ENV_SYNONYMS)
def test_tracing_without_hook_fails_at_construction_with_actionable_message(
    env_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(env_name, "true")

    with pytest.raises(ValueError) as exc_info:
        LlmGateway({ModelRole.GENERATOR: FakeProvider([])})

    message = str(exc_info.value)
    assert env_name in message
    assert "§2-16" in message


def test_hook_returned_request_reaches_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_external_tracing(monkeypatch)
    provider = FakeProvider([_result()])
    gateway = LlmGateway(
        {ModelRole.GENERATOR: provider},
        trace_masking_hook=_ReplacingTraceMaskingHook(),
    )

    _run(gateway.complete(_request(), _context()))

    assert len(provider.requests) == 1
    assert provider.requests[0].prompt == _TRACE_MASKED_PROMPT
    assert provider.requests[0].prompt != _request().prompt


def test_retry_uses_hook_returned_request_for_every_transport_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_external_tracing(monkeypatch)
    provider = FakeProvider([LlmTimeout("첫 전송 실패"), _result()])
    hook = _ReplacingTraceMaskingHook()
    gateway = LlmGateway(
        {ModelRole.GENERATOR: provider},
        trace_masking_hook=hook,
    )

    result = _run(gateway.complete(_request(), _context()))

    assert result.outcome is CallOutcome.OK
    assert hook.calls == 1
    assert len(provider.requests) == 2
    assert all(request.prompt == _TRACE_MASKED_PROMPT for request in provider.requests)
    assert all(request.prompt != _request().prompt for request in provider.requests)


def test_hook_failure_does_not_call_provider_or_recorder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_external_tracing(monkeypatch)
    records: list[LlmCallRecord] = []
    provider = FakeProvider([_result()])
    gateway = LlmGateway(
        {ModelRole.GENERATOR: provider},
        recorder=_collect(records),
        trace_masking_hook=_FailingTraceMaskingHook(),
    )

    with pytest.raises(RuntimeError, match="트레이스 마스킹 실패"):
        _run(gateway.complete(_request(), _context()))

    assert provider.requests == []
    assert records == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("role", ModelRole.VERIFIER),
        ("prompt_id", "changed/prompt"),
        ("prompt_version", "v999"),
        ("response_schema_name", "ChangedSchema"),
    ],
)
def test_hook_cannot_change_request_identity_fields(
    field: str,
    value: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_external_tracing(monkeypatch)
    records: list[LlmCallRecord] = []
    provider = FakeProvider([_result()])
    gateway = LlmGateway(
        {ModelRole.GENERATOR: provider},
        recorder=_collect(records),
        trace_masking_hook=_IdentityMutatingTraceMaskingHook(field, value),
    )

    with pytest.raises(ValueError, match=field):
        _run(gateway.complete(_request(), _context()))

    assert provider.requests == []
    assert records == []


class _SlowProvider:
    """지정한 시간만큼 기다렸다가 답하는 대역 — 상한이 실제로 끊는지 재기 위한 것.

    ⚠ `_started`를 세는 이유: 「상한이 끑었다」와 「애초에 안 불렸다」는
    밖에서 둘 다 「결과가 없다」로 보인다. 가르지 않으면 라우팅 버그를 상한 덕으로 읽는다.
    """

    def __init__(self, *, delay_s: float, name: str = "fake") -> None:
        self._delay_s = delay_s
        self._name = name
        self.started = 0
        self.finished = 0

    @property
    def name(self) -> str:
        return self._name

    async def complete(
        self,
        request: LLMRequest,
        context: ExecutionContext,
    ) -> LLMResult:
        del request, context
        self.started += 1
        await asyncio.sleep(self._delay_s)
        self.finished += 1
        return _result()


def test_an_unregistered_prompt_id_is_not_capped_by_the_gateway() -> None:
    """정상 — 표에 없는 자리는 오늘과 같다(provider 전역 상한이 받는다).

    🔴 **이게 이 변경의 핵심 계약이다.** 정본 표가 비어 있으므로 지금 모든 호출이
    이 경로를 탄다 — 즐, 이 PR 은 운영 동작을 바꾸지 않는다.
    """

    provider = _SlowProvider(delay_s=0.01)
    gateway = LlmGateway(
        {ModelRole.GENERATOR: provider},
        call_timeouts=CallTimeoutTable(call_timeouts={"다른/자리": 0.001}),
    )

    result = _run(gateway.complete(_request(), _context()))

    assert result.outcome is CallOutcome.OK
    assert (provider.started, provider.finished) == (1, 1)


def test_a_registered_cap_shorter_than_the_call_raises_llm_timeout() -> None:
    """경계 — 상한이 호출보다 짧으면 게이트웨이가 끗는다.

    🔴 `TimeoutError` 가 아니라 **`LlmTimeout`** 이어야 한다 — provider 가 직접 끈을
    때와 같은 예외여야 소비쪽이 두 경우를 가르지 않고 같은 폴백을 한다.
    """

    provider = _SlowProvider(delay_s=5.0)
    gateway = LlmGateway(
        {ModelRole.GENERATOR: provider},
        transport_retry={ModelRole.GENERATOR: 0},
        call_timeouts=CallTimeoutTable(call_timeouts={"composition/reply": 0.01}),
    )

    with pytest.raises(LlmTimeout, match="composition/reply"):
        _run(gateway.complete(_request(), _context()))

    #: 🔴 「안 불렸다」가 아니라 「불렸는데 끑겼다」임을 센다.
    assert (provider.started, provider.finished) == (1, 0)


def test_a_capped_timeout_is_recorded_as_timeout_like_any_other() -> None:
    """실패 — 원장에 `TIMEOUT` 으로 적힐다.

    ⚠ 같은 사건이 두 가지 모양으로 원장에 남으면 **원가·장애 집계가 갈린다.**
    """

    records: list[LlmCallRecord] = []
    gateway = LlmGateway(
        {ModelRole.GENERATOR: _SlowProvider(delay_s=5.0)},
        recorder=_collect(records),
        transport_retry={ModelRole.GENERATOR: 0},
        call_timeouts=CallTimeoutTable(call_timeouts={"composition/reply": 0.01}),
    )

    with pytest.raises(LlmTimeout):
        _run(gateway.complete(_request(), _context()))

    assert [record.outcome for record in records] == [CallOutcome.TIMEOUT]
    assert records[0].prompt_id == "composition/reply"


def test_a_capped_timeout_still_follows_the_transport_retry_policy() -> None:
    """상한으로 끘은 것도 전송 재시도 정책을 그대로 따른다.

    🔴 재시도는 role 축이고 상한은 prompt_id 축이다 — **두 축이 섞이지 않음을**
    여기서 센다. 재시도 1회 ⇒ 총 2회 시도이므로 provider 가 두 번 불려야 한다.
    """

    provider = _SlowProvider(delay_s=5.0)
    gateway = LlmGateway(
        {ModelRole.GENERATOR: provider},
        transport_retry={ModelRole.GENERATOR: 1},
        call_timeouts=CallTimeoutTable(call_timeouts={"composition/reply": 0.01}),
    )

    with pytest.raises(LlmTimeout):
        _run(gateway.complete(_request(), _context()))

    assert provider.started == 2


def test_a_call_that_finishes_inside_the_cap_is_untouched() -> None:
    """정상 — 상한 안에서 끝나면 아무것도 안 바뀐다."""

    provider = _SlowProvider(delay_s=0.0)
    gateway = LlmGateway(
        {ModelRole.GENERATOR: provider},
        call_timeouts=CallTimeoutTable(call_timeouts={"composition/reply": 5.0}),
    )

    result = _run(gateway.complete(_request(), _context()))

    assert result.outcome is CallOutcome.OK
    assert (provider.started, provider.finished) == (1, 1)
