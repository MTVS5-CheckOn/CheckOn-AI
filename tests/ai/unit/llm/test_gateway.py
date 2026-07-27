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
from ai.llm.gateway import LlmCallRecord, LlmGateway


def _run[ResultT](coroutine: Coroutine[object, object, ResultT]) -> ResultT:
    return asyncio.run(coroutine)


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


def test_timeout_once_then_success_retries_once_and_records_both_attempts() -> None:
    records: list[LlmCallRecord] = []
    provider = FakeProvider([LlmTimeout("일시 장애"), _result()])
    gateway = LlmGateway({ModelRole.GENERATOR: provider}, recorder=records.append)

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
    gateway = LlmGateway({ModelRole.GENERATOR: provider}, recorder=records.append)

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
    gateway = LlmGateway({ModelRole.GENERATOR: provider}, recorder=records.append)

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
        recorder=records.append,
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
    gateway = LlmGateway({ModelRole.GENERATOR: provider}, recorder=records.append)

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
    shared = FakeProvider([], name="gemma")
    fallback = FakeProvider([], name="fallback")

    with pytest.raises(ValueError, match="같은 provider"):
        LlmGateway(
            {
                ModelRole.GENERATOR: shared,
                ModelRole.VERIFIER: shared,
                ModelRole.MAPPER: fallback,
            }
        )
