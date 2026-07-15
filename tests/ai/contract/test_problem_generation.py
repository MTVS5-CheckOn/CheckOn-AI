"""contracts/problem_generation.py 계약 검증 — 생성·상태·리비전 조합."""

from uuid import UUID

import pytest

from ai.contracts.gates import BlockedReason
from ai.contracts.problem_generation import (
    Answer,
    Choice,
    EvidenceAnchor,
    EvidenceKind,
    GeneratedItem,
    ItemAction,
    ItemFieldChange,
    ItemResult,
    ItemRevision,
    ItemRevisionRequest,
    PassageDomain,
    PassageRequest,
    ProblemFailureReason,
    ProblemItemStatus,
    ProblemRequest,
    ProblemSetResult,
    ProblemSetStatus,
    RevisionKind,
    SentenceComplexity,
    SetStopReason,
    SolveResult,
    TargetKind,
    TargetSelection,
    TargetSource,
)
from ai.contracts.taxonomy import AreaTag, ItemFormat, TypeTag

SET_ID = UUID("00000000-0000-4000-8000-000000000010")
ITEM_ID = UUID("00000000-0000-4000-8000-000000000011")
WEAKNESS_MAP_ID = UUID("00000000-0000-4000-8000-000000000012")


def _request(**changes: object) -> ProblemRequest:
    data: dict[str, object] = {
        "request_id": "request-1",
        "idempotency_key": "idem-1",
        "tenant_id": "teacher-alias",
        "target_kind": TargetKind.STUDENT,
        "target_ref": "student-alias",
        "target_source": TargetSource.WEAKNESS_AUTO,
        "weakness_map_id": WEAKNESS_MAP_ID,
        "manual_targets": None,
        "snapshot_hash": "sha256:abc",
        "taxonomy_version": "v1",
        "area_tag": AreaTag.LANGUAGE,
        "type_tags": (TypeTag.CONCEPT,),
        "item_format": ItemFormat.MCQ,
        "count": 5,
        "target": TargetSelection.AUTO,
    }
    data.update(changes)
    return ProblemRequest.model_validate(data)


def _item() -> GeneratedItem:
    return GeneratedItem(
        area_tag=AreaTag.LANGUAGE,
        type_tag=TypeTag.CONCEPT,
        item_format=ItemFormat.MCQ,
        skill_node_id="lang.phoneme_change",
        stem="다음 중 음운 변동에 대한 설명으로 적절한 것은?",
        choices=tuple(
            Choice(
                no=no,
                text=f"선지 {no}",
                why_wrong=None if no == 2 else f"선지 {no}가 틀린 이유",
            )
            for no in range(1, 6)
        ),
        answer=Answer(correct_no=2),
        rationale="규칙 ID를 적용하면 2번만 성립한다.",
        evidence=(EvidenceAnchor(kind=EvidenceKind.GRAMMAR_RULE, ref="grammar.rule.001"),),
    )


def test_problem_request_roundtrip() -> None:
    request = _request()
    assert ProblemRequest.model_validate(request.model_dump(mode="json")) == request


def test_manual_target_requires_targets() -> None:
    with pytest.raises(ValueError, match="manual_targets"):
        _request(
            target_source=TargetSource.TEACHER_MANUAL,
            weakness_map_id=None,
            manual_targets=None,
        )


def test_manual_target_forbids_weakness_map() -> None:
    with pytest.raises(ValueError, match="weakness_map_id"):
        _request(
            target_source=TargetSource.TEACHER_MANUAL,
            manual_targets=("lang.phoneme_change",),
        )


def test_manual_target_request_is_valid() -> None:
    request = _request(
        target_source=TargetSource.TEACHER_MANUAL,
        weakness_map_id=None,
        manual_targets=("lang.phoneme_change",),
    )
    assert request.target_source is TargetSource.TEACHER_MANUAL


def test_manual_target_rejects_empty_target_id() -> None:
    with pytest.raises(ValueError, match="목표 ID"):
        _request(
            target_source=TargetSource.TEACHER_MANUAL,
            weakness_map_id=None,
            manual_targets=("",),
        )


def test_auto_target_forbids_manual_targets() -> None:
    with pytest.raises(ValueError, match="manual_targets"):
        _request(manual_targets=("lang.phoneme_change",))


@pytest.mark.parametrize("item_format", [ItemFormat.SHORT, ItemFormat.ESSAY])
def test_request_rejects_reserved_formats(item_format: ItemFormat) -> None:
    with pytest.raises(ValueError, match="mcq"):
        _request(item_format=item_format)


