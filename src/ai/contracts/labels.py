"""라벨 제안 계약 — `POST /v1/labels/suggest` (04 §3.7).

🔴 **한 요청에 학부모 한 명 · 동기 200**(2026-08-22 판정 · 99 #190). 종전 계약은
`guardians[]` 배열 + 202 였는데 그건 **「주간 배치」 시절의 형태**다 — 트리거를 「강사 요청」
으로 바꾸면서(8/21) 형태를 같이 안 봤다.

⚠ 🔴 **이 경로는 아무것도 저장하지 않는다.** 동기로 계산해 돌려주고 끝난다 — 테이블·캐시·
원장 어디에도 안 쓴다(04 §3.7 저장 정책 · `db/models.py` 의 `label_suggestion` 은 v1 미사용).
🔴 그래서 **워커·드레인·`WorkerKind`·마이그레이션이 하나도 안 필요하다.**

⚠ **`LabelSuggestion`(axis·value)은 `contracts/counsel.py` 것을 재사용한다** — 4축 값의 정의가
두 곳이 되면 갈린다(#02). 여기서는 그걸 **품고** 응답 전용 필드(근거·신뢰도)를 더한다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ai.contracts.counsel import LabelSuggestion

type NonEmptyStr = Annotated[str, Field(min_length=1)]

#: 🔴 이력 하한 — 04 §3.7 이 「소통 이력 5건 이상」이라 적는다. **상한은 불변식 6**이다
#: (모든 루프에 상한). ⚠ 상한 값은 「이 정도면 되겠지」가 아니라 **프롬프트가 감당하는
#: 크기**에서 온다 — 실측 전이라 계약 예시(`05_request_json.md` §7 · 최근 10건·90일,
#: `04 §5` `comm_history[]`)와 같은 자리에 맞춘다.
MIN_HISTORY: int = 5
MAX_HISTORY: int = 10


class HistoryItem(BaseModel):
    """소통 이력 1건 — 🔴 **백엔드 1차 마스킹 통과본**이다(04 Open-4d · 불변식 3).

    ⚠ `text` 는 alias 처리된 본문이고, AI 는 이 값을 **다시 마스킹**해서 LLM 에 보낸다
    (전송 트립와이어가 마지막 관문 · `runtime/trace_masking.py`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    record_id: NonEmptyStr
    direction: NonEmptyStr
    text: NonEmptyStr
    at: datetime


class LabelSuggestRequest(BaseModel):
    """요청 — 🔴 **학부모 한 명**."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    guardian_ref: NonEmptyStr
    history: Annotated[
        tuple[HistoryItem, ...], Field(min_length=MIN_HISTORY, max_length=MAX_HISTORY)
    ]
    guardian_consent: str | None = None
    """🔴 **v1 미사용 — 자리만 예약한다**(04 §3.7 · 처리 로직 없음).

    ⚠ 필요해지는 시점: (나) 새 라벨 발굴에서 동의가 개정되면 **재동의 안 한 학부모**가
    생긴다 — 값이 실제로 갈리는 첫 순간이다. 🔴 지금 이 값을 **읽는 코드를 만들지 마라**
    (CLAUDE.md §3 의 `item_format` 선례 — 예약값에 처리 로직을 붙이지 않는다).
    """


class EvidenceQuote(BaseModel):
    """제안 근거 인용 — 🔴 **실제 이력에 실존하는 문장만**(04 §3.7).

    ⚠ `record_id` 가 이력에 있는지 **그리고** `quote` 가 그 레코드 본문에 있는지 **둘 다**
    본다 — id 만 보면 «있는 레코드를 가리키며 없는 말을 지어내는» 것이 통과한다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    record_id: NonEmptyStr
    quote: NonEmptyStr


class SuggestedLabel(BaseModel):
    """제안 1건 — 축·값 + 근거.

    🔴 **`LabelSuggestion`(axis·value)을 품는다** — `contracts/counsel.py:329` 의 그것이고,
    4축 값의 정의를 **한 곳**에 둔다. ⚠ 그쪽을 고쳐 필드를 늘리지 않았다: 그 모델은
    **counsel 산출물의 부산물**(항상 빈 배열)이고 이건 **독립 응답**이라 축이 다르다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    suggestion_id: UUID
    guardian_ref: NonEmptyStr
    label: LabelSuggestion
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    evidence_quotes: Annotated[tuple[EvidenceQuote, ...], Field(min_length=1)]
    """🔴 **비면 제안이 성립하지 않는다**(불변식 2 — evidence 없는 산출물 금지).
    타입이 막는다 — 런타임 분기로 지키면 언젠가 새어 나간다."""


class LabelSuggestResponse(BaseModel):
    """응답 — 게이트 통과분만.

    ⚠ 🔴 **빈 배열이 정상이다** — 인용 실존 게이트가 전량 드롭하면 `suggestions=()` 다.
    그건 실패가 아니라 «제안할 근거가 없다» 이고, 04 §3.7 이 «인용이 이력에 없으면 제안
    자체가 폐기» 라고 적은 그 결과다(불변식 4 — 게이트 거부는 에러가 아니다).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    suggestions: tuple[SuggestedLabel, ...] = ()


__all__ = [
    "MAX_HISTORY",
    "MIN_HISTORY",
    "EvidenceQuote",
    "HistoryItem",
    "LabelSuggestRequest",
    "LabelSuggestResponse",
    "SuggestedLabel",
]
