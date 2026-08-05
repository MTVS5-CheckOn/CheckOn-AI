"""확정 회신 계약 — `POST /v1/confirmations` (04 §3.3).

AI 제안(태그·라벨·분류·초안 수정)에 대한 **강사 확정/정정을 되돌려 받는** 표면이다.
`kind`가 어느 제안 축인지 가르고, `suggestion_id`가 그 축의 참조 키다.

🔴 **`suggestion_id`는 kind마다 다른 네임스페이스의 키다** — 단일 UUID 공간이 아니다.

    tag            → `TAG_SUGGESTION.id`
    label          → `LABEL_SUGGESTION.id`
    classification → **`inquiry_ref`**(문의 논리 참조 — 새 UUID를 노출하지 않는다)
    draft_edit     → `job_id`(04 §3.9)

분류가 `inquiry_ref`인 이유: 문의 1건 = 분류 1건이고 **BE가 이미 갖고 있는 값**이라
왕복이 없다. 응답 스키마에 UUID를 새로 늘리면 BE가 관리할 식별자만 는다 —
`04` §3.9의 refine이 `job_id`로 통일된 것과 같은 판단이다.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from ai.contracts.counsel import InquirySentiment, InquiryTopic, InquiryUrgency

NonEmptyStr = Annotated[str, Field(min_length=1)]


class ConfirmationKind(StrEnum):
    """확정 대상 축 — 04 §3.3 [확정 enum].

    ⚠ **v1이 실제로 처리하는 것은 `CLASSIFICATION` 하나다.** 나머지 셋은 제안 생성기가
    없어(`TAG_SUGGESTION`·`LABEL_SUGGESTION` 적재 0건 · `label_suggestions`는 v1 상수
    `[]`) **확정할 대상이 존재하지 않는다.** 받아서 조용히 버리면 BE가 "저장됐다"고
    오해하므로 400으로 정직하게 거절한다(불변식 4의 반대 방향 — 안 한 일을 한 척하지
    않는다).
    """

    TAG = "tag"
    LABEL = "label"
    CLASSIFICATION = "classification"
    DRAFT_EDIT = "draft_edit"


class ConfirmationAction(StrEnum):
    """확정 행위 — 04 §3.3 [확정 enum].

    ⚠ **`REJECTED`는 `classification`에서 받지 않는다.** 3축은 값이 반드시 있어야 하는
    축이라 "거절"이 정의되지 않는다 — tag·label은 "이 제안을 안 쓴다"가 성립하지만
    분류는 아니다. 라우터가 400으로 막는다.
    """

    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    CORRECTED = "corrected"


class ClassificationCorrection(BaseModel):
    """`kind=classification`의 `corrected_value` — **3축 전부 nullable**.

    축이 독립이므로 **부분 정정**이 성립한다(topic만 고치고 나머지는 그대로).
    빈 축은 "그 축은 안 바꿈"이고, 저장 층에서 `corrected_*` NULL로 남는다.

    🔴 **예측과 같은 값을 보내도 정정으로 세지 않는다**(P2-b 기록 규약 ①) — 저장 층이
    예측과 대조해 다른 축만 남긴다. 강사가 드롭다운을 열어 같은 값을 다시 골라도
    재분류율이 부풀려지지 않게 하는 장치다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    topic: InquiryTopic | None = None
    sentiment: InquirySentiment | None = None
    urgency: InquiryUrgency | None = None

    def as_axis_map(self) -> dict[str, str]:
        """축 → 값. 비어 있는 축은 뺀다."""
        return {
            axis: value.value
            for axis, value in (
                ("topic", self.topic),
                ("sentiment", self.sentiment),
                ("urgency", self.urgency),
            )
            if value is not None
        }


class ConfirmationRequest(BaseModel):
    """§3.3 요청."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ConfirmationKind
    suggestion_id: NonEmptyStr
    """kind별 참조 키 — classification은 `inquiry_ref`다(모듈 docstring)."""

    action: ConfirmationAction
    corrected_value: ClassificationCorrection | None = None
    """`action=corrected`일 때의 정정값. 그 외에는 비운다."""


class ConfirmationResponse(BaseModel):
    """§3.3 응답 `data`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    accepted: bool


__all__ = [
    "ClassificationCorrection",
    "ConfirmationAction",
    "ConfirmationKind",
    "ConfirmationRequest",
    "ConfirmationResponse",
]
