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