def test_request_rejects_duplicate_type_tags() -> None:
    with pytest.raises(ValueError, match="중복"):
        _request(type_tags=(TypeTag.CONCEPT, TypeTag.CONCEPT))


def test_passage_is_reading_only() -> None:
    passage = PassageRequest(
        domain=PassageDomain.SCIENCE,
        word_count=750,
        sentence_complexity=SentenceComplexity.STANDARD,
        paragraph_count=3,
        banned_topics_version="v1",
    )
    with pytest.raises(ValueError, match="reading"):
        _request(passage=passage)


def test_generated_item_roundtrip() -> None:
    item = _item()
    assert GeneratedItem.model_validate(item.model_dump(mode="json")) == item


def test_generated_item_requires_five_choices() -> None:
    data = _item().model_dump(mode="json")
    data["choices"] = data["choices"][:4]
    with pytest.raises(ValueError, match="choices"):
        GeneratedItem.model_validate(data)


def test_generated_item_requires_unique_one_to_five_numbers() -> None:
    data = _item().model_dump(mode="json")
    data["choices"][4]["no"] = 4
    with pytest.raises(ValueError, match="1부터 5"):
        GeneratedItem.model_validate(data)


@pytest.mark.parametrize("correct_no", [0, 6])
def test_answer_rejects_out_of_range(correct_no: int) -> None:
    with pytest.raises(ValueError, match="correct_no"):
        Answer(correct_no=correct_no)


def test_every_wrong_choice_requires_why_wrong() -> None:
    data = _item().model_dump(mode="json")
    data["choices"][0]["why_wrong"] = None
    with pytest.raises(ValueError, match="why_wrong"):
        GeneratedItem.model_validate(data)


def test_generated_item_requires_evidence() -> None:
    data = _item().model_dump(mode="json")
    data["evidence"] = []
    with pytest.raises(ValueError, match="evidence"):
        GeneratedItem.model_validate(data)


def test_passage_span_requires_quote() -> None:
    with pytest.raises(ValueError, match="quote"):
        EvidenceAnchor(kind=EvidenceKind.PASSAGE_SPAN, ref="문단2:문장3")


def test_generated_item_rejects_reserved_format() -> None:
    data = _item().model_dump(mode="json")
    data["item_format"] = "short"
    with pytest.raises(ValueError, match="mcq"):
        GeneratedItem.model_validate(data)


def test_answer_has_no_reserved_short_fields() -> None:
    assert set(Answer.model_fields) == {"correct_no"}


def test_solve_result_roundtrip() -> None:
    result = SolveResult(
        chosen=2,
        reasoning="2번만 규칙에 맞는다.",
        confidence=0.91,
        target_skill_node_id="lang.phoneme_change",
        measured_skill_node_id="lang.phoneme_change",
        aligned=True,
        alignment_confidence=0.88,
        alignment_reason="발문이 음운 변동 적용을 직접 측정한다.",
    )
    assert SolveResult.model_validate(result.model_dump(mode="json")) == result


@pytest.mark.parametrize("field,value", [("confidence", -0.1), ("alignment_confidence", 1.1)])
def test_solve_result_rejects_invalid_confidence(field: str, value: float) -> None:
    data: dict[str, object] = {
        "chosen": 2,
        "reasoning": "이유",
        "confidence": 0.9,
        "aligned": True,
        "alignment_confidence": 0.9,
        "alignment_reason": "정렬됨",
    }
    data[field] = value
    with pytest.raises(ValueError, match=field):
        SolveResult.model_validate(data)


def test_state_enum_values_frozen() -> None:
    assert {item.value for item in ProblemSetStatus} == {
        "queued",
        "generating",
        "generated",
        "partial_success",
        "failed",
    }
    assert {item.value for item in ProblemItemStatus} == {
        "verified",
        "needs_review",
        "dropped",
        "verification_unavailable",
    }
    assert {item.value for item in ProblemFailureReason} == {
        "generation_exhausted",
        "source_unverified",
        "banned_topic",
    }


def test_item_action_values_frozen() -> None:
    assert {item.value for item in ItemAction} == {
        "refine",
        "replace",
        "teacher_direct",
        "delete",
        "rollback",
    }


def test_dropped_item_requires_reason() -> None:
    with pytest.raises(ValueError, match="failure_reason"):
        ItemResult(status=ProblemItemStatus.DROPPED, attempt_no=3)


def test_verification_unavailable_requires_stored_item_id() -> None:
    with pytest.raises(ValueError, match="item_id"):
        ItemResult(status=ProblemItemStatus.VERIFICATION_UNAVAILABLE, attempt_no=1)


