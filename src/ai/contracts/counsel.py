"""counsel 초안 API 계약 — 인박스 데이터계약 v1 §4 (2026-07-31 A 확정 통보) · 04 §3.9.

**요청 단위 = 문의 1건**이다(99 D ㉛ — 학생 묶음 pack 폐기). 워커는 기존 counsel_pack을
N=1 축퇴 사례로 재사용하므로 이 모듈은 **와이어 표현만** 정의한다 — 내부 도메인 타입은
`contracts/composition.py`가 계속 소유한다.

경계 데이터는 전부 Pydantic이며 `extra="forbid"`다(03 §2) — 계약에 없는 필드가 조용히
흘러들지 않는다. 실명·연락처 필드는 **스키마에 존재하지 않는다**(불변식 3).

`BlockedReason`은 `contracts/gates.py`(양자 승인)의 것을 **재사용**한다 — 새로 만들지 않는다.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai.contracts.composition import DraftStatus
from ai.contracts.gates import BlockedReason

type NonEmptyStr = Annotated[str, Field(min_length=1)]


# ───────────────────────── 요청 (BE → AI · §4-①) ─────────────────────────


class InquiryTopic(StrEnum):
    """문의 유형 — 계약 [확정 enum] 4종. **"무엇에 대한 문의인가"만 담는다.**

    문체(`composition/tone.py`의 4축 `tone_key`)에는 쓰지 않는다 — `part_a/05` §199에서
    `urgency`·`topic`에서 `sensitivity`를 파생하지 않기로 확정했다(기본값이 이미 최대 완충인
    `anxious`라 어느 입력에서 파생해도 값이 안 바뀐다).

    쓰이는 곳은 **데이터 무관 문의 판정**(`template_only` · `policies/error_codes` §2.1)과
    인박스 정렬이다. 분류가 초안 종류를 가르는 것이 허용되는 근거는 **강사가 되돌릴 수
    있다**는 것이며(`part_a/01` §4-ⓑ), 되돌리기 경로는 `04` §3.9가 계약으로 보장한다.

    ⚠ **(8/5) `complaint`를 뺐다** — 그건 "무엇에 대한 문의인가"가 아니라 "어떤 감정으로
    쓴 문의인가"라 `InquirySentiment` 축으로 옮겼다. 축 분리가 업계 표준이고(Zendesk는
    Intent·Sentiment를 독립 필드로 둔다), 학부모가 스스로 고를 수 없는 값이기도 하다
    (영국 FCA는 complaint를 "expression of dissatisfaction, whether justified or not"으로
    정의해 **고객의 자가 명명에 의존하지 못하게** 한다 — 본문에서 추론할 값이다).

    ⚠ **`etc`는 남긴다.** 업계 가이드는 catch-all 제거를 권하지만, 우리는 미분류를
    **low-confidence 강등**으로 처리하기로 했으므로(`04` §3.5) `etc`가 "분류 실패의 쓰레기통"이
    되지 않는다 — "정말 기타"만 남는다.
    """

    GRADE = "grade"
    SCHEDULE = "schedule"
    COUNSEL_REQUEST = "counsel_request"
    ETC = "etc"


class InquirySentiment(StrEnum):
    """문의 성향 — 계약 [확정 enum] 2종. **`POST /v1/classify` 응답 전용**(04 §3.5).

    `InquiryTopic`과 **독립 축**이다 — 같은 `topic=grade`라도 담담한 질문과 항의는 다르게
    다뤄야 하고, 그 구분이 인박스 최상단 정렬과 완충 강도의 입력이다.

    ⚠ **이름이 `tone`이 아닌 이유**: `composition/tone.py`의 `ToneRule`·`tone_key`가 이미
    "톤"을 **라벨 4축 → 문체 파라미터**(24조합)로 점유하고 있다. 같은 말을 쓰면 두 축이
    섞인다. `sentiment`는 업계 표준 용어다(Zendesk intelligent triage).

    ⚠ **v1에서 `POST /v1/counsel/drafts` 요청에는 싣지 않는다.** `part_a/05` §199에서
    완충 강도 파생을 도입하지 않기로 확정했으므로 초안 생성에 영향이 없다 — 쓰이는 곳이
    인박스 정렬(BE·FE 소유)뿐이라 AI에 되돌려 받을 이유가 없다. 근거 없이 필드부터
    만들지 않는다.
    """

    NORMAL = "normal"
    COMPLAINT = "complaint"


class InquiryUrgency(StrEnum):
    """긴급도 — 완충 강화 입력(불만 + 즉시 → 완충 강화)."""

    IMMEDIATE = "immediate"
    NORMAL = "normal"


class InquiryPayload(BaseModel):
    """문의 1건 — AI에게 원본 접근은 없고 마스킹 통과분만 온다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    inquiry_ref: NonEmptyStr
    """BE 원본 문의 논리 참조 — AI에겐 **불투명 키**다(역참조하지 않는다)."""

    topic: InquiryTopic
    urgency: InquiryUrgency
    received_at: datetime

    text_masked: NonEmptyStr
    """redaction 통과 문의 전문 — 실명·연락처는 여기 못 들어온다(불변식 3)."""


