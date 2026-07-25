"""브리핑 왜곡 게이트 — 결정론(LLM 금지). masking_redaction·05 §4·report numbers_used 선례.

LLM이 만든 브리핑 한 줄이 [표시]로 나가기 전 통과해야 하는 정적 검사. 게이트는 절대
LLM을 쓰지 않는다(불변식 1). 실패 시 재생성(≤3) 또는 템플릿 폴백은 호출자(briefing.py)가 한다.

검사 순서:
① ⟪⟫ 토큰 잔존 → 실패(분기표 #4 — brief는 [표시] 직행이라 토큰 노출 금지)
② 금칙어(05 §4 A군 복사본) → 실패. **한계:** 목록이 어간 기반(게으르·산만하)이라
   substring으로는 활용형(게으른·산만한)을 놓칠 수 있다 — 프롬프트가 1차로 막고 게이트는
   백스톱이며, 활용 커버는 D-③ buffer_lexicon 단일화 때 함께(99 15-ⓒ).
③ 숫자·기간 EXACT 대조 — 문장 속 모든 숫자가 입력 수치 집합에 실존해야(report §3 numbers_used
   선례, 변환·반올림 불허). v0는 구조화 수치 미노출이라 allowed가 비어 fabrication을 전부 차단.
④ 한 줄 길이 상한 초과 → 실패
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict

_FORBIDDEN_PATH = Path(__file__).parent / "briefing_forbidden.yaml"
_NUMBER_RE = re.compile(r"\d+")

#: 브리핑 한 줄 길이 상한(자). 카드 한 줄에 들어가는 구조적 제약 — 정책 임계 아님.
MAX_BRIEF_LENGTH = 80


class GateResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    passed: bool
    reason: str = ""


@lru_cache
def _forbidden() -> tuple[str, ...]:
    raw = yaml.safe_load(_FORBIDDEN_PATH.read_text(encoding="utf-8"))
    return tuple(str(word) for word in raw["forbidden"])


def check_brief_gate(
    text: str,
    allowed_numbers: frozenset[str],
    *,
    max_length: int = MAX_BRIEF_LENGTH,
) -> GateResult:
    """브리핑 한 줄의 왜곡 검사. 통과분만 [표시]로 나간다."""
    if "⟪" in text or "⟫" in text:
        return GateResult(passed=False, reason="token_leak")
    for word in _forbidden():
        if word in text:
            return GateResult(passed=False, reason=f"forbidden:{word}")
    for number in _NUMBER_RE.findall(text):
        if number not in allowed_numbers:
            return GateResult(passed=False, reason=f"number_not_grounded:{number}")
    if len(text) > max_length:
        return GateResult(passed=False, reason="too_long")
    return GateResult(passed=True)
