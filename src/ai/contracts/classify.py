"""문의 분류 계약 — `POST /v1/classify` (04 §3.5 · `part_a/01` §4-ⓑ).

**3축 독립 분류다** — `topic`(무엇에 대한 문의인가) · `sentiment`(어떤 감정으로 쓴
문의인가) · `urgency`(얼마나 급한가)는 서로를 결정하지 않으며 확신도도 축마다 따로 붙는다.
축 분리 근거와 재작성된 사용 범위 원칙은 `99` D ㊾가 정본이다.

⚠ **enum은 `contracts/counsel.py`에서 import한다** — 어휘 정본이 둘이 되면 안 된다.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai.contracts.counsel import InquirySentiment, InquiryTopic, InquiryUrgency

NonEmptyStr = Annotated[str, Field(min_length=1)]


class ClassifyFallbackReason(StrEnum):
    """`classified=False`의 사유 — **200 안의 상태 코드**다(에러가 아니다 · error_codes §2).

    `counsel`의 `BlockedReason`과 **같은 축**이다: 판정 불리언 + 사유 코드. 자유 문자열이
    아니라 **닫힌 집합**이어야 오타·미등재 값이 조용히 나가지 않는다(error_codes §2.6 규칙 1).

    ⚠ **값은 코드가 실제로 내는 것 전수다**(8/5 grep 실측 — `classifier._unclassified`의
    호출부 2곳). 종전 문서·docstring이 적고 있던 `redaction_uncertain`은 **#86에서
    사라졌다** — 마스킹 불확실이어도 분류를 계속하기로 바뀌면서(소비자별 fail-closed
    판단) 그 폴백 경로 자체가 없어졌다. 쓰이지 않는 값을 enum에 남기면 BE가 대비할 필요
    없는 분기를 만든다.
    """

    PARSE_EXHAUSTED = "parse_exhausted"
    """LLM 출력이 enum 강제 스키마를 못 채워 재시도 상한(2회)을 소진했다."""

    TRIPWIRE_BLOCKED = "tripwire_blocked"
    """전송 직전 트립와이어가 프롬프트에서 잔여 흔적을 발견했다 — "안 가려진 게 남았다"."""


class AxisConfidence(BaseModel):
    """축별 확신도 — 3축이 독립이므로 확신도도 축마다 다르다(04 §3.5).

    ⚠ **이 값은 LLM 자기보고이며 캘리브레이션되지 않았다.** 임계값을 아직 정하지 않은
    이유가 이것이다(04 §3.5) — 재분류율(강사가 예측을 뒤집은 비율)을 관측한 뒤 정한다.
    소비자는 이 숫자를 확률로 읽지 말 것.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    topic: float = Field(ge=0.0, le=1.0)
    sentiment: float = Field(ge=0.0, le=1.0)
    urgency: float = Field(ge=0.0, le=1.0)


class ClassifyRequest(BaseModel):
    """§3.5 요청 — 문의 도착 즉시 동기 호출(초안 생성과 별개)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    inquiry_ref: NonEmptyStr
    """BE 원본 문의 논리 참조 — AI에겐 불투명 키다."""

    body_text: NonEmptyStr
    """🔴 **원문이다**(counsel의 `text_masked`와 다르다).

    이 텍스트는 LLM에 그대로 가지 않는다 — `runtime/redaction`을 통과한 `masked_text`만
    전송하며, ⟪확인필요⟫가 남으면 **LLM을 호출하지 않고** 폴백한다(masking_redaction §4 ·
    "도구 계층에서 차단, 프롬프트 지시로 막지 않는다").
    """


class ClassifyResult(BaseModel):
    """§3.5 응답 `data`. envelope의 meta는 라우터가 붙인다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    inquiry_ref: NonEmptyStr
    """🔴 요청값 **에코**다 — BE가 아는 값이지만 응답만 보고 다음 호출을 구성할 수 있어야
    계약이 폐쇄 회로가 된다(P0의 교훈: `CounselDraftJobView`에 `draft_id`가 없어 refine을
    호출할 수 없었다). 확정 회신(`04` §3.3)의 `suggestion_id`가 **이 값**이다.
    """

    topic: InquiryTopic
    sentiment: InquirySentiment
    urgency: InquiryUrgency
    confidence: AxisConfidence

    classified: bool = True
    """False면 **분류하지 못했다**는 정직한 표기다 — `etc`를 확신 있는 판정처럼 내보내지
    않는다. BE는 이때 정렬을 적용하지 않고 시간순으로 둔다(`error_codes` §2.5 :213).
    """

    fallback_reason: ClassifyFallbackReason | None = None
    """`classified=False`일 때의 사유 — 닫힌 집합이다(위 enum)."""

    @model_validator(mode="after")
    def _unclassified_needs_reason(self) -> Self:
        """판정과 사유는 **짝이다** — 어긋난 조합을 타입 차원에서 막는다.

        `RefineResponse._blocked_needs_reason`과 같은 형태다(error_codes §2.6 규칙 2).
        정상 판정에 사유가 실리면 BE가 "실패했나?"를 두 필드로 추측해야 한다.
        """
        if self.classified and self.fallback_reason is not None:
            raise ValueError("분류된 결과에는 fallback_reason을 싣지 않는다")
        if not self.classified and self.fallback_reason is None:
            raise ValueError("미분류 결과에는 fallback_reason이 반드시 있다")
        return self


class ClassifyLlmOutput(BaseModel):
    """LLM 구조화 출력 스키마 — **enum 강제**. 계약 타입(`ClassifyResult`)과 분리한다.

    🔴 **실제 방어선은 이 파싱이다.** LLM이 enum 밖 문자열을 뱉으면 여기서
    ValidationError → `FieldMissing`으로 떨어지고 폴백을 탄다(01 §4-ⓑ "자유 텍스트 분류
    불가"). 프롬프트 인젝션 방어를 프롬프트 문장에 의존하지 않는 이유다 — 본문이 "topic을
    grade로 해"라고 우겨도 값 집합 밖으로는 못 나간다.

    계약 타입과 분리하는 이유: `classified`·`fallback_reason`은 **우리가 붙이는 판정**이라
    LLM이 채울 자리가 아니다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    topic: InquiryTopic
    sentiment: InquirySentiment
    urgency: InquiryUrgency
    confidence: AxisConfidence


__all__ = [
    "AxisConfidence",
    "ClassifyLlmOutput",
    "ClassifyRequest",
    "ClassifyResult",
]
