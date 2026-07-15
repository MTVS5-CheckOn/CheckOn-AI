"""contracts/llm.py 스모크 — enum 값 고정 · 왕복 직렬화 · Protocol 구현 가능성.

FakeProvider(B 소유, llm/providers/)가 이 Protocol을 구현할 수 있는 모양인지를
여기서 미리 확인한다 — 계약이 구현 불가능한 채로 머지되는 것을 막는다.
"""

import asyncio
from uuid import UUID

from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import (
    CallOutcome,
    FieldMissing,
    LlmError,
    LLMProvider,
    LLMRequest,
    LLMResult,
    LlmTimeout,
    LlmUnavailable,
    ModelRole,
    ParseFailed,
    RedactionBlocked,
    TokenUsage,
)


def _request() -> LLMRequest:
    return LLMRequest(
        role=ModelRole.GENERATOR,
        prompt="[마스킹 통과 프롬프트]",
        prompt_id="composition/reply",
        prompt_version="v0.1",
    )


def _result() -> LLMResult:
    return LLMResult(
        outcome=CallOutcome.OK,
        text="초안 문장",
        provider="fake",
        model="fake-1",
        usage=TokenUsage(tokens_in=10, tokens_out=20, cost_usd=0.0),
        latency_ms=5,
    )


class _StubProvider:
    """Protocol 구현 가능성 확인용 — 실제 FakeProvider는 B가 llm/providers/에 만든다."""

    @property
    def name(self) -> str:
        return "stub"

    async def complete(self, request: LLMRequest, context: ExecutionContext) -> LLMResult:
        return _result()


def test_model_role_values_frozen() -> None:
    """ERD LLM_CALL.role "generator|verifier|mapper|classifier(v2)"."""
    assert {role.value for role in ModelRole} == {
        "generator",
        "verifier",
        "mapper",
        "classifier",
    }


def test_call_outcome_values_frozen() -> None:
    """error_codes.md §3 — LLM_CALL.outcome."""
    assert {outcome.value for outcome in CallOutcome} == {
        "ok",
        "parse_fail",
        "field_missing",
        "bad_ref",
        "timeout",
        "provider_error",
        "redaction_blocked",
    }


def test_llm_request_roundtrip() -> None:
    request = _request()
    assert LLMRequest.model_validate(request.model_dump(mode="json")) == request


def test_llm_result_roundtrip() -> None:
    result = _result()
    assert LLMResult.model_validate(result.model_dump(mode="json")) == result


def test_failed_result_has_no_text() -> None:
    """OK가 아니면 text는 없을 수 있다 — 빈 문자열로 덮지 않는다."""
    result = LLMResult(
        outcome=CallOutcome.TIMEOUT,
        provider="fake",
        model="fake-1",
        usage=TokenUsage(tokens_in=10, tokens_out=0, cost_usd=0.0),
        latency_ms=10_000,
    )
    assert result.text is None
    assert LLMResult.model_validate(result.model_dump(mode="json")) == result


def test_stub_satisfies_provider_protocol() -> None:
    assert isinstance(_StubProvider(), LLMProvider)


def test_provider_complete_returns_result() -> None:
    context = ExecutionContext(
        execution_id=UUID("00000000-0000-4000-8000-000000000002"),
        tenant_id="teacher_alias_001",
        capability=Capability.COMPOSITION,
        input_snapshot_hash="sha256:abc",
        versions=VersionSet(
            pipeline_version="v2.1",
            engine_version="rules-1.0",
            schema_version="0.1",
            contract_version="0.1",
            prompt_version="v0.1",
        ),
    )
    # pytest-asyncio를 추가하지 않기 위해 표준 asyncio로 구동한다 (03_coding_rules.md §1b)
    result = asyncio.run(_StubProvider().complete(_request(), context))
    assert result.outcome is CallOutcome.OK


def test_exception_hierarchy() -> None:
    """runtime/errors.py가 이 계층을 받아 HTTP로 매핑한다 (error_codes.md §4)."""
    assert issubclass(LlmUnavailable, LlmError)
    assert issubclass(LlmTimeout, LlmError)
    assert issubclass(ParseFailed, LlmError)
    assert issubclass(RedactionBlocked, LlmError)
    # field_missing은 parse_fail과 같은 재시도 정책(≤3회)을 따른다 — §3
    assert issubclass(FieldMissing, ParseFailed)


def test_no_vendor_sdk_imported() -> None:
    """B-5 확정 전 벤더 SDK 설치·import 금지 (CLAUDE.md §3).

    모듈 소스에 벤더 이름이 등장하면 계약이 벤더에 오염된 것이다.
    """
    import inspect

    import ai.contracts.llm as llm_module

    source = inspect.getsource(llm_module).lower()
    for vendor in ("openai", "anthropic", "google.generativeai", "cohere", "mistralai"):
        assert vendor not in source, f"벤더 SDK 흔적: {vendor}"
