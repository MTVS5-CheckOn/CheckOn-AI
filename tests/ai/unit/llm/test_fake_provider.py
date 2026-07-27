"""FakeProvider의 Protocol 정합성과 결정론 시나리오 소비 검사."""

import asyncio
from collections.abc import Coroutine
from uuid import UUID

import pytest
from fake_provider import FakeProvider

from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import (
    CallOutcome,
    LLMProvider,
    LLMRequest,
    LLMResult,
    LlmTimeout,
    ModelRole,
    TokenUsage,
)


def _run[ResultT](coroutine: Coroutine[object, object, ResultT]) -> ResultT:
    return asyncio.run(coroutine)


def _context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=UUID("00000000-0000-4000-8000-000000000101"),
        tenant_id="teacher_alias_001",
        capability=Capability.COMPOSITION,
        input_snapshot_hash="sha256:fake-provider",
        versions=VersionSet(
            pipeline_version="v2.1",
            engine_version="rules-1.0",
            schema_version="0.1",
            contract_version="0.1",
            prompt_version="v0.1",
        ),
    )


def _request(prompt_id: str = "composition/reply") -> LLMRequest:
    return LLMRequest(
        role=ModelRole.GENERATOR,
        prompt="[마스킹 통과 프롬프트]",
        prompt_id=prompt_id,
        prompt_version="v0.1",
    )


def _failed_result() -> LLMResult:
    return LLMResult(
        outcome=CallOutcome.BAD_REF,
        provider="fake",
        model="fake-model",
        usage=TokenUsage(tokens_in=3, tokens_out=1, cost_usd=0.0),
        latency_ms=2,
    )


def test_fake_provider_satisfies_runtime_protocol() -> None:
    assert isinstance(FakeProvider(["정상 응답"]), LLMProvider)


def test_scenario_is_consumed_in_order_and_requests_are_recorded() -> None:
    first_request = _request("first")
    second_request = _request("second")
    provider = FakeProvider(["정상 응답", _failed_result()])

    first_result = _run(provider.complete(first_request, _context()))
    second_result = _run(provider.complete(second_request, _context()))

    assert first_result.text == "정상 응답"
    assert second_result.outcome is CallOutcome.BAD_REF
    assert provider.requests == [first_request, second_request]


def test_scenario_exception_is_raised_at_its_position() -> None:
    provider = FakeProvider([LlmTimeout("일시 장애")])

    with pytest.raises(LlmTimeout, match="일시 장애"):
        _run(provider.complete(_request(), _context()))


def test_call_after_scenario_exhaustion_fails_explicitly() -> None:
    provider = FakeProvider(["정상 응답"])
    _run(provider.complete(_request(), _context()))

    with pytest.raises(RuntimeError, match="시나리오가 소진"):
        _run(provider.complete(_request(), _context()))