def test_generated_set_requires_only_success_items() -> None:
    result = ProblemSetResult(
        set_id=SET_ID,
        status=ProblemSetStatus.GENERATED,
        target_source=TargetSource.WEAKNESS_AUTO,
        personalized=True,
        items=(ItemResult(item_id=ITEM_ID, status=ProblemItemStatus.VERIFIED, attempt_no=1),),
        summary="1문항 생성",
    )
    assert ProblemSetResult.model_validate(result.model_dump(mode="json")) == result


def test_manual_set_must_be_non_personalized() -> None:
    with pytest.raises(ValueError, match="personalized"):
        ProblemSetResult(
            set_id=SET_ID,
            status=ProblemSetStatus.QUEUED,
            target_source=TargetSource.TEACHER_MANUAL,
            personalized=True,
        )


def test_partial_success_requires_success_and_failure() -> None:
    with pytest.raises(ValueError, match="partial_success"):
        ProblemSetResult(
            set_id=SET_ID,
            status=ProblemSetStatus.PARTIAL_SUCCESS,
            target_source=TargetSource.WEAKNESS_AUTO,
            personalized=True,
            items=(ItemResult(item_id=ITEM_ID, status=ProblemItemStatus.VERIFIED, attempt_no=1),),
        )


def test_failed_set_rejects_success_item() -> None:
    with pytest.raises(ValueError, match="failed"):
        ProblemSetResult(
            set_id=SET_ID,
            status=ProblemSetStatus.FAILED,
            target_source=TargetSource.WEAKNESS_AUTO,
            personalized=True,
            stop_reason=SetStopReason.TIME_BUDGET_EXCEEDED,
            items=(ItemResult(item_id=ITEM_ID, status=ProblemItemStatus.VERIFIED, attempt_no=1),),
        )


def test_ai_refine_request_requires_instruction() -> None:
    with pytest.raises(ValueError, match="instruction"):
        ItemRevisionRequest(
            request_id="request-2",
            idempotency_key="idem-2",
            item_id=ITEM_ID,
            base_revision_no=1,
            revision_kind=RevisionKind.AI_REFINE,
        )


def test_teacher_direct_request_requires_edited_item() -> None:
    with pytest.raises(ValueError, match="edited_item"):
        ItemRevisionRequest(
            request_id="request-2",
            idempotency_key="idem-2",
            item_id=ITEM_ID,
            base_revision_no=1,
            revision_kind=RevisionKind.TEACHER_DIRECT,
        )


def test_rollback_requires_previous_revision() -> None:
    with pytest.raises(ValueError, match="이전"):
        ItemRevisionRequest(
            request_id="request-2",
            idempotency_key="idem-2",
            item_id=ITEM_ID,
            base_revision_no=3,
            revision_kind=RevisionKind.ROLLBACK,
            revert_to=3,
        )


def test_revision_roundtrip() -> None:
    revision = ItemRevision(
        revision_no=2,
        revision_kind=RevisionKind.AI_REFINE,
        instruction="3번 선지를 자연스럽게",
        result_snapshot=_item(),
        diff=(
            ItemFieldChange(
                path="choices[2].text",
                before_json='"이전 선지"',
                after_json='"수정 선지"',
            ),
        ),
        verifications_passed=True,
        llm_call_id=UUID("00000000-0000-4000-8000-000000000013"),
    )
    assert ItemRevision.model_validate(revision.model_dump(mode="json")) == revision


def test_passed_revision_requires_snapshot() -> None:
    with pytest.raises(ValueError, match="result_snapshot"):
        ItemRevision(
            revision_no=2,
            revision_kind=RevisionKind.AI_REFINE,
            instruction="더 짧게",
            verifications_passed=True,
        )


def test_passed_revision_rejects_blocked_reason() -> None:
    with pytest.raises(ValueError, match="blocked_reason"):
        ItemRevision(
            revision_no=2,
            revision_kind=RevisionKind.AI_REFINE,
            instruction="더 짧게",
            result_snapshot=_item(),
            verifications_passed=True,
            blocked_reason=BlockedReason.OUT_OF_SCOPE,
        )


def test_blocked_revision_can_preserve_last_verified_item() -> None:
    revision = ItemRevision(
        revision_no=2,
        revision_kind=RevisionKind.AI_REFINE,
        instruction="정답을 두 개로",
        verifications_passed=False,
        blocked_reason=BlockedReason.ANSWER_INTEGRITY,
    )
    assert revision.result_snapshot is None
