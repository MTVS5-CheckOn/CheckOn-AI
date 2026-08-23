"""인용 실존 게이트 — 🔴 **인용이 이력에 없으면 제안 자체를 버린다**(04 §3.7 · 불변식 2).

⚠ 🔴 **`counsel/grounding.py` 를 복제하지 않았다 — 재사용도 못 했다.** 성질은 형제인데
입력이 다르다:

    counsel `ground_emphasis`   `Mapping[student_ref, list[str]]` · **자유 텍스트에서**
                                `record_id=...` 를 정규식으로 뽑고 **id 실존만** 본다.
                                `DraftContext.cited_record_ids()` 에 묶여 있다.
    labels (여기)               학부모 **한 명** · 인용이 **구조화**돼 있고
                                🔴 **id 실존 + 인용문이 그 레코드 본문에 실제로 있는가**
                                **둘 다** 본다.

🔴 **id 만 보면 «있는 레코드를 가리키며 없는 말을 지어내는» 것이 통과한다** — counsel 은
자유 텍스트라 그 검사를 할 자리가 없었고, 여기는 구조화라 할 수 있다. ⇒ **더 센 게이트다.**
⚠ 공통 부분을 뽑아 합치지 않았다 — 지금 겹치는 것은 «실존을 본다» 는 **성질**뿐이고
자료구조가 달라 합치면 양쪽에 안 맞는 추상이 생긴다(#02 는 «같은 사실이 두 곳» 이지
«비슷한 일을 하는 함수가 둘» 이 아니다).

**위반은 예외가 아니라 드롭이다**(불변식 4) — 한 제안이 죽어도 나머지는 돌려준다.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from ai.contracts.labels import HistoryItem, SuggestedLabel

logger = logging.getLogger(__name__)

#: 드롭 사유 — **두 경우를 구분해** 기록한다. 대응이 다르다:
#: 없는 레코드를 가리키면 **날조**이고, 인용문이 안 맞으면 **변형**(요약·의역)이다.
DROP_UNKNOWN_RECORD_ID: Final = "unknown_record_id"
DROP_QUOTE_NOT_IN_RECORD: Final = "quote_not_in_record"


@dataclass(frozen=True)
class GroundingOutcome:
    """통과분과 드롭 사유 — 순수 데이터. 🔴 **조용한 드롭 금지.**"""

    suggestions: tuple[SuggestedLabel, ...]
    drops: tuple[tuple[str, str], ...]
    """(suggestion_id, 사유) 목록 — 등장 순서."""


def _record_text(history: Iterable[HistoryItem]) -> Mapping[str, str]:
    return {item.record_id: item.text for item in history}


def ground_suggestions(
    suggestions: Sequence[SuggestedLabel],
    *,
    history: Sequence[HistoryItem],
) -> GroundingOutcome:
    """인용이 **실존하는** 제안만 남긴다(결정론 · 순수 함수).

    🔴 **인용 하나라도 실패하면 그 제안을 통째로 버린다** — 절반만 남기면 남은 인용이
    «검증된 것처럼» 보이는데, 실제로는 그 제안을 낸 근거가 이미 무너졌다.
    ⚠ 전량 드롭이면 `suggestions=()` 이고 **그건 정상 200 이다**(04 §3.7).
    """
    texts = _record_text(history)
    kept: list[SuggestedLabel] = []
    drops: list[tuple[str, str]] = []
    for suggestion in suggestions:
        reason: str | None = None
        for quote in suggestion.evidence_quotes:
            source = texts.get(quote.record_id)
            if source is None:
                reason = DROP_UNKNOWN_RECORD_ID
                break
            if quote.quote not in source:
                reason = DROP_QUOTE_NOT_IN_RECORD
                break
        if reason is None:
            kept.append(suggestion)
        else:
            drops.append((str(suggestion.suggestion_id), reason))
    for suggestion_id, reason in drops:
        #: ⚠ 🔴 **인용 본문을 로그에 싣지 않는다**(불변식 3 · 99 #80) — id 와 사유까지다.
        logger.info("라벨 제안 드롭 suggestion=%s reason=%s", suggestion_id, reason)
    return GroundingOutcome(suggestions=tuple(kept), drops=tuple(drops))


__all__ = [
    "DROP_QUOTE_NOT_IN_RECORD",
    "DROP_UNKNOWN_RECORD_ID",
    "GroundingOutcome",
    "ground_suggestions",
]
