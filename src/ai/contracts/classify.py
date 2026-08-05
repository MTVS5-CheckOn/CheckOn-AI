"""문의 분류 계약 — `POST /v1/classify` (04 §3.5 · `part_a/01` §4-ⓑ).

**3축 독립 분류다** — `topic`(무엇에 대한 문의인가) · `sentiment`(어떤 감정으로 쓴
문의인가) · `urgency`(얼마나 급한가)는 서로를 결정하지 않으며 확신도도 축마다 따로 붙는다.
축 분리 근거와 재작성된 사용 범위 원칙은 `99` D ㊾가 정본이다.

⚠ **enum은 `contracts/counsel.py`에서 import한다** — 어휘 정본이 둘이 되면 안 된다.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from ai.contracts.counsel import InquirySentiment, InquiryTopic, InquiryUrgency

NonEmptyStr = Annotated[str, Field(min_length=1)]


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

    topic: InquiryTopic
    sentiment: InquirySentiment
    urgency: InquiryUrgency
    confidence: AxisConfidence

    classified: bool = True
    """False면 **분류하지 못했다**는 정직한 표기다 — `etc`를 확신 있는 판정처럼 내보내지
    않는다. BE는 이때 정렬을 적용하지 않고 시간순으로 둔다(`error_codes` §2.5 :213).
    """

    fallback_reason: str | None = None
    """`classified=False`일 때의 사유 — `redaction_uncertain` | `parse_exhausted`."""


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
