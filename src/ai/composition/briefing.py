"""브리핑 문장화(ⓐ) — 신호를 자연어 한 줄로. LLM + 왜곡 게이트 + 템플릿 폴백.

소유: 박진희 (A 단독). 02_design 불변: **detection 코드·판정 무변경, LLM 0 유지** —
문장화는 detection 밖 소비 기능이다. 감지 판정(신호·score·lifecycle·evidence)은 문장화
실패와 무관하게 무변(분기표 #6).

분기표(09 §3 · error_codes §3):
- LLM 실패(LlmUnavailable·LlmTimeout·ParseFailed) → **재시도 없이** 즉시 템플릿 폴백
  (재시도는 게이트웨이 후속 — 여기 넣지 않는다).
- redaction uncertain → 미전송(fail-closed) → 템플릿 폴백.
- 왜곡 게이트 실패 → 재생성 ≤3 → 소진 시 템플릿 폴백(gate_passed=False).
- 시간 예산 소진(호출 전 deadline 초과) → 템플릿 폴백.

폴백 텍스트는 `detection/brief.py`의 결정론 템플릿을 재사용한다(중복 구현 금지).
`fallback_used=True`가 이제 실의미(LLM 실패·게이트 소진·마스킹 불확실·예산 소진).
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from ai.composition.briefing_context import BriefingContext, render_evidence_block
from ai.composition.briefing_gate import MAX_BRIEF_LENGTH, check_brief_gate
from ai.contracts.detection import Brief
from ai.contracts.execution import ExecutionContext, GenerationParams
from ai.contracts.llm import (
    LLMProvider,
    LLMRequest,
    LlmTimeout,
    LlmUnavailable,
    ModelRole,
    ParseFailed,
)
from ai.llm.gateway import LlmGateway
from ai.runtime.redaction import redact

_PROMPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "llm"
    / "prompts"
    / "templates"
    / "composition"
    / "briefing.txt"
)

#: 왜곡 게이트 실패 시 재생성 상한(error_codes §3 — 블록 단위 ≤3). CLAUDE.md 불변식 6.
MAX_REGEN = 3
PROMPT_ID = "composition/briefing"
PROMPT_VERSION = "0.2"

#: 브리핑은 한 문장(≤MAX_BRIEF_LENGTH자)이라 생성 토큰 상한을 좁게 준다 — 서버 기본값의
#: 과생성·지연을 막는다(v2 프리뷰 지연 개선). 게이트 길이 상한과 별개의 성능 제어.
_BRIEF_MAX_TOKENS = 128
_GEN_PARAMS = GenerationParams(max_tokens=_BRIEF_MAX_TOKENS)


@lru_cache
def _template() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _assemble_prompt(ctx: BriefingContext) -> str:
    """프롬프트 조립 — 구조화 근거 블록(v2). 엔진 초안 문장은 넣지 않는다(모사 방지).

    근거 블록은 briefing_context가 소유·렌더한다(무주어·무실명·수치·enum 태그만).
    이 블록이 제공한 수치만 게이트에서 허용된다(ctx.allowed_numbers).
    """
    return _template().format(
        evidence_block=render_evidence_block(ctx),
        max_length=MAX_BRIEF_LENGTH,
    )


def _fallback(ctx: BriefingContext) -> Brief:
    """템플릿 폴백 — 엔진 결정론 brief(ctx.fallback_text)를 재사용한다(중복 구현 금지).

    LLM 실패·게이트 소진·마스킹 불확실·예산 소진 때 되돌아간다. fallback_used=True로 실의미.
    """
    return Brief(text=ctx.fallback_text, gate_passed=False, fallback_used=True)


async def make_brief(
    ctx: BriefingContext,
    completer: LlmGateway | LLMProvider,
    *,
    context: ExecutionContext,
    now: Callable[[], float],
    deadline: float,
) -> tuple[Brief, str]:
    """근거 패키지 하나를 문장화한다. (brief, outcome 라벨[로그용]) 반환.

    completer는 프로덕션에선 LlmGateway(role=narrator 라우팅·전송 재시도 0)이며, 단위
    테스트는 provider mock을 직접 주입한다 — 둘 다 `complete(request, context)` 동형이다
    (gateway 경유는 라우터가 조립). now/deadline은 시간 예산(호출당·총)을 호출자(라우터)가
    관리하도록 주입한다 (datetime.now() 직접 호출 금지 — 03_coding_rules §3).
    """
    if now() >= deadline:
        return _fallback(ctx), "budget_exhausted"

    redacted = redact(_assemble_prompt(ctx))
    if redacted.uncertain:  # fail-closed — 마스킹 불확실이면 LLM에 안 보낸다
        return _fallback(ctx), "redaction_blocked"

    request = LLMRequest(
        role=ModelRole.NARRATOR,
        prompt=redacted.masked_text,
        prompt_id=PROMPT_ID,
        prompt_version=PROMPT_VERSION,
        generation_params=_GEN_PARAMS,
    )
    allowed = ctx.allowed_numbers()
    last_reason = ""
    for _ in range(MAX_REGEN):
        try:
            result = await completer.complete(request, context)
        except (LlmUnavailable, LlmTimeout, ParseFailed):
            return _fallback(ctx), "llm_failed"  # 재시도 없이 즉시
        text = (result.text or "").strip()
        gate = check_brief_gate(text, allowed)
        if gate.passed:
            return (
                Brief(text=text, gate_passed=True, fallback_used=False),
                result.outcome.value,
            )
        last_reason = gate.reason
    return _fallback(ctx), f"gate_exhausted:{last_reason}"
