"""결정론 LLM provider 테스트 대역."""

from collections.abc import Sequence

from ai.contracts.execution import ExecutionContext
from ai.contracts.llm import (
    CallOutcome,
    LLMRequest,
    LLMResult,
    TokenUsage,
)

type FakeStep = str | Exception | LLMResult


class FakeProvider:
    """주입된 시나리오를 호출 순서대로 한 번씩 소비한다."""

    def __init__(
        self,
        scenario: Sequence[FakeStep],
        *,
        name: str = "fake",
    ) -> None:
        self._scenario = tuple(scenario)
        self._position = 0
        self._name = name
        self.requests: list[LLMRequest] = []

    @property
    def name(self) -> str:
        return self._name

    async def complete(
        self,
        request: LLMRequest,
        context: ExecutionContext,
    ) -> LLMResult:
        del context
        self.requests.append(request)
        if self._position >= len(self._scenario):
            raise RuntimeError("FakeProvider 시나리오가 소진됐다.")

        step = self._scenario[self._position]
        self._position += 1
        if isinstance(step, Exception):
            raise step
        if isinstance(step, LLMResult):
            return step
        return LLMResult(
            outcome=CallOutcome.OK,
            text=step,
            provider=self.name,
            model="fake-model",
            usage=TokenUsage(tokens_in=0, tokens_out=0, cost_usd=0.0),
            latency_ms=0,
        )