class ContextFact(BaseModel):
    """근거 한 줄 — 인용 가능한 사실의 전체 우주를 이루는 원소.

    `record_id`가 **없을 수 있다** — 집계·기준선 파생 fact에는 단일 기록이 없기 때문이다
    (`composition.EvidenceFact`와 같은 규율). 그 대가로 **record_id 없는 fact는 인용될 수
    없다** — `citations`는 record_id가 있는 것만으로 만들어진다(불변식 2).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    record_id: str | None = None
    summary: NonEmptyStr


class DismissedSuggestion(BaseModel):
    """강사가 무시한 라벨 제안 — 같은 근거 재제안 억제(AI가 무상태라 매 요청 동봉)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    axis: NonEmptyStr
    value: NonEmptyStr


class CounselContext(BaseModel):
    """근거 패키지 — alias·마스킹 통과분만. 초안이 인용할 수 있는 전체 우주다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_hash: NonEmptyStr
    """재현성 축(AI_RUN 기록 · 불변식 8) — "그때 그 입력"을 특정한다."""

    period_label: NonEmptyStr
    """기간 표기 — 문장 속 표현 + 게이트 허용 숫자 파생(05 §6-1)."""

    facts: tuple[ContextFact, ...] = ()

    def citable_facts(self) -> tuple[ContextFact, ...]:
        """인용 가능한 fact — `record_id`가 있는 것만(`DraftContext.cited_record_ids` 대칭).

        이게 **0건이면 근거 있는 초안을 만들 수 없다** — 라우터는 LLM 호출 전에
        `rejected_insufficient`로 끊는다(04 §3.9 규약 · 불변식 2).
        """
        return tuple(fact for fact in self.facts if fact.record_id)


class CounselDraftRequest(BaseModel):
    """§4-① POST /v1/counsel/drafts 바디. 참조는 전부 가명이다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    inquiry: InquiryPayload
    student_ref: NonEmptyStr
    parent_ref: NonEmptyStr
    class_ref: NonEmptyStr
    labels: tuple[NonEmptyStr, ...] = ()
    """확정 라벨 — 톤 게이트 입력(05 매핑). 제안이 아니라 강사 확정분이다."""

    dismissed_suggestions: tuple[DismissedSuggestion, ...] = ()
    context: CounselContext


# ───────────────────────── 응답 (AI → BE · §4-③) ─────────────────────────


class WireDraftStatus(StrEnum):
    """계약 §4-③ `result.draft_status` 4종 — **와이어 어휘**다.

    내부 `composition.DraftStatus`와 **일부러 다르다**(99 D ㊱): 내부는 판정만 담고
    `llm_failed`·`gate_exhausted`는 사유라서 `failed`의 `status_reason`으로 산다
    (error_codes §2.1). 변환은 `wire_status_for` 한 곳이 한다.
    """

    GENERATED = "generated"
    REJECTED_INSUFFICIENT = "rejected_insufficient"
    LLM_FAILED = "llm_failed"
    GATE_EXHAUSTED = "gate_exhausted"


#: 내부 판정 → 와이어 판정. **전수 등재가 계약**이며 CI가 대조한다(error_codes §2.1).
#: `FAILED`만 사유에 따라 갈라지므로 여기서는 기본값을 두고 `wire_status_for`가 세분한다.
_WIRE_BY_STATUS: Final[dict[DraftStatus, WireDraftStatus]] = {
    DraftStatus.GENERATED: WireDraftStatus.GENERATED,
    DraftStatus.TEMPLATE_ONLY: WireDraftStatus.GENERATED,
    DraftStatus.REJECTED_INSUFFICIENT: WireDraftStatus.REJECTED_INSUFFICIENT,
    DraftStatus.FAILED: WireDraftStatus.GATE_EXHAUSTED,
}

#: `fail_reason` 접두 → 와이어 실패 판정. graph.py가 내는 접두가 원본이다.
_FAILURE_WIRE: Final[dict[str, WireDraftStatus]] = {
    "llm_failed": WireDraftStatus.LLM_FAILED,
    "redaction_blocked": WireDraftStatus.LLM_FAILED,
    "gate_exhausted": WireDraftStatus.GATE_EXHAUSTED,
}


def wire_status_for(
    status: DraftStatus, fail_reason: str | None
) -> tuple[WireDraftStatus, str | None]:
    """내부 판정 + 사유 → (와이어 판정, `status_reason`). 순수 함수·결정론.

    변환을 **한 곳에** 둔다 — 내부 도메인 ≠ 와이어 표현이고, 그 변환이 라우터의 일이다.
    파생표에 없는 값이 와도 **크래시하지 않는다**: `unmapped:{값}`으로 정직하게 내보내고
    CI 대조(`test_counsel_contract.py`)가 먼저 막는다.

    `fail_reason`은 `접두` 또는 `접두:상세` 형태다(`llm_failed:LlmTimeout`) — 와이어에는
    **접두만** 싣는다. 상세는 예외 클래스명·게이트 내부 사유라 화면 어휘가 아니다.
    """
    if status not in _WIRE_BY_STATUS:
        return WireDraftStatus.GATE_EXHAUSTED, f"unmapped:{status}"
    prefix = (fail_reason or "").partition(":")[0]
    if status is DraftStatus.FAILED:
        return _FAILURE_WIRE.get(prefix, WireDraftStatus.GATE_EXHAUSTED), (
            prefix or "gate_exhausted"
        )
    return _WIRE_BY_STATUS[status], (prefix or None)


