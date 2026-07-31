"""refine(핑퐁 다듬기) — `part_a/06_refine_policy.md` §3·§4.

**한 줄 원칙: 강사 지시가 게이트를 이기지 못한다.** 매 턴 결과도 게이트 전체를 재통과해야
하고, 통과하지 못하면 초안은 **직전 버전 그대로** 남는다(인박스 계약 §6).

두 층으로 막는다(06 §3).
- **C 분류 — 사전 정적 검사.** 정책 위반 지시는 **LLM을 부르지 않고** 차단한다.
  규칙은 `refine_rules.yaml`이 소유한다(03 §1 — 어휘가 늘어도 코드 diff가 없어야 한다).
- **A·B 분류 — 생성 후 게이트.** 스타일 지시는 그대로 조립하고, 결과를 게이트에 태운다.
  근거 없는 수치는 `evidence_missing`, 금칙 표현은 `tone_violation`으로 옮긴다.

차단은 **에러가 아니다** — 호출자는 200으로 응답한다(불변식 4). 사유 코드는
`contracts/gates.BlockedReason`(양자 승인)의 값만 쓴다.

⚠ 쿼터를 다루지 않는다 — 턴 카운트·차단·잔여 표시는 전부 백엔드다(7/15 BE-4).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final

import yaml  # type: ignore[import-untyped]

from ai.composition.counsel.gate import check_counsel_gate
from ai.composition.counsel.provider import DraftWriter, RedactionBlockedError, max_chars_for
from ai.composition.gate_feedback import instruction_for
from ai.contracts.composition import DraftContext
from ai.contracts.execution import ExecutionContext
from ai.contracts.gates import BlockedReason
from ai.contracts.llm import LlmError

_RULES_PATH: Final = Path(__file__).parent / "refine_rules.yaml"

#: 게이트 사유 접두 → 차단 사유(06 §4). 목록에 없는 접두는 `tone_violation`으로 수렴한다 —
#: 게이트를 통과하지 못한 결과라는 의미는 같고, `BlockedReason`은 닫힌 공용 enum이라
#: 형식 실패(`too_long`·`symbol`·`token_leak`·`empty`)를 위한 값이 없다.
#: 사유 세분은 99 D에 후속으로 등록했다 — 여기서 새 어휘를 발명하지 않는다.
_GATE_REASON_TO_BLOCK: Final[dict[str, BlockedReason]] = {
    "ungrounded_number": BlockedReason.EVIDENCE_MISSING,
    "forbidden": BlockedReason.TONE_VIOLATION,
}


class RefineRulesError(ValueError):
    """정적 차단 규칙 데이터가 계약을 위반했다 — 기동 실패(fail-closed)."""


@dataclass(frozen=True)
class RefineRules:
    """`refine_rules.yaml` 전체 — 순수 데이터."""

    version: str
    #: (사유, 패턴) 순서 고정 — 같은 지시문에 항상 같은 사유가 나온다(결정론).
    entries: tuple[tuple[BlockedReason, tuple[str, ...]], ...]


@lru_cache
def load_refine_rules() -> RefineRules:
    raw = yaml.safe_load(_RULES_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RefineRulesError("refine_rules.yaml이 매핑이 아니다")
    rules = raw.get("rules")
    if not isinstance(rules, list) or not rules:
        raise RefineRulesError("refine_rules.yaml에 rules가 없다")
    entries: list[tuple[BlockedReason, tuple[str, ...]]] = []
    for rule in rules:
        reason = rule.get("reason")
        patterns = rule.get("patterns")
        if reason not in {member.value for member in BlockedReason}:
            raise RefineRulesError(f"BlockedReason에 없는 사유: {reason}")
        if not isinstance(patterns, list) or not patterns:
            raise RefineRulesError(f"{reason}에 패턴이 없다")
        entries.append((BlockedReason(reason), tuple(str(p) for p in patterns)))
    return RefineRules(version=str(raw.get("version", "")), entries=tuple(entries))


def screen_instruction(instruction: str) -> BlockedReason | None:
    """C 분류 사전 정적 검사 — 걸리면 **LLM을 부르지 않는다**(06 §3·§5).

    순수 함수다. 규칙 순서가 고정이라 같은 지시문에 항상 같은 사유가 나온다.
    """
    for reason, patterns in load_refine_rules().entries:
        if any(pattern in instruction for pattern in patterns):
            return reason
    return None


@dataclass(frozen=True)
class RefineOutcome:
    """한 턴의 결과 — 반영 또는 차단. 예외를 쓰지 않는다(게이트 거부 = 정상 반환값)."""

    applied: bool
    text: str | None = None
    blocked_reason: BlockedReason | None = None


async def refine_draft(
    *,
    context: DraftContext,
    instruction: str,
    writer: DraftWriter,
    execution_context: ExecutionContext,
    regen_max: int,
) -> RefineOutcome:
    """지시를 반영해 초안을 다시 만든다 — **매 턴 게이트 전체 재통과**(06 §1).

    ① 사전 정적 검사(C 분류) → 걸리면 미호출 차단.
    ② 지시를 프롬프트에 조립해 생성 → 게이트. 실패하면 사유를 다음 시도에 실어 재생성한다
       (05 §6-2 — 같은 상한 ≤3 안에서만. 불변식 6).
    ③ 상한까지 통과 못 하면 차단으로 수렴하고 **초안은 직전 버전 유지**(호출자 책임).
    """
    blocked = screen_instruction(instruction)
    if blocked is not None:
        return RefineOutcome(applied=False, blocked_reason=blocked)

    max_chars = max_chars_for(context)
    last_reason = ""
    for _ in range(regen_max):
        try:
            text = await writer.write(
                context=context,
                execution_context=execution_context,
                gate_feedback=instruction_for(last_reason),
                refine_instruction=instruction,
            )
        except RedactionBlockedError:  # fail-closed — 미전송(불변식 3)
            return RefineOutcome(applied=False, blocked_reason=BlockedReason.PII_EXPOSURE)
        except LlmError:
            return RefineOutcome(applied=False, blocked_reason=BlockedReason.TONE_VIOLATION)
        gate = check_counsel_gate(text, context, max_chars=max_chars)
        if gate.passed:
            return RefineOutcome(applied=True, text=text)
        last_reason = gate.reason
    prefix = last_reason.partition(":")[0]
    return RefineOutcome(
        applied=False,
        blocked_reason=_GATE_REASON_TO_BLOCK.get(prefix, BlockedReason.TONE_VIOLATION),
    )


__all__ = [
    "RefineOutcome",
    "RefineRules",
    "RefineRulesError",
    "load_refine_rules",
    "refine_draft",
    "screen_instruction",
]
