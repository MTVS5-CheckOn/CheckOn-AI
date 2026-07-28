"""역할 기반 LLM provider 라우팅·전송 재시도·호출 기록."""

import logging
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

logger = logging.getLogger(__name__)

#: 미지정 role의 전송 재시도 — 총 2회(현행 stop_after_attempt(2) 보존). 09 §1-10 ① 기본값.
_DEFAULT_TRANSPORT_RETRY = 1


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


type LlmCallRecorder = Callable[[LlmCallRecord, ExecutionContext], None]


def _ignore_record(record: LlmCallRecord, context: ExecutionContext) -> None:
    del record, context


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
        transport_retry: Mapping[ModelRole, int] | None = None,
    ) -> None:
        self._providers = dict(providers)
        self._recorder = recorder
        #: role별 전송 재시도(생성자 주입 — 호출별 금지). 미지정 role은 기본 1회.
        self._transport_retry = self._validated_transport_retry(transport_retry)
        #: recorder 적재 실패 누적 — 조용한 누락 방지(09 §1-10 ②). 호출은 성공 유지.
        self.record_failures = 0
        self._validate_provider_assignment()

    @staticmethod
    def _validated_transport_retry(
        transport_retry: Mapping[ModelRole, int] | None,
    ) -> dict[ModelRole, int]:
        """0..1 범위 밖이면 기동 실패(불변식 6 — 모든 루프에 상한). 09 §1-10 ① 값 범위."""
        resolved = dict(transport_retry or {})
        for role, retries in resolved.items():
            if not 0 <= retries <= 1:
                raise ValueError(
                    f"transport_retry[{role.value}]={retries}는 0..1을 벗어난다"
                    " — 재시도 상한 위반(불변식 6)."
                )
        return resolved

    def _attempts_for(self, role: ModelRole) -> int:
        """총 시도 횟수 = 재시도 + 1. 미지정 role은 현행 보존(재시도 1 → 2회)."""
        return self._transport_retry.get(role, _DEFAULT_TRANSPORT_RETRY) + 1

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
            stop=stop_after_attempt(self._attempts_for(request.role)),
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
            self._record(
                LlmCallRecord(
                    role=request.role,
                    prompt_id=request.prompt_id,
                    prompt_version=request.prompt_version,
                    provider=provider.name,
                    model=None,
                    usage=None,
                    latency_ms=int((time.monotonic() - started_at) * 1000),
                    outcome=_exception_outcome(error),
                ),
                context,
            )
            raise

        self._record(
            LlmCallRecord(
                role=request.role,
                prompt_id=request.prompt_id,
                prompt_version=request.prompt_version,
                provider=result.provider,
                model=result.model,
                usage=result.usage,
                latency_ms=result.latency_ms,
                outcome=result.outcome,
            ),
            context,
        )
        return result

    def _record(self, record: LlmCallRecord, context: ExecutionContext) -> None:
        """관측 기록 — 적재 실패가 LLM 호출을 실패시키지 않는다(09 §1-10 ②, 01 §5).

        기록은 관측이지 게이트가 아니다. 실패는 경고+실패 카운터로 남기고(조용한 누락 금지)
        호출은 성공시킨다. 재시도 0회여도 이 경로로 시도별 기록이 남는다(회계 분리).
        """
        try:
            self._recorder(record, context)
        except Exception:  # noqa: BLE001 — 관측 실패를 삼키되 카운터·경고로 남긴다
            self.record_failures += 1
            logger.warning(
                "LlmCallRecord 적재 실패 — 호출은 성공 처리(관측이지 게이트 아님).",
                exc_info=True,
            )
