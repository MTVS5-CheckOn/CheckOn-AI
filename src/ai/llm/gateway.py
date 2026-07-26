"""역할 기반 LLM provider 라우팅·전송 재시도·호출 기록."""

import time
from collections.abc import Callable, Mapping

from pydantic import BaseModel, ConfigDict, Field
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_none

from ai.contracts.execution import ExecutionContext
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


class LlmCallRecord(BaseModel):
    """LLM 호출 1회분의 비민감 관측 메타데이터."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: ModelRole
    prompt_id: str
    prompt_version: str
    provider: str
    model: str | None
    usage: TokenUsage | None
    latency_ms: int = Field(ge=0)
    outcome: CallOutcome


type LlmCallRecorder = Callable[[LlmCallRecord], None]


def _ignore_record(record: LlmCallRecord) -> None:
    del record


def _exception_outcome(error: Exception) -> CallOutcome:
    if isinstance(error, FieldMissing):
        return CallOutcome.FIELD_MISSING
    if isinstance(error, ParseFailed):
        return CallOutcome.PARSE_FAIL
    if isinstance(error, LlmTimeout):
        return CallOutcome.TIMEOUT
    if isinstance(error, LlmUnavailable):
        return CallOutcome.PROVIDER_ERROR
    if isinstance(error, RedactionBlocked):
        return CallOutcome.REDACTION_BLOCKED
    if isinstance(error, LlmError):
        return CallOutcome.PROVIDER_ERROR
    return CallOutcome.PROVIDER_ERROR


class LlmGateway:
    """LLMRequest.role에 맞는 provider를 선택해 호출한다."""

    def __init__(
        self,
        providers: Mapping[ModelRole, LLMProvider],
        *,
        recorder: LlmCallRecorder = _ignore_record,
    ) -> None:
        self._providers = dict(providers)
        self._recorder = recorder
        self._validate_provider_assignment()

    def _validate_provider_assignment(self) -> None:
        distinct_provider_names = {provider.name for provider in self._providers.values()}
        if len(distinct_provider_names) < 2:
            # TODO(B-5): verifier 폴백 패밀리 확보 시 강제 활성.
            return

        generator = self._providers.get(ModelRole.GENERATOR)
        verifier = self._providers.get(ModelRole.VERIFIER)
        if (
            generator is not None
            and verifier is not None
            and generator.name == verifier.name
        ):
            raise ValueError(
                "provider가 2개 이상이면 generator와 verifier를 같은 provider에 배정할 수 없다."
            )

    async def complete(
        self,
        request: LLMRequest,
        context: ExecutionContext,
    ) -> LLMResult:
        """일시적 전송 오류만 1회 재시도하고 모든 시도를 기록한다."""

        provider = self._providers.get(request.role)
        if provider is None:
            raise LookupError(f"role={request.role.value}에 등록된 LLM provider가 없다.")

        retrying = AsyncRetrying(
            retry=retry_if_exception_type((LlmTimeout, LlmUnavailable)),
            stop=stop_after_attempt(2),
            wait=wait_none(),
            reraise=True,
        )
        async for attempt in retrying:
            with attempt:
                return await self._complete_once(provider, request, context)
        raise RuntimeError("LLM 전송 재시도 흐름이 결과 없이 종료됐다.")

    async def _complete_once(
        self,
        provider: LLMProvider,
        request: LLMRequest,
        context: ExecutionContext,
    ) -> LLMResult:
        started_at = time.monotonic()
        try:
            result = await provider.complete(request, context)
        except Exception as error:
            self._recorder(
                LlmCallRecord(
                    role=request.role,
                    prompt_id=request.prompt_id,
                    prompt_version=request.prompt_version,
                    provider=provider.name,
                    model=None,
                    usage=None,
                    latency_ms=int((time.monotonic() - started_at) * 1000),
                    outcome=_exception_outcome(error),
                )
            )
            raise

        self._recorder(
            LlmCallRecord(
                role=request.role,
                prompt_id=request.prompt_id,
                prompt_version=request.prompt_version,
                provider=result.provider,
                model=result.model,
                usage=result.usage,
                latency_ms=result.latency_ms,
                outcome=result.outcome,
            )
        )
        return result