class Citation(BaseModel):
    """근거 인용 1건 — 각주형 목록.

    ⚠ **v1은 본문 인라인 앵커(`#L1`)를 지원하지 않는다**(99 D ㊳). `cite_id`는 목록 안의
    순서 키이며 본문과의 문장 단위 대응은 v1.1이다. 계약 §4-③ 예시의 `#L1` 문면은 그때
    유효해진다 — BE·FE에는 `docs/handoff/2026-07-31_counsel_router_v1_scope_to_BE.md`로
    통보했다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    cite_id: NonEmptyStr
    record_id: NonEmptyStr
    summary: NonEmptyStr


class LabelSuggestion(BaseModel):
    """라벨 제안 부산물 — ⚠ **v1에서는 항상 빈 배열**이다(생성기 미구현 · 99 D ㊲)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    axis: NonEmptyStr
    value: NonEmptyStr


class CounselDraftResult(BaseModel):
    """§4-③ `result` — 초안의 판정과 알맹이.

    🔴 **근거 없는 성공을 타입이 막는다**(불변식 2): `draft_status=generated`인데
    `citations`가 비었거나 `text`가 없으면 **구성 자체가 실패**한다. #37 `ResolvedEvidence`가
    "빈 성공"을 타입으로 막은 것과 같은 형태다 — 런타임 분기로 지키면 언젠가 새어 나간다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    draft_status: WireDraftStatus
    text: str | None = None
    citations: tuple[Citation, ...] = ()
    labels_applied: tuple[NonEmptyStr, ...] = ()
    label_suggestions: tuple[LabelSuggestion, ...] = ()
    status_reason: str | None = None
    generated_at: datetime

    @model_validator(mode="after")
    def _generated_must_be_grounded(self) -> Self:
        if self.draft_status is not WireDraftStatus.GENERATED:
            return self
        if not self.citations:
            raise ValueError("generated 초안에는 citations가 1건 이상 있어야 한다(불변식 2)")
        if not (self.text or "").strip():
            raise ValueError("generated 초안에는 text가 있어야 한다")
        return self


class CounselDraftJobView(BaseModel):
    """§4-③ GET 응답의 `data` — envelope의 meta는 라우터가 붙인다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: NonEmptyStr
    status: NonEmptyStr
    """잡 phase 7종(error_codes §2.5) — 초안 판정과 **다른 축**이다.

    `succeeded` + `result.draft_status="rejected_insufficient"`는 정상 조합이다
    (잡 성공 ≠ 초안 존재).
    """

    result: CounselDraftResult | None = None


# ───────────────────────── refine (§4-④) ─────────────────────────


class RefineRequest(BaseModel):
    """§4-④ 요청 — 자유 지시문 1턴."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    instruction: NonEmptyStr
    turn_no: int = Field(default=1, ge=1)
    """로그·이력용. **AI는 턴 상한을 판정하지 않는다** — 06 §1 "세션 턴 상한 없음"이고
    할당 집행은 전부 백엔드 Billing이다(7/15 BE-4). 쿼터 코드를 여기 만들지 않는다."""


class RefineResponse(BaseModel):
    """§4-④ 응답 — 반영·차단 모두 **200**이다(게이트 거부는 에러가 아니다 · 불변식 4).

    차단 문구(`message`)의 원본은 `part_a/06_refine_policy.md` §4 표다 — 여기서 문구를
    중복 정의하지 않는다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    applied: bool
    text: str | None = None
    citations: tuple[Citation, ...] = ()
    blocked_reason: BlockedReason | None = None
    message: str | None = None

    @model_validator(mode="after")
    def _blocked_needs_reason(self) -> Self:
        if self.applied:
            if self.blocked_reason is not None:
                raise ValueError("반영된 턴에는 blocked_reason을 싣지 않는다")
            if not (self.text or "").strip():
                raise ValueError("반영된 턴에는 text가 있어야 한다")
            if not self.citations:
                raise ValueError("반영된 턴에도 citations가 1건 이상 있어야 한다(불변식 2)")
        elif self.blocked_reason is None:
            raise ValueError("차단 턴에는 blocked_reason이 있어야 한다(사유 없는 거부 금지)")
        return self


__all__ = [
    "BlockedReason",
    "Citation",
    "ContextFact",
    "CounselContext",
    "CounselDraftJobView",
    "CounselDraftRequest",
    "CounselDraftResult",
    "DismissedSuggestion",
    "InquiryPayload",
    "InquirySentiment",
    "InquiryTopic",
    "InquiryUrgency",
    "LabelSuggestion",
    "RefineRequest",
    "RefineResponse",
    "WireDraftStatus",
    "wire_status_for",
]
