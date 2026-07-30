"""강조점 근거 실존 검증 — 불변식 ①의 구현(순수 함수).

plan(LLM)이 낸 강조점은 **근거 record_id를 동반해야 하고, 그 record_id가 컨텍스트에 실존해야
한다.** 기존 검증은 `record_id=[\\w-]+` **문자열 존재 검사**뿐이라 날조(`record_id=fake_1`)가
그대로 통과했다.

**위반은 예외가 아니라 드롭이다**(불변식 4 — 게이트 거부는 에러가 아니다). LLM 불량 출력
하나가 state ValidationError로 잡 전체를 죽이면, 정상 학생 21명의 초안까지 사라진다.
전량 드롭이면 **강조점 없이 진행**한다 — plan은 부가정보다.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from ai.contracts.composition import DraftContext

logger = logging.getLogger(__name__)

#: 강조점에서 근거 표기를 뽑는다 — `record_id=le_1029` 형태(state의 패턴과 같은 어휘).
_RECORD_ID_RE: Final = re.compile(r"record_id=([\w-]+)")

#: 드롭 사유 — 두 경우를 **구분해** 기록한다. "ID 없는 인용"은 plan 프롬프트 문제이고,
#: "없는 ID 인용"은 LLM 날조라 대응이 다르다.
DROP_MISSING_RECORD_ID: Final = "missing_record_id"
DROP_UNKNOWN_RECORD_ID: Final = "unknown_record_id"


@dataclass(frozen=True)
class GroundingOutcome:
    """검증 결과 — 통과분과 드롭 사유. 순수 데이터."""

    emphasis_points: dict[str, list[str]]
    drops: tuple[tuple[str, str], ...]
    """(student_ref, 사유) 목록 — 등장 순서. 조용한 드롭 금지."""


def ground_emphasis(
    planned: Mapping[str, list[str]],
    *,
    contexts: Mapping[str, DraftContext],
) -> GroundingOutcome:
    """plan 산출을 컨텍스트 보유 record_id와 대조해 통과분만 남긴다(결정론).

    학생이 컨텍스트에 없거나 인용 ID가 실존하지 않으면 그 강조점만 드롭한다 — 다른 학생·
    다른 강조점은 살린다(불변식 ③ "1명 실패가 루프를 멈추지 않는다"와 같은 결).
    """
    kept: dict[str, list[str]] = {}
    drops: list[tuple[str, str]] = []
    for student_ref in sorted(planned):
        context = contexts.get(student_ref)
        allowed = context.cited_record_ids() if context is not None else frozenset()
        survivors: list[str] = []
        for point in planned[student_ref]:
            cited = _RECORD_ID_RE.findall(point)
            if not cited:
                drops.append((student_ref, DROP_MISSING_RECORD_ID))
                continue
            if not set(cited) <= allowed:
                drops.append((student_ref, DROP_UNKNOWN_RECORD_ID))
                continue
            survivors.append(point)
        if survivors:
            kept[student_ref] = survivors
    for student_ref, reason in drops:
        logger.info("강조점 드롭 student=%s reason=%s", student_ref, reason)
    return GroundingOutcome(emphasis_points=kept, drops=tuple(drops))


__all__ = [
    "DROP_MISSING_RECORD_ID",
    "DROP_UNKNOWN_RECORD_ID",
    "GroundingOutcome",
    "ground_emphasis",
]
