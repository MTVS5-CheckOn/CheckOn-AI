"""counsel_pack의 LLM 접점 — plan·generate_draft Protocol + 결정론 Fake.

`probe/`의 `tools.py`·`planner.py` 자리에 해당한다(counsel_pack은 ReAct가 아니라 순차
그래프라 도구 호출이 없다). 실제 LLM 호출은 **전부 `llm/gateway` 경유**이며(03 §2),
이 모듈은 Protocol과 CI 기본값인 Fake만 둔다.

**redaction 경계:** gateway로 나가는 프롬프트는 전송 직전 `runtime/redaction.redact`를
거치고, `uncertain`이면 **전송하지 않는다**(fail-closed · 불변식 3). 이 규율은
`tests/ai/contract/test_composition_redaction.py`가 AST로 고정한다.

**불변식 1:** plan 노드는 확정 수치 안에서 강조점만 고른다 — 새 사실·새 수치를 만들지
않는다. 산출 강조점에는 근거 `record_id`가 동반돼야 한다(state 불변식 ①이 재검증).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Final, Protocol, runtime_checkable

from ai.composition.counsel.prompt import (
    PROMPT_ID,
    PROMPT_VERSION,
    assemble_prompt,
    tone_rule_for,
)
from ai.contracts.composition import DraftContext
from ai.contracts.execution import ExecutionContext, GenerationParams
from ai.contracts.llm import (
    CallOutcome,
    LlmError,
    LLMRequest,
    LLMResult,
    ModelRole,
    TokenUsage,
)
from ai.llm.gateway import LlmGateway
from ai.runtime.redaction import redact

#: 초안 블록은 문단 단위라 브리핑(한 줄)보다 길다. 문장당 상한 × 문장 수로 산출한다 —
#: 값을 게이트에 박지 않고 tone_map의 sentences_per_block에서 파생시킨다(03 §1).
CHARS_PER_SENTENCE = 120


def max_chars_for(context: DraftContext) -> int:
    """이 조합의 블록 길이 상한 — tone_map의 문장 수에서 파생."""
    return tone_rule_for(context).sentences_per_block * CHARS_PER_SENTENCE


PLAN_PROMPT_ID: Final = "composition/counsel_plan"
PLAN_PROMPT_VERSION: Final = "0.1"

_PLAN_PROMPT_PATH: Final = (
    Path(__file__).resolve().parents[2]
    / "llm"
    / "prompts"
    / "templates"
    / "composition"
    / "counsel_plan.txt"
)


@lru_cache
def _plan_template() -> str:
    return _PLAN_PROMPT_PATH.read_text(encoding="utf-8")


def assemble_plan_prompt(
    contexts: Mapping[str, DraftContext],
    student_refs: Sequence[str],
    *,
    max_points: int,
) -> str:
    """plan 프롬프트 — 학생별 근거를 **record_id와 함께** 제시한다(결정론).

    record_id가 없는 fact(집계·기준선 파생)는 인용 대상이 아니므로 제시하지 않는다 —
    LLM에게 인용할 수 없는 근거를 보여주면 지어내게 된다.
    """
    blocks: list[str] = []
    for ref in student_refs:
        context = contexts.get(ref)
        if context is None:
            continue
        lines = [
            f"  - {fact.label}: {fact.value} (record_id={fact.record_id})"
            for fact in context.facts
            if fact.record_id
        ]
        blocks.append(f"{ref}\n" + ("\n".join(lines) if lines else "  - (인용 가능한 근거 없음)"))
    return _plan_template().format(
        max_points=max_points, student_blocks="\n".join(blocks)
    )


def parse_plan_response(text: str, student_refs: Sequence[str]) -> dict[str, list[str]]:
    """`학생참조 | 강조점1; 강조점2` 형식을 파싱한다 — 관용적이되 결정론.

    형식이 어긋난 줄·미지 학생은 조용히 버린다(근거 실존 검증이 뒤에서 한 번 더 걸러낸다).
    """
    known = set(student_refs)
    parsed: dict[str, list[str]] = {}
    for line in text.splitlines():
        if "|" not in line:
            continue
        ref, _, rest = line.partition("|")
        ref = ref.strip()
        if ref not in known:
            continue
        points = [p.strip() for p in rest.split(";") if p.strip()]
        if points:
            parsed[ref] = points
    return parsed


class RedactionBlockedError(LlmError):
    """마스킹 불확실 — 전송하지 않았다(fail-closed · 불변식 3)."""


@runtime_checkable
class CounselPlanner(Protocol):
    """plan 노드 — 학생별 강조점을 고른다(LLM 1회). 새 사실 생성 금지."""

    async def plan(
        self,
        *,
        contexts: Mapping[str, DraftContext],
        student_refs: Sequence[str],
        execution_context: ExecutionContext,
    ) -> dict[str, list[str]]:
        """student_ref → 강조점 목록(각 항목에 근거 `record_id` 동반)."""
        ...


@runtime_checkable
class DraftWriter(Protocol):
    """generate_draft 노드 — 블록 본문을 만든다."""

    async def write(
        self,
        *,
        context: DraftContext,
        execution_context: ExecutionContext,
        emphasis: Sequence[str] = (),
    ) -> str:
        """조립·마스킹을 마친 프롬프트로 초안 본문을 받는다.

        `emphasis`는 근거 실존 검증을 통과한 강조점이다 — 비면 프롬프트가 현행과 동일하다.
        """
        ...


class GatewayDraftWriter:
    """실 경로 — `llm/gateway`를 role=counselor로 호출한다.

    프롬프트 조립 → **redaction(fail-closed)** → gateway 순서를 지킨다.
    재시도를 여기서 돌리지 않는다 — 게이트 실패 재생성은 그래프가 ≤3으로 관리하고,
    전송 재시도는 조립부가 `transport_retry`로 주입한다(브리핑 narrator와 동일하게 0회).
    """

    def __init__(self, gateway: LlmGateway) -> None:
        self._gateway = gateway

    async def write(
        self,
        *,
        context: DraftContext,
        execution_context: ExecutionContext,
        emphasis: Sequence[str] = (),
    ) -> str:
        redacted = redact(assemble_prompt(context, emphasis))
        if redacted.uncertain:  # fail-closed — 불확실하면 LLM에 보내지 않는다
            raise RedactionBlockedError("상담 초안 프롬프트의 마스킹이 불확실하다")
        result = await self._gateway.complete(
            LLMRequest(
                role=ModelRole.COUNSELOR,
                prompt=redacted.masked_text,
                prompt_id=PROMPT_ID,
                prompt_version=PROMPT_VERSION,
                generation_params=GenerationParams(temperature=0.0),
            ),
            execution_context,
        )
        # outcome≠OK를 빈 문자열로 삼키면 장애가 게이트 실패(`gate_exhausted:empty`)로
        # **오분류**된다 — 그러면 서킷 카운터도 안 오르고 알럿이 뜨지 않는다.
        # LlmError로 승격해 `llm_failed` 경로(서킷 포함)로 태운다(error_codes §3).
        if result.outcome is not CallOutcome.OK:
            raise LlmError(f"counselor 호출 실패 outcome={result.outcome.value}")
        text = (result.text or "").strip()
        if not text:
            raise LlmError("counselor 응답이 비었다")
        return text


class GatewayPlanner:
    """실 plan 경로 — `GatewayDraftWriter`와 **같은 규율**이다.

    프롬프트 조립 → **redact(fail-closed)** → gateway(role=counselor) → 구조화 파싱.
    파싱 실패·빈 응답은 예외로 올리지 않고 **빈 강조점**으로 수렴한다 — plan은 부가정보이고
    잡을 죽일 사유가 아니다(그래프의 무강조 진행 경로로 이어진다).

    registry.yaml에 등재하지 않는다 — composition 프롬프트는 템플릿 파일 직접 읽기가 선례다
    (`briefing.txt`·`counsel_pack.txt`와 동일. registry는 B의 `pg.*` 전용).
    """

    def __init__(self, gateway: LlmGateway, *, max_points: int = 3) -> None:
        self._gateway = gateway
        self._max_points = max_points

    async def plan(
        self,
        *,
        contexts: Mapping[str, DraftContext],
        student_refs: Sequence[str],
        execution_context: ExecutionContext,
    ) -> dict[str, list[str]]:
        prompt = assemble_plan_prompt(
            contexts, student_refs, max_points=self._max_points
        )
        redacted = redact(prompt)
        if redacted.uncertain:  # fail-closed — 불확실하면 LLM에 보내지 않는다(불변식 3)
            raise RedactionBlockedError("plan 프롬프트의 마스킹이 불확실하다")
        result = await self._gateway.complete(
            LLMRequest(
                role=ModelRole.COUNSELOR,
                prompt=redacted.masked_text,
                prompt_id=PLAN_PROMPT_ID,
                prompt_version=PLAN_PROMPT_VERSION,
                generation_params=GenerationParams(temperature=0.0),
            ),
            execution_context,
        )
        if result.outcome is not CallOutcome.OK or not (result.text or "").strip():
            return {}  # 무강조 진행 — 잡 실패가 아니다
        return parse_plan_response(result.text or "", student_refs)


class FakeCounselProvider:
    """결정론 Fake — 시나리오 주입식. CI·테스트 기본값.

    LLM을 호출하지 않고 주입된 시나리오를 순서대로 소비한다. 시계·난수를 쓰지 않는다.
    """

    def __init__(
        self,
        *,
        drafts: Sequence[str | Exception] = (),
        emphasis: Mapping[str, list[str]] | None = None,
    ) -> None:
        self._drafts = tuple(drafts)
        self._emphasis = dict(emphasis or {})
        self.write_calls: list[str] = []
        self.plan_calls: list[tuple[str, ...]] = []

    async def plan(
        self,
        *,
        contexts: Mapping[str, DraftContext],
        student_refs: Sequence[str],
        execution_context: ExecutionContext,
    ) -> dict[str, list[str]]:
        self.plan_calls.append(tuple(student_refs))
        if self._emphasis:
            return {ref: list(self._emphasis.get(ref, [])) for ref in student_refs}
        # 기본 시나리오 — 컨텍스트가 실제로 제공한 근거로만 강조점을 만든다(새 사실 없음).
        planned: dict[str, list[str]] = {}
        for ref in student_refs:
            context = contexts.get(ref)
            if context is None or not context.facts:
                continue
            fact = context.facts[0]
            planned[ref] = [f"{fact.label} {fact.value} (record_id=le_{ref})"]
        return planned

    async def write(
        self,
        *,
        context: DraftContext,
        execution_context: ExecutionContext,
        emphasis: Sequence[str] = (),
    ) -> str:
        del emphasis  # Fake는 강조점을 소비하지 않는다 — 시나리오 순서로만 응답한다
        index = len(self.write_calls)
        self.write_calls.append(context.student_ref)
        if index < len(self._drafts):
            step = self._drafts[index]
        elif self._drafts:
            step = self._drafts[-1]
        else:
            step = context.fallback_text
        if isinstance(step, Exception):
            raise step
        return step


class FakeCounselLlmProvider:
    """`LLMProvider` 대역 — gateway 경로의 CI 기본값(실 벤더 호출 없음).

    `composition/provider.FakeBriefProvider`와 같은 자리다. 결정론이며 시계·난수를 쓰지 않는다.
    """

    def __init__(self, text: str = "이번 주 학습 상황을 정리해 드립니다.") -> None:
        self._text = text

    @property
    def name(self) -> str:
        return "fake-counsel"

    async def complete(
        self, request: LLMRequest, context: ExecutionContext
    ) -> LLMResult:
        return LLMResult(
            outcome=CallOutcome.OK,
            text=self._text,
            provider=self.name,
            model="template",
            usage=TokenUsage(tokens_in=0, tokens_out=0, cost_usd=0.0),
            latency_ms=0,
        )


__all__ = [
    "PLAN_PROMPT_ID",
    "PLAN_PROMPT_VERSION",
    "GatewayPlanner",
    "assemble_plan_prompt",
    "parse_plan_response",
    "CHARS_PER_SENTENCE",
    "CounselPlanner",
    "DraftWriter",
    "FakeCounselLlmProvider",
    "FakeCounselProvider",
    "GatewayDraftWriter",
    "RedactionBlockedError",
    "max_chars_for",
]
