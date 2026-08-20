"""contracts/problem_generation.py 계약 검증 — 생성·상태·리비전 조합."""

from uuid import UUID

import pytest
from pydantic import TypeAdapter

from ai.contracts.gates import BlockedReason
from ai.contracts.llm import ParseFailed
from ai.contracts.problem_generation import (
    Answer,
    Choice,
    DifficultyBand,
    EvidenceAnchor,
    EvidenceKind,
    GeneratedItem,
    InvalidProblemGenerationStateTransition,
    ItemAction,
    ItemFieldChange,
    ItemResult,
    ItemRevision,
    ItemRevisionRequest,
    LiteratureGenre,
    MediaSourceKind,
    MediaSourceRequest,
    PassageDomain,
    PassageDraft,
    PassageRequest,
    ProblemFailureReason,
    ProblemGenerationOutcome,
    ProblemGenerationState,
    ProblemItemStatus,
    ProblemRequest,
    ProblemSetResult,
    ProblemSetStatus,
    RejectedInsufficientOutcome,
    ReviewReason,
    RevisionKind,
    SentenceComplexity,
    SetStopReason,
    SolveResult,
    SourceMaterialDraft,
    SpeechWritingSourceKind,
    SpeechWritingSourceRequest,
    TargetKind,
    TargetSelection,
    TargetSource,
    WorkSelection,
    assert_problem_generation_state_transition,
)
from ai.contracts.taxonomy import AreaTag, ItemFormat, TypeTag
from ai.llm.structured import parse
from ai.problem_generation.infrastructure.config import load_misconception_tags

SET_ID = UUID("00000000-0000-4000-8000-000000000010")
ITEM_ID = UUID("00000000-0000-4000-8000-000000000011")
SECOND_ITEM_ID = UUID("00000000-0000-4000-8000-000000000014")
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
                misconception_tag=None if no == 2 else "adjacent_change_type_confusion",
            )
            for no in range(1, 6)
        ),
        answer=Answer(correct_no=2),
        rationale="규칙 ID를 적용하면 2번만 성립한다.",
        evidence=(EvidenceAnchor(kind=EvidenceKind.GRAMMAR_RULE, ref="grammar.rule.001"),),
    )


def _state(**changes: object) -> ProblemGenerationState:
    data: dict[str, object] = {
        "request_ref": "problem-request:request-1",
        "request_hash": f"sha256:{'a' * 64}",
        "set_id": SET_ID,
        "target_source": TargetSource.WEAKNESS_AUTO,
        "requested_count": 3,
        "cursor": 0,
        "items": (),
        "item_attempt": 0,
        "stop_reason": None,
    }
    data.update(changes)
    return ProblemGenerationState.model_validate(data)


def _passage_draft(**changes: object) -> PassageDraft:
    data: dict[str, object] = {
        "passage_text": "첫째 문단입니다.\n\n둘째 문단입니다.",
        "paragraph_count": 2,
        "evidence_anchor_ids": ("reading:source-1",),
    }
    data.update(changes)
    return PassageDraft.model_validate(data)


def test_problem_request_roundtrip() -> None:
    request = _request()
    assert ProblemRequest.model_validate(request.model_dump(mode="json")) == request


def test_problem_request_difficulty_band_values_and_default() -> None:
    assert {band.value for band in DifficultyBand} == {"low", "medium", "high"}
    assert _request().requested_difficulty is None
    assert (
        _request(requested_difficulty="high").requested_difficulty
        is DifficultyBand.HIGH
    )


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


def test_problem_request_rejects_multiple_areas() -> None:
    with pytest.raises(ValueError, match="area_tag"):
        _request(area_tag=(AreaTag.SPEECH_WRITING, AreaTag.LANGUAGE))


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


@pytest.mark.parametrize(
    ("area_tag", "source_request"),
    [
        (
            AreaTag.SPEECH_WRITING,
            SpeechWritingSourceRequest(
                source_kind=SpeechWritingSourceKind.WRITING_DRAFT,
                banned_topics_version="v1",
            ),
        ),
        (
            AreaTag.MEDIA,
            MediaSourceRequest(
                source_kind=MediaSourceKind.PAIRED,
                banned_topics_version="v1",
            ),
        ),
    ],
)
def test_generated_source_request_roundtrip(
    area_tag: AreaTag,
    source_request: SpeechWritingSourceRequest | MediaSourceRequest,
) -> None:
    request = _request(area_tag=area_tag, passage=source_request)

    assert request.passage == source_request
    assert request.model_dump(mode="json")["passage"]["area_tag"] == area_tag.value


