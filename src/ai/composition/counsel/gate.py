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

from ai.composition.buffer_lexicon import (
    find_forbidden,
    forbidden_terms,
    replacement_probes,
)
from ai.contracts.composition import DraftContext, extract_numbers
from ai.runtime.internal_terms import find_internal_terms

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
    min_chars: int,
) -> GateResult:
    """**초안 본문 전체**를 판정한다. 순수 함수(계산·I/O 분리, 03 §2).

    ⚠ 종전 서술은 *"초안 블록 하나를 판정한다"* 였는데 **사실이 아니었다** — 호출부
    (`graph.py`·`refine.py`)는 처음부터 본문 전체를 넘긴다. 그 거짓 서술 때문에 상한이
    블록 하나분으로 계산되는 것이 오래 안 보였다(99 ㉤). 블록 단위 판정은 별건이다(**#31**).
    ⚠ **(8/9 정정) 종전에는 `(㊱)`을 가리켰다** — 번호는 맞았고 ㊱이 **묶인 등재**였다.
    표제는 닫혔고 이 꼬리가 #31로 갈라졌다(99 #15).

    검사 순서는 고정이라 같은 입력에 같은 사유가 나온다(결정론).
    ⚠ `internal_term`은 **맨 뒤에 붙였다** — 기존 검사 사이에 끼우면 종전에 다른 사유로
    막히던 본문의 사유 코드가 바뀐다(순서가 곧 계약이다).
    `max_chars`·`min_chars`는 호출자가 tone_map에서 산출해 주입한다(`max_chars_for`·
    `min_chars_for`) — 임계값을 이 모듈에 박지 않는다(03 §1).

    🔴 **`min_chars`에 기본값을 두지 않았다**(99 #13). `= 0`으로 두면 새 호출자가
    **조용히 하한 없이** 게이트를 통과시킨다 — 그게 이 안건이 고치는 결함(*"선언은 있고
    배선이 없다"*)과 정확히 같은 형태다. `max_chars`와 **대칭으로 필수 키워드**라
    호출부마다 값을 정하게 강제한다. ⚠ 길이 축과 무관한 테스트는 `min_chars=0`을
    **명시**한다 — 숨은 옵트아웃이 아니라 보이는 선언이다.
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

    #: 🔴 **B군 — 「이 말 대신 저 말」**(99 #79 · 05 §4). A군 **바로 뒤**다:
    #:   같은 어휘 축이고 **A군이 더 강하다**(금칙 > 완충). ⚠ `ungrounded_number` **앞**이어야
    #:   한다 — 숫자 검사가 더 치명적인데 B군이 먼저 걸리면 **숫자 문제가 가려진다.**
    #:
    #: 🔴 **두 달 열려 있던 이유는 「재생성이 터지나」였고, 실측이 답했다** —
    #:   실 LLM 57건에서 적중 **0건**(95% 상한 5.3% · 99 #79·#80). ⇒ 넣어도 안 터진다.
    #: ⚠ **그 상한은 「이 입력 분포에서」다** — 실 문의가 다양해지면 다시 재야 한다.
    #:
    #: 🔴 **그리고 이 층이 유일한 방어인 항이 둘 있다** — `이해력이 부족`·`다른 학생에 비해`는
    #:   `redact()` 오탐 때문에 **프롬프트에 못 싣는다**(99 #83) ⇒ 종전에는 **어디서도 안
    #:   막혔다.** 그게 이 검사의 실제 값이다.
    #:
    #: ⚠ **관측과 같은 목록을 본다**(`replacement_probes()`) — 두 층이 갈리면
    #:   *"관측엔 안 잡히는데 게이트에 걸린다"* 가 나고 강사가 원인을 볼 수 없다.
    buffered = find_forbidden(body, replacement_probes())
    if buffered:
        #: 🔴 **detail(콜론 뒤)을 안 싣는다 — 이 자리만 그렇다.** 다른 사유는 `forbidden:게으르`
        #: 처럼 걸린 어휘를 붙이고 `gate_feedback` 의 `detail_suffix` 가 그걸 **재생성
        #: 프롬프트에 실어** 준다. 🔴 **B군은 그러면 안 된다** — 실측(8/20 · 28항 전수):
        #: `이해력이 부족`·`다른 학생에 비해` **2항**이 detail 로 붙는 순간 그 프롬프트가
        #: `redact()` 에 걸려 **fail-closed 로 미전송**된다(99 #83 이 실측한 그 둘이다).
        #: ⇒ 그러면 2회차가 통째로 죽는다 — **99 #102 와 정확히 같은 사고**다.
        #: ⚠ 대가: 강사·로그가 «어느 표현이었나» 를 사유에서 못 읽는다. 그건 **관측**이
        #:   답한다(`observe_gated_draft` 가 같은 목록으로 세고 어휘를 로그에 남긴다) —
        #:   재생성 프롬프트에 싣는 것과 **읽는 자리가 다르다.**
        del buffered  # 사유에 안 싣는다(위) — 매칭 여부만 쓴다
        return GateResult(passed=False, reason="buffered")

    leaked = find_internal_terms(body)
    if leaked:
        # 🔴 지시문·산출물 이름 누출 — 3차 실측 첫 문장이 "…상담 초안을 드립니다."였다.
        # 프롬프트에 "쓰지 마라"를 적는 걸로는 못 막는다(금칙어에서 겪은 그대로) — 확률적
        # 되뇜의 실제 방어선은 게이트다. 어휘 정본은 `internal_terms.yaml`.
        return GateResult(passed=False, reason=f"internal_term:{leaked[0]}")

    allowed = context.allowed_numbers()
    # 🔴 추출은 허용집합과 **같은 함수**여야 한다 — 한쪽만 정규화하면 표기 방향이
    # 반대일 때 그대로 뚫린다(`1,240` ↔ `1240`). 검사 순서·사유 코드는 그대로다.
    ungrounded = sorted(extract_numbers(body) - allowed)
    if ungrounded:  # 근거에 없는 수치 — 불변식 1·2(LLM이 수치를 만들지 않는다)
        return GateResult(passed=False, reason=f"ungrounded_number:{ungrounded[0]}")

    # 🔴 **하한은 맨 뒤다**(99 #13). 종전에는 규칙 일곱이 전부 「있으면 안 되는 것」이라
    #    `passed=True`가 **떨어져 나오는 값**이었다 — `"네."` 두 글자가 통과했다.
    #
    # ⚠ **`empty`(:56) 바로 뒤가 자연스러워 보이지만 그 자리를 안 골랐다.** 길이 검사
    #    둘을 나란히 두면 읽기는 좋은데, **종전에 다른 사유로 막히던 짧은 본문의 사유
    #    코드가 바뀐다** — 예: 마스킹 토큰이 남은 20자 본문은 지금 `token_leak`인데
    #    앞에 끼우면 `too_short`가 된다. 이 파일 머리말이 *"순서가 곧 계약이다"* 를 적어
    #    뒀고 `internal_term`이 같은 이유로 맨 뒤에 붙은 선례다.
    #    🔴 **자연스러움보다 사유 코드 안정이 먼저다.**
    #
    # ⚠ 사유 코드는 `too_long`과 **대칭**이다(`too_short:{len}<{min}`) — 접두가 갈리면
    #    `error_codes` §2.1 매핑이 갈린다.
    if len(body) < min_chars:
        return GateResult(passed=False, reason=f"too_short:{len(body)}<{min_chars}")

    return GateResult(passed=True)


__all__ = ["GateResult", "check_counsel_gate"]
