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

from collections.abc import Sequence
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
from ai.runtime.errors import RedactionUncertain
from ai.runtime.redaction import redact

_RULES_PATH: Final = Path(__file__).parent / "refine_rules.yaml"

#: 게이트 사유 접두 → 차단 사유(06 §4). **게이트가 내는 사유 전수를 명시 등재한다** —
#: `tests/ai/contract/test_gate_feedback_coverage.py`가 CI에서 대조한다.
#:
#: 🔴 **왜 기본값에 맡기지 않는가.** `.get(prefix, TONE_VIOLATION)`이 있으니 등재를 빼도
#: 런타임은 돈다 — 그래서 #113이 `internal_term`을 새로 만들면서 여기만 빠뜨렸고 아무도
#: 몰랐다. **"의도적으로 수렴시켰다"와 "빠뜨렸다"는 다른 사건인데 기본값으로 흐르면
#: 코드에서 구분되지 않는다.** 기본값은 안전망으로 남기되 정상 상태에서는 안 쓰인다.
#:
#: ⚠ **`BlockedReason`은 닫힌 공용 enum(양자)이라 형식 실패에 맞는 값이 없다**(99 ㊴).
#: 아래 다섯은 전부 `TONE_VIOLATION`으로 **수렴시킨 것**이지 그 값이 정확해서가 아니다 —
#: 강사에게는 "안전 기준에 걸려 다시 썼다"로 보이고, 형식 실패도 게이트 미통과라는 점에서
#: 같은 쪽이다. ㊴가 닫히면 **이 dict 한 곳만** 고치면 된다.
_GATE_REASON_TO_BLOCK: Final[dict[str, BlockedReason]] = {
    # 근거 축 — 유일하게 `BlockedReason`에 정확한 값이 있다.
    "ungrounded_number": BlockedReason.EVIDENCE_MISSING,
    # 톤 축 — 원래 이 값이 뜻하는 것.
    "forbidden": BlockedReason.TONE_VIOLATION,
    # ── 아래부터 ㊴ 수렴분: 맞는 enum 값이 없어 tone_violation으로 모은다 ──
    "internal_term": BlockedReason.TONE_VIOLATION,  # 지시문 누출(#113)
    "too_long": BlockedReason.TONE_VIOLATION,
    "symbol": BlockedReason.TONE_VIOLATION,
    "token_leak": BlockedReason.TONE_VIOLATION,
    "empty": BlockedReason.TONE_VIOLATION,
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
    previous_text: str = "",
    emphasis: Sequence[str] = (),
) -> RefineOutcome:
    """지시를 반영해 초안을 다시 만든다 — **매 턴 게이트 전체 재통과**(06 §1).

    ① 사전 정적 검사(C 분류) → 걸리면 미호출 차단.
    ② 지시를 프롬프트에 조립해 생성 → 게이트. 실패하면 사유를 다음 시도에 실어 재생성한다
       (05 §6-2 — 같은 상한 ≤3 안에서만. 불변식 6).
    ③ 상한까지 통과 못 하면 차단으로 수렴하고 **초안은 직전 버전 유지**(호출자 책임).

    🔴 **LLM 장애는 잡지 않는다 — 밖으로 나간다.** 종전에는 `except LlmError`가
    `blocked_reason=TONE_VIOLATION`으로 바꿔 라우터가 200을 냈고, 화면에는 06 §4 표의
    *"해당 표현은 안전 기준에 걸려…"* 가 떴다 — **벤더가 죽었을 때 강사에게 말투가
    문제라고 말하고 있었다.** `error_codes.md`:261이 이미 정해 놨다: *"LLM 장애는 폴백이
    아니다 — `LlmUnavailable`·`LlmTimeout`은 503으로 올라간다(§4). '안 하기로 판단한 것'과
    '못 한 것'을 같은 상태로 뭉개지 않는다."* **계약이 예정하고 있었고 코드만 안 따랐다.**
    변환은 라우터가 `domain_error_for`로 한다(경계 한 곳).

    ⚠ `RefineOutcome`에 실패 필드를 만들지 않았다 — 만들면 호출자가 또 "실패인데 200"을
    조립할 수 있는 자리가 생긴다. **예외로 나가는 것이 요지다.**
    ⚠ 게이트 소진은 여전히 200이다(`_GATE_REASON_TO_BLOCK`) — 그건 진짜 판단이다.

    🔴 `previous_text`는 **직전 턴의 본문**이다. 없으면 매 턴 원본 근거에서 새로 쓰므로
    턴1의 "짧게"가 턴2에서 되살아난다 — 다듬기가 누적되지 않는다. 기본값이 빈 문자열인
    이유는 호출자(라우터)가 아직 초안을 모를 수 있어서가 아니라, **이 파라미터가 없던
    시절의 동작을 명시적으로 재현할 수 있게** 두기 위함이다(테스트가 대조군으로 쓴다).

    🔴 `emphasis`는 **최초 생성이 고른 검증 통과 강조점**이다(99 ㉮). 안 넘기면 다듬기
    턴마다 강조점 없이 다시 쓰므로 *"1턴에 강조한 것이 2턴에 사라진다"* — 강사가 refine을
    한 번이라도 누르면 **매번** 그렇다. 최초 생성(`graph.py:201`)이 같은 인자로 같은 값을
    넘기므로 **프롬프트 문면도 같다**(`prompt.render_emphasis_block`).

    ⚠ **기본값 `()`는 「강조점 없음」이 아니라 「안 넘겼음」이다.** 지금까지의 상태가 정확히
    후자였고 그게 이 결함이다 — 둘이 같은 값이라 **코드에서 구분되지 않는다.**
    「없음」의 사유는 별도 축이 든다(`CounselPackResultRecord.plan_outcome` · ㉲):
    `OK`+빈 값은 고를 게 없었던 것, `ALL_DROPPED`는 전량 드롭, `LLM_FAILED`·`UNPARSED`는
    plan 실패다. **이 함수는 사유를 모른다** — 호출자가 값을 넘길 책임을 진다.
    """
    # 🔴 `regen_max=0`이면 루프가 0회 — LLM을 한 번도 안 부르고 "상한 소진"으로 차단된다.
    # 강사에게는 게이트가 막은 것으로 보이는데 실은 아무것도 시도하지 않았다.
    # ⚠ `graph.py`는 **조립 시점**(`build_counsel_graph`)에 막지만 여기는 **함수 진입**이다 —
    #   refine에는 조립 단계가 없고 라우터가 매 턴 직접 부르므로, 값이 들어오는 가장 이른
    #   지점이 여기다. 두 경로 다 "값이 처음 들어오는 곳"이라는 규칙은 같다.
    #   검사가 LLM 호출보다 앞이라 비용도 0이다.
    if regen_max < 1:
        raise ValueError(
            f"regen_max는 1 이상이어야 한다(받은 값: {regen_max}) — 0이면 생성 시도가 "
            "0회인데 상한 소진으로 차단된다"
        )

    blocked = screen_instruction(instruction)
    if blocked is not None:
        return RefineOutcome(applied=False, blocked_reason=blocked)

    # 🔴 **마스킹 불확실의 주체를 여기서 가른다.** 조립된 프롬프트 전체를 한 번에 검사하면
    #   "강사가 지시문에 실명을 썼다"와 "BE가 준 컨텍스트가 불확실하다"가 같은 결과로
    #   수렴한다 — 전자는 강사가 고칠 수 있고(200) 후자는 아무것도 못 한다(5xx).
    #   지시문만 따로 먼저 본다. **LLM 호출보다 앞이라 원가가 0이다**(06 §3·§5의 사전 정적
    #   검사와 같은 자리). 여기를 통과했는데 아래에서 걸리면 그건 컨텍스트 쪽이다.
    if redact(instruction).uncertain:
        return RefineOutcome(applied=False, blocked_reason=BlockedReason.PII_EXPOSURE)

    max_chars = max_chars_for(context)
    last_reason = ""
    # 🔴 **재생성 N회 = 시도 N+1회.** 초안 경로(`graph.py`)와 **같은 `_REGEN_MAX`를 받으므로
    # 해석도 같아야 한다** — 종전 `range(regen_max)`는 여기만 시도 3회(재생성 2회)라
    # 초안은 3회 재생성하고 다듬기는 2회였다. 위 docstring이 "같은 상한 ≤3 안에서만"이라고
    # 선언해 놓고 지키지 않던 자리다(#110이 초안 쪽만 고쳤다).
    for _ in range(regen_max + 1):
        try:
            text = await writer.write(
                context=context,
                execution_context=execution_context,
                # 🔴 최초 생성과 **같은 인자**다(`graph.py:201`) — 문면이 갈리면
                #    "같은 강조점인데 턴마다 다른 글"이 된다.
                emphasis=emphasis,
                gate_feedback=instruction_for(last_reason),
                refine_instruction=instruction,
                previous_text=previous_text,
            )
        except RedactionBlockedError as exc:  # fail-closed — 미전송(불변식 3)
            # 🔴 여기 도달했다는 건 **컨텍스트 쪽**이 불확실하다는 뜻이다 — 지시문은 위에서
            # 이미 걸렀다. 강사는 아무것도 못 바꾼다(지시를 백 번 고쳐도 같은 화면) ⇒ 실패다.
            # ⚠ `except` 절 순서가 계약이다 — `RedactionBlockedError`도 `LlmError` 하위라
            # 아래보다 먼저 와야 한다.
            raise RedactionUncertain("컨텍스트 마스킹 불확실 — 전송하지 않았다") from exc
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