def test_generated_source_request_must_match_problem_area() -> None:
    with pytest.raises(ValueError, match="같아야"):
        _request(
            area_tag=AreaTag.MEDIA,
            passage=SpeechWritingSourceRequest(
                source_kind=SpeechWritingSourceKind.PRESENTATION,
                banned_topics_version="v1",
            ),
        )


def test_source_material_draft_requires_evidence() -> None:
    with pytest.raises(ValueError, match="evidence_anchor_ids"):
        SourceMaterialDraft(
            material_text="학생 A의 발표 자료",
            evidence_anchor_ids=(),
        )


def test_work_selection_roundtrip_and_literature_scope() -> None:
    selection = WorkSelection(
        genre=LiteratureGenre.MODERN_NOVEL,
        era="근대",
        concept_keywords=("공간", "인물"),
    )
    request = _request(area_tag=AreaTag.LITERATURE, work_selection=selection)

    assert WorkSelection.model_validate(selection.model_dump(mode="json")) == selection
    assert request.work_selection == selection


def test_work_selection_is_literature_only() -> None:
    with pytest.raises(ValueError, match="literature"):
        _request(
            work_selection=WorkSelection(genre=LiteratureGenre.MODERN_POETRY)
        )


def test_passage_and_work_selection_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="함께 사용할 수 없다"):
        _request(
            area_tag=AreaTag.LITERATURE,
            passage=PassageRequest(
                domain=PassageDomain.ART,
                word_count=500,
                sentence_complexity=SentenceComplexity.STANDARD,
                paragraph_count=2,
                banned_topics_version="v1",
            ),
            work_selection=WorkSelection(genre=LiteratureGenre.MODERN_POETRY),
        )


def test_work_selection_rejects_duplicate_keywords() -> None:
    with pytest.raises(ValueError, match="중복"):
        WorkSelection(
            genre=LiteratureGenre.CLASSICAL_POETRY,
            concept_keywords=("화자", "화자"),
        )


def test_passage_draft_roundtrip_and_exact_fields() -> None:
    draft = _passage_draft()

    assert PassageDraft.model_validate(draft.model_dump(mode="json")) == draft
    assert set(PassageDraft.model_fields) == {
        "passage_text",
        "paragraph_count",
        "evidence_anchor_ids",
    }


@pytest.mark.parametrize("paragraph_count", [1, 7])
def test_passage_draft_rejects_out_of_range_paragraph_count(
    paragraph_count: int,
) -> None:
    with pytest.raises(ValueError, match="paragraph_count"):
        _passage_draft(paragraph_count=paragraph_count)


def test_passage_draft_rejects_empty_evidence_anchor_ids() -> None:
    with pytest.raises(ValueError, match="evidence_anchor_ids"):
        _passage_draft(evidence_anchor_ids=())


@pytest.mark.parametrize(
    ("field", "value"),
    [("passage_text", ""), ("evidence_anchor_ids", ("",))],
)
def test_passage_draft_rejects_empty_required_text(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValueError, match=field):
        _passage_draft(**{field: value})


def test_passage_draft_rejects_extra_fields() -> None:
    with pytest.raises(ValueError, match="extra_forbidden"):
        _passage_draft(word_count=100)


def test_generation_unavailable_is_not_a_passage_draft() -> None:
    with pytest.raises(ParseFailed):
        parse("generation_unavailable", PassageDraft)


def test_generated_item_roundtrip() -> None:
    item = _item()
    assert GeneratedItem.model_validate(item.model_dump(mode="json")) == item


