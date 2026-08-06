"""상담 초안 왜곡 게이트 — **결정론**(LLM 금지).

`briefing_gate.py`의 원칙을 그대로 따른다: 숫자 EXACT 대조 · 금칙어 · 길이 · 기호·토큰.
불변식 1(CLAUDE.md): 판정은 결정론 코드가 한다 — 게이트에 LLM을 쓰지 않는다.

**금칙어 목록을 새로 만들지 않는다** — `buffer_lexicon.yaml`의 A군을 단일 참조한다
(99 #15 ⓒ 단일화. `briefing_gate`도 같은 목록을 읽는다).
"""

from __future__ import annotations

import re
from typing import Final

from pydantic import BaseModel, ConfigDict

from ai.composition.buffer_lexicon import find_forbidden, forbidden_terms
from ai.contracts.composition import DraftContext

_NUMBER_RE: Final = re.compile(r"\d+")

#: LaTeX·마크다운 메타문자 — 한글 상담 초안엔 안 나오는 게 정상(× U+00D7은 정상 문자).
_SYMBOL_RE: Final = re.compile(r"[$\\`*#_~^{}]")

#: 마스킹 토큰이 초안에 남으면 게이트 실패(masking_redaction §1).
_MASK_TOKEN_RE: Final = re.compile(r"[⟪⟫]")


class GateResult(BaseModel):
    """게이트 판정 — 거부는 예외가 아니라 반환값이다(불변식 4)."""

    model_config = ConfigDict(frozen=True)

    passed: bool
    reason: str = ""


def check_counsel_gate(
    text: str,
    context: DraftContext,
    *,
    max_chars: int,
) -> GateResult:
    """**초안 본문 전체**를 판정한다. 순수 함수(계산·I/O 분리, 03 §2).

    ⚠ 종전 서술은 *"초안 블록 하나를 판정한다"* 였는데 **사실이 아니었다** — 호출부
    (`graph.py`·`refine.py`)는 처음부터 본문 전체를 넘긴다. 그 거짓 서술 때문에 상한이
    블록 하나분으로 계산되는 것이 오래 안 보였다(99 ㉤). 블록 단위 판정은 별건이다(㊱).

    검사 순서는 고정이라 같은 입력에 같은 사유가 나온다(결정론).
    `max_chars`는 호출자가 tone_map에서 산출해 주입한다(`max_chars_for` — 블록 수 ×
    블록당 문장 수 × 문장당 글자) — 임계값을 이 모듈에 박지 않는다(03 §1).
    """
    body = text.strip()
    if not body:
        return GateResult(passed=False, reason="empty")
    if len(body) > max_chars:
        return GateResult(passed=False, reason=f"too_long:{len(body)}>{max_chars}")
    if _MASK_TOKEN_RE.search(body):
        return GateResult(passed=False, reason="token_leak")
    if _SYMBOL_RE.search(body):
        return GateResult(passed=False, reason="symbol")

    hits = find_forbidden(body, forbidden_terms())
    if hits:  # A군 금칙 — 치환 불가, 블록 재생성(05 §4)
        return GateResult(passed=False, reason=f"forbidden:{hits[0]}")

    allowed = context.allowed_numbers()
    ungrounded = sorted(set(_NUMBER_RE.findall(body)) - allowed)
    if ungrounded:  # 근거에 없는 수치 — 불변식 1·2(LLM이 수치를 만들지 않는다)
        return GateResult(passed=False, reason=f"ungrounded_number:{ungrounded[0]}")

    return GateResult(passed=True)


__all__ = ["GateResult", "check_counsel_gate"]
