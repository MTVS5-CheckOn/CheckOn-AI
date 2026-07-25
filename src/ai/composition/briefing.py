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

import re
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from ai.composition.briefing_gate import MAX_BRIEF_LENGTH, check_brief_gate
from ai.contracts.detection import Brief, Signal
from ai.contracts.execution import ExecutionContext
from ai.contracts.llm import (
    LLMProvider,
    LLMRequest,
    LlmTimeout,
    LlmUnavailable,
    ModelRole,
    ParseFailed,
)
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
PROMPT_VERSION = "0.1"

_NUMBER_RE = re.compile(r"\d+")


@lru_cache
def _template() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _assemble_prompt(signal: Signal) -> str:
    """프롬프트 조립 — signal_type·lifecycle·초안(엔진 brief.text). 학생 식별자·과제명 없음.

    초안 = 엔진이 만든 결정론 brief(무주어·무실명·rule detail·핵심 수치 포함). detection
    무변경으로 얻는 유일한 grounding 소스이며, 여기 담긴 숫자만 문장에 허용된다.
    """
    return _template().format(
        signal_type=signal.signal_type.value,
        lifecycle=signal.lifecycle.value,
        draft=signal.brief.text,
        max_length=MAX_BRIEF_LENGTH,
    )


def _allowed_numbers(signal: Signal) -> frozenset[str]:
    """초안(엔진 brief)에 실존하는 숫자만 허용 — LLM의 숫자 fabrication 차단(EXACT 대조)."""
    return frozenset(_NUMBER_RE.findall(signal.brief.text))


def _fallback(signal: Signal, *, gate_passed: bool) -> Brief:
    """템플릿 폴백 — 엔진이 이미 만든 결정론 brief(detection/brief 산출)를 재사용한다.

    signal.brief.text가 그 템플릿(중복 구현 금지). fallback_used만 True로 바꿔 실의미 부여.
    """
    return Brief(
        text=signal.brief.text,
        gate_passed=gate_passed,
        fallback_used=True,
    )


async def make_brief(
    signal: Signal,
    provider: LLMProvider,
    *,
    context: ExecutionContext,
    now: Callable[[], float],
    deadline: float,
) -> tuple[Brief, str]:
    """신호 하나를 문장화한다. (brief, outcome 라벨[로그용]) 반환.

    now/deadline은 시간 예산(호출당·총)을 호출자(라우터)가 관리하도록 주입한다
    (datetime.now() 직접 호출 금지 — 03_coding_rules §3).
    """
    if now() >= deadline:
        return _fallback(signal, gate_passed=False), "budget_exhausted"

    redacted = redact(_assemble_prompt(signal))
    if redacted.uncertain:  # fail-closed — 마스킹 불확실이면 LLM에 안 보낸다
        return _fallback(signal, gate_passed=False), "redaction_blocked"

    request = LLMRequest(
        role=ModelRole.GENERATOR,
        prompt=redacted.masked_text,
        prompt_id=PROMPT_ID,
        prompt_version=PROMPT_VERSION,
    )
    last_reason = ""
    for _ in range(MAX_REGEN):
        try:
            result = await provider.complete(request, context)
        except (LlmUnavailable, LlmTimeout, ParseFailed):
            return _fallback(signal, gate_passed=False), "llm_failed"  # 재시도 없이 즉시
        text = (result.text or "").strip()
        gate = check_brief_gate(text, _allowed_numbers(signal))
        if gate.passed:
            return (
                Brief(text=text, gate_passed=True, fallback_used=False),
                result.outcome.value,
            )
        last_reason = gate.reason
    return _fallback(signal, gate_passed=False), f"gate_exhausted:{last_reason}"