def test_generated_item_preserves_synthetic_suneung_t1_text_shapes() -> None:
    data = _item().model_dump(mode="json")
    data["stem"] = (
        "[합성 자료]\n"
        "㉠ 합성 예문 하나\n"
        "㉡ 합성 예문 둘\n"
        "구분 | 예문 A | 예문 B\n"
        "옛한글 코드 포인트: ᄀᆞᄅᆞ\n"
        "자료를 분석한 내용으로 가장 적절한 것은?"
    )
    data["choices"][0]["text"] = "ㄱ, ㄴ"
    data["choices"][1]["text"] = "ㄱ, ㄷ"
    data["choices"][2]["text"] = "ㄴ, ㄹ"
    data["choices"][3]["text"] = "ㄱ, ㄴ, ㄷ"
    data["choices"][4]["text"] = "ㄴ, ㄷ, ㄹ"

    item = GeneratedItem.model_validate(data)
    restored = GeneratedItem.model_validate(item.model_dump(mode="json"))

    assert restored == item
    assert "㉠" in restored.stem
    assert "ᄀᆞᄅᆞ" in restored.stem
    assert restored.choices[4].text == "ㄴ, ㄷ, ㄹ"


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


def test_every_wrong_choice_requires_misconception_tag() -> None:
    data = _item().model_dump(mode="json")
    data["choices"][0]["misconception_tag"] = None

    with pytest.raises(ValueError, match="misconception_tag"):
        GeneratedItem.model_validate(data)


def test_correct_choice_forbids_misconception_tag() -> None:
    data = _item().model_dump(mode="json")
    data["choices"][1]["misconception_tag"] = "adjacent_change_type_confusion"

    with pytest.raises(ValueError, match="정답 선지의 misconception_tag"):
        GeneratedItem.model_validate(data)


