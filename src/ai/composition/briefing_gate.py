"""브리핑 왜곡 게이트 — 결정론(LLM 금지). masking_redaction·05 §4·report numbers_used 선례.

LLM이 만든 브리핑 한 줄이 [표시]로 나가기 전 통과해야 하는 정적 검사. 게이트는 절대
LLM을 쓰지 않는다(불변식 1). 실패 시 재생성(≤3) 또는 템플릿 폴백은 호출자(briefing.py)가 한다.

검사 순서:
① ⟪⟫ 토큰 잔존 → 실패(분기표 #4 — brief는 [표시] 직행이라 토큰 노출 금지)
② 기호 잔존 → 실패. LaTeX·마크다운 메타문자(달러·역슬래시·백틱·별표·우물정·밑줄·물결·
   캐럿·중괄호)는 한글 브리핑에 정당하게 쓰일 일이 없다. v1 실 LLM 프리뷰에서 LaTeX
   유출을 관찰 — 프롬프트가 1차로 막고 게이트는 백스톱이다. 곱하기 '×'(U+00D7)는 허용.
③ 금칙어(05 §4 A군) → 실패. 🔴 **판정을 `buffer_lexicon.find_forbidden`에 위임한다**
   (8/5 · 99 D ⑰ 해소). 어휘 단일 참조(99 #15 ⓒ)에 이어 **판정도 단일 참조**다 — 종전에는
   여기 자기 루프가 따로 있어 "`find_forbidden`을 고쳐도 브리핑 게이트는 안 고쳐지는"
   상태였고, 그게 ⑰ 사고의 구조적 원인이었다. 이제 활용형(`게으른`·**`산만합니다`**)도
   잡는다. 두 게이트의 A군 판정이 동일하다는 것은 테스트가 고정한다.
④ 숫자·기간 EXACT 대조 — 문장 속 모든 숫자가 입력 수치 집합에 실존해야(report §3 numbers_used
   선례, 변환·반올림 불허). v2는 BriefingContext가 제공한 수치만 allowed(파생 표기 포함,
   제공 안 한 환산값 불허) — LLM이 근거 밖 숫자를 만들면 차단된다.
⑤ 한 줄 길이 상한 초과 → 실패
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

from ai.composition.buffer_lexicon import find_forbidden, forbidden_terms

_NUMBER_RE = re.compile(r"\d+")

#: LaTeX·마크다운 메타문자 — 한글 브리핑엔 안 나오는 게 정상(× U+00D7은 정상 문자라 제외).
_SYMBOL_RE = re.compile(r"[$\\`*#_~^{}]")

#: 브리핑 한 줄 길이 상한(자). 카드 한 줄에 들어가는 구조적 제약 — 정책 임계 아님.
MAX_BRIEF_LENGTH = 80


class GateResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    passed: bool
    reason: str = ""


def _forbidden() -> tuple[str, ...]:
    """금칙어 A군 — `buffer_lexicon.yaml` 단일 참조(99 #15 ⓒ).

    종전에는 `briefing_forbidden.yaml` 복사본을 읽었다. 목록이 두 곳에서 따로 늙는 것을
    막으려고 D-③에서 정본 한 곳으로 합쳤다 — 값·검출 방식은 그대로다(동작 무변경).
    캐시는 `load_buffer_lexicon`의 lru_cache가 담당한다.
    """
    return forbidden_terms()


def check_brief_gate(
    text: str,
    allowed_numbers: frozenset[str],
    *,
    max_length: int = MAX_BRIEF_LENGTH,
) -> GateResult:
    """브리핑 한 줄의 왜곡 검사. 통과분만 [표시]로 나간다."""
    if "⟪" in text or "⟫" in text:
        return GateResult(passed=False, reason="token_leak")
    symbol = _SYMBOL_RE.search(text)
    if symbol is not None:
        return GateResult(passed=False, reason=f"symbol:{symbol.group()}")
    # A군 판정은 `buffer_lexicon.find_forbidden` **단일 정본**에 위임한다(99 D ⑰ 해소).
    # 종전에는 여기 자기 루프(`word in text`)가 따로 있어 어휘만 단일화되고 판정은
    # 갈려 있었다 — 그게 ⑰ 사고의 구조적 원인이다. 등록 순서 첫 히트를 쓰는 것은
    # 종전 루프와 동일하므로 사유 문자열도 그대로다.
    hits = find_forbidden(text, _forbidden())
    if hits:
        return GateResult(passed=False, reason=f"forbidden:{hits[0]}")
    for number in _NUMBER_RE.findall(text):
        if number not in allowed_numbers:
            return GateResult(passed=False, reason=f"number_not_grounded:{number}")
    if len(text) > max_length:
        return GateResult(passed=False, reason="too_long")
    return GateResult(passed=True)
