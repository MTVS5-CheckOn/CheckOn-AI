"""브리핑 문장화(ⓐ) — 신호를 자연어 한 줄로. LLM + 왜곡 게이트 + 템플릿 폴백.

소유: 박진희 (A 단독). 02_design 불변: **detection 코드·판정 무변경, LLM 0 유지** —
문장화는 detection 밖 소비 기능이다. 감지 판정(신호·score·lifecycle·evidence)은 문장화
실패와 무관하게 무변(분기표 #6).

분기표(09 §3 · error_codes §3):
- LLM 실패(`LlmError` 전체 — 4xx도 plain LlmError로 온다) → **재시도 없이** 즉시 템플릿
  폴백(재시도는 게이트웨이 후속 — 여기 넣지 않는다).
- redaction uncertain(전송 전 로컬 검사) → 미전송(fail-closed) → 템플릿 폴백.
- `RedactionBlocked`(전송 경로에서 차단) → 템플릿 폴백 + **outcome=redaction_blocked 유지**.
  LlmError 서브클래스이므로 **먼저** 받는다 — 재시도 금지+알럿 의미를 잃지 않게.
- 왜곡 게이트 실패 → 재생성 ≤3 → 소진 시 템플릿 폴백(gate_passed=False).
- 시간 예산 소진(deadline 초과) → 템플릿 폴백. **매 시도 전에 재검사**한다(05 §6-4) —
  진입 시 1회만 보면 게이트 실패 재생성이 예산 이후에도 호출을 계속한다.

폴백 텍스트는 `detection/brief.py`의 결정론 템플릿을 재사용한다(중복 구현 금지).
`fallback_used=True`가 이제 실의미(LLM 실패·게이트 소진·마스킹 불확실·예산 소진).
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from ai.composition.briefing_context import BriefingContext, render_evidence_block
from ai.composition.briefing_gate import MAX_BRIEF_LENGTH, check_brief_gate
from ai.composition.determinism import deterministic_params
from ai.composition.gate_feedback import instruction_for, render_feedback_block
from ai.contracts.detection import Brief
from ai.contracts.execution import ExecutionContext
from ai.contracts.llm import (
    CallOutcome,
    LlmError,
    LLMProvider,
    LLMRequest,
    ModelRole,
    RedactionBlocked,
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

#: 🔴 종전에는 `max_tokens`만 있어 **temperature·seed가 둘 다 미지정**이었다 — 어댑터
#: 기본 temperature(0.7)로 나가 같은 신호가 매번 다른 문장을 냈다(99 ㊼).
#: 재현 축의 **정본은 `llm/determinism.py`**(B 소유)이고
#: `composition/determinism.py`는 재수출이다(99 ⓨ).
BRIEF_GEN_PARAMS = deterministic_params(max_tokens=_BRIEF_MAX_TOKENS)


@lru_cache
def _template() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _assemble_prompt(ctx: BriefingContext, gate_feedback: str = "") -> str:
    """프롬프트 조립 — 구조화 근거 블록(v2). 엔진 초안 문장은 넣지 않는다(모사 방지).

    근거 블록은 briefing_context가 소유·렌더한다(무주어·무실명·수치·enum 태그만).
    이 블록이 제공한 수치만 게이트에서 허용된다(ctx.allowed_numbers).

    `gate_feedback`은 직전 게이트 실패의 수정 지시다(05 §6-2) — **1회차는 빈 문자열**
    이라 문면이 종전과 바이트 동일하다. 렌더러는 counsel과 **같은 것**을 쓴다.
    """
    return _template().format(
        evidence_block=render_evidence_block(ctx) + render_feedback_block(gate_feedback),
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
    allowed = ctx.allowed_numbers()
    last_reason = ""
    for _ in range(MAX_REGEN):
        # 예산 검사 — **호출 전, 매 반복**(05 §6-4 · 점검 4-6a). 종전에는 함수 진입 시
        # 1회만 봤고, 게이트 실패 재생성은 그 뒤에 일어나 예산을 넘긴 2·3회차 호출이
        # 그대로 나갔다(호출당 수 초 × 잔여 횟수). 루프가 최소 1회는 돌므로 이 검사가
        # 종전 진입 검사를 그대로 포섭한다 — 같은 조건을 두 곳에 두지 않는다.
        if now() >= deadline:
            return _fallback(ctx), "budget_exhausted"

        # 직전 게이트 사유를 수정 지시로 실어 다시 조립한다(05 §6-2) — 같은 프롬프트를
        # 상한까지 반복하면 같은 실패만 되풀이한다(비용 3배·개선 0 · 99 D ㉙).
        # 1회차는 last_reason이 비어 지시도 비므로 문면이 종전과 바이트 동일하다.
        # 조립이 루프 안으로 들어왔으므로 **마스킹도 시도마다** 다시 건다 — 지시 문구가
        # 붙은 문면을 검사 없이 내보내지 않는다(fail-closed 유지).
        redacted = redact(_assemble_prompt(ctx, instruction_for(last_reason)))
        if redacted.uncertain:  # fail-closed — 마스킹 불확실이면 LLM에 안 보낸다
            return _fallback(ctx), "redaction_blocked"
        request = LLMRequest(
            role=ModelRole.NARRATOR,
            prompt=redacted.masked_text,
            prompt_id=PROMPT_ID,
            prompt_version=PROMPT_VERSION,
            generation_params=BRIEF_GEN_PARAMS,
        )
        try:
            result = await completer.complete(request, context)
            # 🔴 **두 검사를 `try` 안에 둔다**(99 ㊝) — 밖에 두면 `except LlmError`가 못 받고
            #    `Brief(text="")`가 `min_length=1`에 걸려 **`ValidationError`가 detect까지
            #    올라간다**(실측: 빈 응답 1건 → `POST /v1/detect` 500).
            # ⚠ classify와 **처방이 다르다** — 거긴 `raise`가 밖으로 나가는 게 맞았지만
            #    (라우터가 5xx로 변환) 브리핑은 **분기표 ⑥ "어떤 실패든 감지 판정 무변"**
            #    이라 폴백으로 수렴해야 한다. 같은 어휘(`LlmError`)로 던지되 **여기서 받는다.**
            # ⚠ 두 검사를 한 줄로 합치지 않는다 — 뒤집기가 각각 red가 되어야 한다(#122).
            if result.outcome is not CallOutcome.OK:
                raise LlmError(f"브리핑 호출 실패 outcome={result.outcome.value}")
            text = (result.text or "").strip()
            if not text:
                # 실제로 겪었다 — `max_completion_tokens` 문제로 빈 응답이 왔다(99 ⓟ).
                raise LlmError("브리핑 응답이 비었다")
        except RedactionBlocked:
            # 🔴 `LlmError`보다 **먼저** 받는다 — RedactionBlocked는 LlmError의 서브클래스라
            # 순서가 뒤바뀌면 "재시도 금지+알럿"(error_codes §3)이 llm_failed로 뭉개진다.
            # except 절 순서가 곧 계약이다. 루프 머리의 로컬 fail-closed와 같은 outcome을 쓴다.
            return _fallback(ctx), "redaction_blocked"
        except LlmError:
            # LlmError 베이스로 받는다 — `openai_compat`은 4xx(컨텍스트 한도 초과 등)를
            # **plain LlmError**로 올리므로 좁은 튜플이면 결정론 판정이 멀쩡한 채 500이 된다.
            return _fallback(ctx), "llm_failed"  # 재시도 없이 즉시
        gate = check_brief_gate(text, allowed)
        if gate.passed:
            return (
                Brief(text=text, gate_passed=True, fallback_used=False),
                result.outcome.value,
            )
        last_reason = gate.reason
    return _fallback(ctx), f"gate_exhausted:{last_reason}"