def test_misconception_tag_must_belong_to_item_area_vocabulary() -> None:
    data = _item().model_dump(mode="json")
    data["choices"][0]["misconception_tag"] = "scope_shift"

    with pytest.raises(ValueError, match="language 영역 어휘에 없는"):
        GeneratedItem.model_validate(
            data,
            context=load_misconception_tags().validation_context(),
        )


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
    assert {item.value for item in ReviewReason} == {
        "low_confidence",
        "area_mismatch",
        "t3_literature",
        "diagnostic_purpose",
        "manual_target_first",
        "difficulty_band_mismatch",
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


def test_item_result_preserves_difficulty_and_review_reason() -> None:
    result = ItemResult(
        item_id=ITEM_ID,
        status=ProblemItemStatus.NEEDS_REVIEW,
        attempt_no=1,
        difficulty_est=2.5,
        difficulty_band=DifficultyBand.MEDIUM,
        review_reason=ReviewReason.LOW_CONFIDENCE,
    )

    restored = ItemResult.model_validate(result.model_dump(mode="json"))

    assert restored == result
    assert restored.difficulty_fit is None


def test_item_result_difficulty_fit_is_always_null() -> None:
    with pytest.raises(ValueError, match="difficulty_fit"):
        ItemResult.model_validate(
            {
                "item_id": str(ITEM_ID),
                "status": "verified",
                "attempt_no": 1,
                "difficulty_fit": 0.8,
            }
        )


def test_review_reason_is_rejected_for_non_review_item() -> None:
    with pytest.raises(ValueError, match="review_reason"):
        ItemResult(
            item_id=ITEM_ID,
            status=ProblemItemStatus.VERIFIED,
            attempt_no=1,
            review_reason=ReviewReason.AREA_MISMATCH,
        )


def test_generated_set_requires_only_success_items() -> None:
    result = ProblemSetResult(
        set_id=SET_ID,
        status=ProblemSetStatus.GENERATED,
        target_source=TargetSource.WEAKNESS_AUTO,
        personalized=True,
        requested_count=1,
        processed_count=1,
        unstarted_count=0,
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
            requested_count=1,
            processed_count=0,
            unstarted_count=1,
        )


def test_partial_success_requires_success_and_failure() -> None:
    with pytest.raises(ValueError, match="partial_success"):
        ProblemSetResult(
            set_id=SET_ID,
            status=ProblemSetStatus.PARTIAL_SUCCESS,
            target_source=TargetSource.WEAKNESS_AUTO,
            personalized=True,
            requested_count=1,
            processed_count=1,
            unstarted_count=0,
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
            requested_count=1,
            processed_count=1,
            unstarted_count=0,
            items=(ItemResult(item_id=ITEM_ID, status=ProblemItemStatus.VERIFIED, attempt_no=1),),
        )


def test_rejected_insufficient_is_discriminated_normal_outcome() -> None:
    rejected = RejectedInsufficientOutcome(status_reason="개인화에 필요한 풀이 표본이 부족하다")

    outcome: ProblemGenerationOutcome = TypeAdapter(ProblemGenerationOutcome).validate_python(
        rejected.model_dump(mode="json")
    )

    assert isinstance(outcome, RejectedInsufficientOutcome)
    assert outcome == rejected
    assert outcome.status == "rejected_insufficient"
    assert outcome.personalized is False
    assert outcome.weakness_map_id is None


def test_rejected_insufficient_is_auto_target_only() -> None:
    with pytest.raises(ValueError, match="target_source"):
        RejectedInsufficientOutcome.model_validate(
            {
                "target_source": TargetSource.TEACHER_MANUAL,
                "status_reason": "표본 부족",
            }
        )


def test_processed_count_must_match_items() -> None:
    with pytest.raises(ValueError, match="processed_count"):
        ProblemSetResult(
            set_id=SET_ID,
            status=ProblemSetStatus.GENERATING,
            target_source=TargetSource.WEAKNESS_AUTO,
            personalized=True,
            requested_count=3,
            processed_count=2,
            unstarted_count=1,
            items=(ItemResult(item_id=ITEM_ID, status=ProblemItemStatus.VERIFIED, attempt_no=1),),
        )


def test_requested_count_must_preserve_all_slots() -> None:
    with pytest.raises(ValueError, match="requested_count"):
        ProblemSetResult(
            set_id=SET_ID,
            status=ProblemSetStatus.GENERATING,
            target_source=TargetSource.WEAKNESS_AUTO,
            personalized=True,
            requested_count=3,
            processed_count=1,
            unstarted_count=1,
            items=(ItemResult(item_id=ITEM_ID, status=ProblemItemStatus.VERIFIED, attempt_no=1),),
        )


def test_terminal_result_with_unstarted_items_requires_stop_reason() -> None:
    with pytest.raises(ValueError, match="stop_reason"):
        ProblemSetResult(
            set_id=SET_ID,
            status=ProblemSetStatus.PARTIAL_SUCCESS,
            target_source=TargetSource.WEAKNESS_AUTO,
            personalized=True,
            requested_count=3,
            processed_count=1,
            unstarted_count=2,
            items=(ItemResult(item_id=ITEM_ID, status=ProblemItemStatus.VERIFIED, attempt_no=1),),
        )


def test_early_stop_preserves_success_failure_and_unstarted_counts() -> None:
    result = ProblemSetResult(
        set_id=SET_ID,
        status=ProblemSetStatus.PARTIAL_SUCCESS,
        stop_reason=SetStopReason.DROP_RATIO_EXCEEDED,
        target_source=TargetSource.WEAKNESS_AUTO,
        personalized=True,
        requested_count=5,
        processed_count=2,
        unstarted_count=3,
        items=(
            ItemResult(item_id=ITEM_ID, status=ProblemItemStatus.VERIFIED, attempt_no=1),
            ItemResult(
                status=ProblemItemStatus.DROPPED,
                attempt_no=3,
                failure_reason=ProblemFailureReason.GENERATION_EXHAUSTED,
            ),
        ),
        dropped_reasons=(ProblemFailureReason.GENERATION_EXHAUSTED,),
    )

    outcome: ProblemGenerationOutcome = TypeAdapter(ProblemGenerationOutcome).validate_python(
        result.model_dump(mode="json")
    )

    assert isinstance(outcome, ProblemSetResult)
    assert outcome == result
    assert outcome.processed_count + outcome.unstarted_count == outcome.requested_count


def test_time_budget_can_stop_after_only_successful_items() -> None:
    result = ProblemSetResult(
        set_id=SET_ID,
        status=ProblemSetStatus.PARTIAL_SUCCESS,
        stop_reason=SetStopReason.TIME_BUDGET_EXCEEDED,
        target_source=TargetSource.WEAKNESS_AUTO,
        personalized=True,
        requested_count=3,
        processed_count=1,
        unstarted_count=2,
        items=(ItemResult(item_id=ITEM_ID, status=ProblemItemStatus.VERIFIED, attempt_no=1),),
    )

    assert result.status is ProblemSetStatus.PARTIAL_SUCCESS


def test_verification_unavailable_is_normal_failed_set_result() -> None:
    result = ProblemSetResult(
        set_id=SET_ID,
        status=ProblemSetStatus.FAILED,
        stop_reason=SetStopReason.VERIFIER_OUTAGE,
        target_source=TargetSource.WEAKNESS_AUTO,
        personalized=True,
        requested_count=3,
        processed_count=1,
        unstarted_count=2,
        items=(
            ItemResult(
                item_id=ITEM_ID,
                status=ProblemItemStatus.VERIFICATION_UNAVAILABLE,
                attempt_no=1,
                failure_reason=ProblemFailureReason.SOURCE_UNVERIFIED,
            ),
        ),
    )

    outcome: ProblemGenerationOutcome = TypeAdapter(ProblemGenerationOutcome).validate_python(
        result.model_dump(mode="json")
    )

    assert isinstance(outcome, ProblemSetResult)
    assert outcome == result
    assert outcome.outcome == "problem_set"


def test_generated_set_rejects_verification_unavailable_item() -> None:
    with pytest.raises(ValueError, match="generated"):
        ProblemSetResult(
            set_id=SET_ID,
            status=ProblemSetStatus.GENERATED,
            target_source=TargetSource.WEAKNESS_AUTO,
            personalized=True,
            requested_count=1,
            processed_count=1,
            unstarted_count=0,
            items=(
                ItemResult(
                    item_id=ITEM_ID,
                    status=ProblemItemStatus.VERIFICATION_UNAVAILABLE,
                    attempt_no=1,
                ),
            ),
        )


def test_generating_set_cannot_have_processed_all_items() -> None:
    with pytest.raises(ValueError, match="generating"):
        ProblemSetResult(
            set_id=SET_ID,
            status=ProblemSetStatus.GENERATING,
            target_source=TargetSource.WEAKNESS_AUTO,
            personalized=True,
            requested_count=2,
            processed_count=2,
            unstarted_count=0,
            items=(
                ItemResult(item_id=ITEM_ID, status=ProblemItemStatus.VERIFIED, attempt_no=1),
                ItemResult(
                    item_id=SECOND_ITEM_ID,
                    status=ProblemItemStatus.NEEDS_REVIEW,
                    attempt_no=1,
                ),
            ),
        )


def test_item_result_rejects_attempt_over_common_budget() -> None:
    with pytest.raises(ValueError, match="attempt_no"):
        ItemResult(
            status=ProblemItemStatus.DROPPED,
            attempt_no=4,
            failure_reason=ProblemFailureReason.GENERATION_EXHAUSTED,
        )


def test_problem_generation_state_roundtrip_and_slot_key() -> None:
    state = _state()

    restored = ProblemGenerationState.model_validate(state.model_dump(mode="json"))

    assert restored == state
    assert restored.state_schema_version == "problem_generation.v1"
    assert restored.current_slot_key == f"problem-set:{SET_ID}:slot:0"
    assert restored.unstarted_count == 3
    assert restored.fallback_ref is None
    assert restored.difficulty_regen_used is False


def test_problem_generation_state_restores_legacy_checkpoint_defaults() -> None:
    payload = _state().model_dump(mode="json")
    payload.pop("fallback_ref")
    payload.pop("difficulty_regen_used")

    restored = ProblemGenerationState.model_validate(payload)

    assert restored.state_schema_version == "problem_generation.v1"
    assert restored.fallback_ref is None
    assert restored.difficulty_regen_used is False


def test_problem_generation_state_rejects_fallback_without_active_slot() -> None:
    with pytest.raises(ValueError, match="처리 중인 문항"):
        _state(fallback_ref="candidate:1")


def test_problem_generation_state_requires_fallback_for_difficulty_regen() -> None:
    with pytest.raises(ValueError, match="fallback_ref"):
        _state(item_attempt=2, difficulty_regen_used=True)


def test_problem_generation_state_rejects_cursor_items_mismatch() -> None:
    with pytest.raises(ValueError, match="cursor"):
        _state(cursor=1)


def test_problem_generation_state_rejects_attempt_on_terminal_checkpoint() -> None:
    with pytest.raises(ValueError, match="item_attempt"):
        _state(
            item_attempt=1,
            stop_reason=SetStopReason.TIME_BUDGET_EXCEEDED,
        )


def test_problem_generation_state_rejects_unknown_schema_version() -> None:
    with pytest.raises(ValueError, match="state_schema_version"):
        _state(state_schema_version="problem_generation.v2")


def test_problem_generation_state_sets_passage_before_item_attempt() -> None:
    initial = _state()
    passage_ready = _state(passage_draft=_passage_draft())

    assert_problem_generation_state_transition(initial, passage_ready)


def test_problem_generation_state_keeps_generated_passage_immutable() -> None:
    previous = _state(passage_draft=_passage_draft())
    changed = _state(passage_draft=_passage_draft(passage_text="바뀐 지문"))

    with pytest.raises(InvalidProblemGenerationStateTransition, match="passage_draft"):
        assert_problem_generation_state_transition(previous, changed)


def test_problem_generation_state_rejects_late_passage_generation() -> None:
    attempt_started = _state(item_attempt=1)
    passage_added = _state(item_attempt=1, passage_draft=_passage_draft())

    with pytest.raises(InvalidProblemGenerationStateTransition, match="시작하기 전에"):
        assert_problem_generation_state_transition(attempt_started, passage_added)


def test_problem_generation_state_transition_preserves_current_attempt() -> None:
    initial = _state()
    first_attempt_started = _state(item_attempt=1)
    first_item_completed = _state(
        cursor=1,
        items=(
            ItemResult(
                item_id=ITEM_ID,
                status=ProblemItemStatus.VERIFIED,
                attempt_no=1,
            ),
        ),
    )

    assert_problem_generation_state_transition(initial, first_attempt_started)
    assert_problem_generation_state_transition(first_attempt_started, first_item_completed)

    assert first_attempt_started.unstarted_count == 2
    assert first_item_completed.item_attempt == 0
    assert first_item_completed.current_slot_key == f"problem-set:{SET_ID}:slot:1"


def test_problem_generation_state_transition_requires_attempt_reset() -> None:
    previous = _state(item_attempt=1)
    not_reset = _state(
        cursor=1,
        item_attempt=1,
        items=(
            ItemResult(
                item_id=ITEM_ID,
                status=ProblemItemStatus.VERIFIED,
                attempt_no=1,
            ),
        ),
    )

    with pytest.raises(InvalidProblemGenerationStateTransition, match="초기화"):
        assert_problem_generation_state_transition(previous, not_reset)


def test_problem_generation_state_transition_restores_preserved_candidate() -> None:
    regenerating = _state(
        item_attempt=2,
        fallback_ref="candidate:slot-0:attempt-1",
        difficulty_regen_used=True,
    )
    restored = _state(
        cursor=1,
        items=(
            ItemResult(
                item_id=ITEM_ID,
                status=ProblemItemStatus.NEEDS_REVIEW,
                attempt_no=1,
                difficulty_est=1.5,
                difficulty_band=DifficultyBand.LOW,
                review_reason=ReviewReason.DIFFICULTY_BAND_MISMATCH,
            ),
        ),
    )

    assert_problem_generation_state_transition(regenerating, restored)
    assert restored.fallback_ref is None
    assert restored.difficulty_regen_used is False


def test_problem_generation_state_transition_rejects_fallback_change() -> None:
    previous = _state(item_attempt=1, fallback_ref="candidate:attempt-1")
    changed = _state(item_attempt=1, fallback_ref="candidate:attempt-2")

    with pytest.raises(InvalidProblemGenerationStateTransition, match="fallback_ref"):
        assert_problem_generation_state_transition(previous, changed)


def test_problem_generation_state_transition_checkpoints_regen_attempt() -> None:
    preserved = _state(item_attempt=1, fallback_ref="candidate:attempt-1")
    regenerating = _state(
        item_attempt=2,
        fallback_ref="candidate:attempt-1",
        difficulty_regen_used=True,
    )

    assert_problem_generation_state_transition(preserved, regenerating)


def test_problem_generation_state_transition_rejects_regen_without_attempt() -> None:
    preserved = _state(item_attempt=1, fallback_ref="candidate:attempt-1")
    not_checkpointed = _state(
        item_attempt=1,
        fallback_ref="candidate:attempt-1",
        difficulty_regen_used=True,
    )

    with pytest.raises(InvalidProblemGenerationStateTransition, match="attempt"):
        assert_problem_generation_state_transition(preserved, not_checkpointed)


def test_problem_generation_state_transition_requires_pre_call_checkpoint() -> None:
    initial = _state()
    completed_without_checkpoint = _state(
        cursor=1,
        items=(
            ItemResult(
                item_id=ITEM_ID,
                status=ProblemItemStatus.VERIFIED,
                attempt_no=1,
            ),
        ),
    )

    with pytest.raises(InvalidProblemGenerationStateTransition, match="외부 호출 전"):
        assert_problem_generation_state_transition(initial, completed_without_checkpoint)


def test_problem_generation_state_third_attempt_exhaustion_is_preserved() -> None:
    initial = _state()
    first_attempt_started = _state(item_attempt=1)
    second_attempt_started = _state(item_attempt=2)
    third_attempt_started = _state(item_attempt=3)
    dropped = _state(
        cursor=1,
        items=(
            ItemResult(
                status=ProblemItemStatus.DROPPED,
                attempt_no=3,
                failure_reason=ProblemFailureReason.GENERATION_EXHAUSTED,
            ),
        ),
    )

    assert_problem_generation_state_transition(initial, first_attempt_started)
    assert_problem_generation_state_transition(first_attempt_started, second_attempt_started)
    assert_problem_generation_state_transition(second_attempt_started, third_attempt_started)
    assert_problem_generation_state_transition(third_attempt_started, dropped)


def test_problem_generation_state_transition_rejects_cursor_skip() -> None:
    initial = _state()
    skipped = _state(
        cursor=2,
        items=(
            ItemResult(
                item_id=ITEM_ID,
                status=ProblemItemStatus.VERIFIED,
                attempt_no=1,
            ),
            ItemResult(
                item_id=SECOND_ITEM_ID,
                status=ProblemItemStatus.NEEDS_REVIEW,
                attempt_no=1,
            ),
        ),
    )

    with pytest.raises(InvalidProblemGenerationStateTransition, match="cursor"):
        assert_problem_generation_state_transition(initial, skipped)


def test_problem_generation_state_transition_rejects_input_change() -> None:
    initial = _state()
    changed_request = _state(
        request_ref="problem-request:request-2",
        item_attempt=1,
    )

    with pytest.raises(InvalidProblemGenerationStateTransition, match="불변"):
        assert_problem_generation_state_transition(initial, changed_request)


def test_problem_generation_state_transition_rejects_completed_item_replacement() -> None:
    previous = _state(
        cursor=1,
        items=(
            ItemResult(
                item_id=ITEM_ID,
                status=ProblemItemStatus.VERIFIED,
                attempt_no=1,
            ),
        ),
    )
    replaced = _state(
        cursor=1,
        items=(
            ItemResult(
                item_id=SECOND_ITEM_ID,
                status=ProblemItemStatus.NEEDS_REVIEW,
                attempt_no=1,
            ),
        ),
    )

    with pytest.raises(InvalidProblemGenerationStateTransition, match="items"):
        assert_problem_generation_state_transition(previous, replaced)


def test_terminal_problem_generation_state_converts_to_partial_result() -> None:
    state = _state(
        requested_count=5,
        cursor=2,
        items=(
            ItemResult(
                item_id=ITEM_ID,
                status=ProblemItemStatus.VERIFIED,
                attempt_no=1,
            ),
            ItemResult(
                status=ProblemItemStatus.DROPPED,
                attempt_no=3,
                failure_reason=ProblemFailureReason.GENERATION_EXHAUSTED,
            ),
        ),
        stop_reason=SetStopReason.DROP_RATIO_EXCEEDED,
    )

    result = state.to_result(summary="2문항 처리 후 조기중단")

    assert result.status is ProblemSetStatus.PARTIAL_SUCCESS
    assert result.requested_count == 5
    assert result.processed_count == 2
    assert result.unstarted_count == 3
    assert result.dropped_reasons == (ProblemFailureReason.GENERATION_EXHAUSTED,)


def test_nonterminal_problem_generation_state_cannot_convert_to_result() -> None:
    with pytest.raises(ValueError, match="종료되지 않은"):
        _state().to_result()


def test_completed_problem_generation_state_converts_to_generated_result() -> None:
    state = _state(
        requested_count=1,
        cursor=1,
        items=(
            ItemResult(
                item_id=ITEM_ID,
                status=ProblemItemStatus.VERIFIED,
                attempt_no=1,
            ),
        ),
    )

    result = state.to_result()

    assert state.current_slot_key is None
    assert result.status is ProblemSetStatus.GENERATED
    assert result.processed_count == 1
    assert result.unstarted_count == 0


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
