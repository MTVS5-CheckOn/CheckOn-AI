"""counsel 초안 계약 타입 — 인박스 데이터계약 v1 §4 왕복 대조.

**기대값의 출처는 계약 문서다** — 엔진 산출로 기대값을 만들지 않는다(7/23 감사 규칙).
아래 `_REQUEST_EXAMPLE`은 계약 §4-①의 예시 JSON을 손으로 옮긴 것이고,
`_RESPONSE_FIELDS`는 §4-③ 응답과 부록 필드 사전에서 옮긴 필드 전수다.

이 파일이 지키는 것 셋:
① 계약 예시가 **그대로** 요청 타입으로 파싱된다(필드 이름·enum 값 일치)
② `citations`가 비면 `generated` 결과를 **만들 수 없다**(불변식 2를 타입으로 봉쇄)
③ 내부 `DraftStatus` 전수가 와이어 4종 파생표에 등재돼 있다(error_codes §2.1 CI 대조)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ai.contracts.composition import DraftStatus
from ai.contracts.counsel import (
    Citation,
    CounselDraftRequest,
    CounselDraftResult,
    InquirySentiment,
    InquiryTopic,
    InquiryUrgency,
    WireDraftStatus,
    wire_status_for,
)

#: 계약 §4-① 예시 JSON — 손으로 옮긴 것(자동 생성 금지).
_REQUEST_EXAMPLE = {
    "inquiry": {
        "inquiry_ref": "iq_884",
        "topic": "grade",
        "urgency": "immediate",
        "received_at": "2026-07-31T14:20:00+09:00",
        "text_masked": "요즘 아이가 힘들어하는 것 같은데…",
    },
    "student_ref": "st_8f2a",
    "parent_ref": "pa_9c1d",
    "class_ref": "cl_a1",
    "labels": ["narrative", "anxiety_sensitive"],
    "dismissed_suggestions": [{"axis": "frequency", "value": "monthly"}],
    "context": {
        "snapshot_hash": "sha256:" + "a" * 64,
        "period_label": "2026년 7월",
        "facts": [
            {"record_id": "le_2041", "summary": "6월 지문 42개·312문항"},
            {"record_id": "le_2077", "summary": "제출률 100% (4주)"},
        ],
    },
}

#: 계약 §4-③ result 필드 전수 — 하나라도 빠지면 BE가 조립을 못 한다.
_RESULT_FIELDS = {
    "draft_status",
    "text",
    "citations",
    "labels_applied",
    "label_suggestions",
    "status_reason",
    "generated_at",
}


# ── ① 계약 예시가 그대로 파싱된다 ────────────────────────────────


def test_contract_example_parses() -> None:
    request = CounselDraftRequest.model_validate(_REQUEST_EXAMPLE)
    assert request.inquiry.inquiry_ref == "iq_884"
    assert request.inquiry.topic is InquiryTopic.GRADE
    assert request.inquiry.urgency is InquiryUrgency.IMMEDIATE
    assert request.context.period_label == "2026년 7월"
    assert len(request.context.facts) == 2


def test_topic_enum_matches_contract() -> None:
    """계약 [확정 enum] 4종 — `complaint`는 `InquirySentiment`로 이동(8/5). 임의 확장 금지."""
    assert {t.value for t in InquiryTopic} == {
        "grade",
        "schedule",
        "counsel_request",
        "etc",
    }


def test_sentiment_enum_matches_contract() -> None:
    """계약 [확정 enum] 2종 — `/v1/classify` 응답 전용 축(04 §3.5)."""
    assert {s.value for s in InquirySentiment} == {"normal", "complaint"}


def test_topic_and_sentiment_are_disjoint() -> None:
    """같은 값이 두 축에 살면 축 분리가 무의미하다 — 8/5 이전 상태의 회귀 방지."""
    assert not ({t.value for t in InquiryTopic} & {s.value for s in InquirySentiment})


def test_complaint_is_rejected_as_topic() -> None:
    """🔴 파괴적 변경의 계약면 — BE가 `topic="complaint"`를 보내면 거부된다.

    `test_unknown_field_is_rejected`(필드 거부)와 별개로 **enum 값 거부**를 고정한다.
    """
    body = json.loads(json.dumps(_REQUEST_EXAMPLE))
    body["inquiry"]["topic"] = "complaint"
    with pytest.raises(ValidationError):
        CounselDraftRequest.model_validate(body)


def test_urgency_enum_matches_contract() -> None:
    assert {u.value for u in InquiryUrgency} == {"immediate", "normal"}


def test_unknown_field_is_rejected() -> None:
    """extra=forbid — 계약에 없는 필드가 조용히 흘러들지 않는다(03 경계 규칙)."""
    with pytest.raises(ValidationError):
        CounselDraftRequest.model_validate({**_REQUEST_EXAMPLE, "student_name": "김철수"})


def test_realname_fields_are_absent_from_schema() -> None:
    """불변식 3 — 실명·연락처 필드는 스키마에 **존재하지 않는다**."""
    forbidden = {"name", "student_name", "parent_name", "phone", "email"}
    assert not forbidden & set(CounselDraftRequest.model_fields)


# ── ② 근거 없는 성공을 타입이 막는다 ─────────────────────────────


def _citation() -> Citation:
    return Citation(cite_id="L1", record_id="le_2041", summary="6월 지문 42개·312문항")


def test_result_has_every_contract_field() -> None:
    assert _RESULT_FIELDS <= set(CounselDraftResult.model_fields)


def test_generated_result_requires_citations() -> None:
    """🔴 불변식 2 — 근거 0건인 `generated`는 **구성 자체가 불가능**하다."""
    with pytest.raises(ValidationError, match="citations"):
        CounselDraftResult(
            draft_status=WireDraftStatus.GENERATED,
            text="어머님, 먼저 세심하게…",
            citations=(),
            generated_at=datetime(2026, 7, 31, tzinfo=UTC),
        )


def test_generated_result_requires_text() -> None:
    with pytest.raises(ValidationError, match="text"):
        CounselDraftResult(
            draft_status=WireDraftStatus.GENERATED,
            text=None,
            citations=(_citation(),),
            generated_at=datetime(2026, 7, 31, tzinfo=UTC),
        )


def test_generated_result_with_citations_is_valid() -> None:
    result = CounselDraftResult(
        draft_status=WireDraftStatus.GENERATED,
        text="어머님, 먼저 세심하게…",
        citations=(_citation(),),
        generated_at=datetime(2026, 7, 31, tzinfo=UTC),
    )
    assert result.citations[0].cite_id == "L1"
    assert result.label_suggestions == ()  # v1 상수 — 생성기 미구현


def test_rejected_result_carries_no_text() -> None:
    """정직한 거부 — 본문 없이 사유만. 에러가 아니다(불변식 4)."""
    result = CounselDraftResult(
        draft_status=WireDraftStatus.REJECTED_INSUFFICIENT,
        status_reason="context_missing",
        generated_at=datetime(2026, 7, 31, tzinfo=UTC),
    )
    assert result.text is None
    assert result.citations == ()


# ── ③ DraftStatus 전수가 파생표에 등재돼 있다 ────────────────────


@pytest.mark.parametrize("status", list(DraftStatus))
def test_every_internal_status_is_mapped(status: DraftStatus) -> None:
    """미등재 값은 크래시가 아니라 unmapped 사유로 나가되, CI가 먼저 막는다."""
    wire, reason = wire_status_for(status, None)
    assert not (reason or "").startswith("unmapped:"), (
        f"{status.value}가 와이어 파생표에 없다 — error_codes §2.1 표에 등재하라"
    )
    assert isinstance(wire, WireDraftStatus)


def test_failure_reasons_ride_status_reason_not_status() -> None:
    """계약 4종 대응 — llm_failed·gate_exhausted는 failed의 사유다(error_codes §2.1)."""
    assert wire_status_for(DraftStatus.FAILED, "llm_failed:LlmTimeout") == (
        WireDraftStatus.LLM_FAILED,
        "llm_failed",
    )
    assert wire_status_for(DraftStatus.FAILED, "gate_exhausted:too_long:9>8") == (
        WireDraftStatus.GATE_EXHAUSTED,
        "gate_exhausted",
    )


def test_unknown_status_degrades_honestly() -> None:
    """파생표 밖의 값이 와도 침묵하지 않는다 — 정직한 unmapped."""
    wire, reason = wire_status_for("brand_new_status", None)  # type: ignore[arg-type]
    assert wire is WireDraftStatus.GATE_EXHAUSTED or wire is WireDraftStatus.LLM_FAILED
    assert reason == "unmapped:brand_new_status"
