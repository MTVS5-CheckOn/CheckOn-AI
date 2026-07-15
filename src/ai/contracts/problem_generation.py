"""문항 생성·검증·수정의 경계 계약.

사양 원본: docs/part_b/05_problem_generation.md §4
         docs/part_b/07_refine_policy.md
소유: member-B(염준영) 단독 (docs/02_ownership.md §3)

v1은 객관식 5지선다만 처리한다. ItemFormat.SHORT·ESSAY는 공용 enum의 예약값일
뿐이며 이 계약에는 답안·채점 필드나 처리 분기를 선반영하지 않는다.
"""

from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai.contracts.gates import BlockedReason
from ai.contracts.taxonomy import (
    SUPPORTED_ITEM_FORMATS,
    AreaTag,
    ItemFormat,
    TypeTag,
)


class PassageDomain(StrEnum):
    """비문학 지문 소재 영역 — 공용 taxonomy 승격 전 B 내부 어휘."""

    HUMANITIES = "humanities"
    SOCIAL = "social"
    SCIENCE = "science"
    TECH = "tech"
    ART = "art"
    FUSION = "fusion"


class SentenceComplexity(StrEnum):
    """지문 생성 문장 복잡도."""

    BASIC = "basic"
    STANDARD = "standard"
    ADVANCED = "advanced"


class PassageRequest(BaseModel):
    """T2 비문학 지문 생성 요청."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    area_tag: Literal[AreaTag.READING] = AreaTag.READING
    domain: PassageDomain
    topic_hint: str | None = Field(default=None, min_length=1)
    word_count: int = Field(gt=0)
    sentence_complexity: SentenceComplexity
    paragraph_count: int = Field(ge=2, le=6)
    banned_topics_version: str = Field(min_length=1)


class TargetKind(StrEnum):
    STUDENT = "student"
    CLASS = "class"


class TargetSource(StrEnum):
    WEAKNESS_AUTO = "weakness_auto"
    TEACHER_MANUAL = "teacher_manual"


class TargetSelection(StrEnum):
    CELL = "cell"
    NODE = "node"
    AUTO = "auto"


class ProblemRequest(BaseModel):
    """맞춤 문항 세트 생성 요청."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    target_kind: TargetKind
    target_ref: str = Field(min_length=1)
    target_source: TargetSource
    weakness_map_id: UUID | None = None
    manual_targets: tuple[str, ...] | None = None
    snapshot_hash: str = Field(min_length=1)
    taxonomy_version: str = Field(min_length=1)
    area_tag: AreaTag
    type_tags: tuple[TypeTag, ...] = Field(min_length=1)
    item_format: ItemFormat
    count: int = Field(ge=1, le=20)
    target: TargetSelection = TargetSelection.AUTO
    passage: PassageRequest | None = None
    topic_hint: str | None = Field(default=None, min_length=1)

    @field_validator("item_format")
    @classmethod
    def validate_supported_format(cls, value: ItemFormat) -> ItemFormat:
        if value not in SUPPORTED_ITEM_FORMATS:
            raise ValueError("v1 문항 생성은 mcq만 지원한다")
        return value

    @model_validator(mode="after")
    def validate_target_source(self) -> Self:
        if len(set(self.type_tags)) != len(self.type_tags):
            raise ValueError("type_tags는 중복될 수 없다")

        if self.target_source is TargetSource.TEACHER_MANUAL:
            if self.weakness_map_id is not None:
                raise ValueError("수동 목표 출제에는 weakness_map_id를 사용할 수 없다")
            if not self.manual_targets:
                raise ValueError("수동 목표 출제에는 manual_targets가 필요하다")
            if any(not target for target in self.manual_targets):
                raise ValueError("manual_targets의 목표 ID는 비어 있을 수 없다")
        elif self.manual_targets is not None:
            raise ValueError("자동 약점 출제에는 manual_targets를 사용할 수 없다")

        if self.passage is not None and self.area_tag is not AreaTag.READING:
            raise ValueError("PassageRequest는 reading 영역에서만 사용할 수 있다")
        return self


class Choice(BaseModel):
    """객관식 선지 1개."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    no: int = Field(ge=1, le=5)
    text: str = Field(min_length=1)
    why_wrong: str | None = Field(default=None, min_length=1)


class Answer(BaseModel):
    """v1 객관식 정답 — 단답형 예약 필드는 의도적으로 두지 않는다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    correct_no: int = Field(ge=1, le=5)


class EvidenceKind(StrEnum):
    PASSAGE_SPAN = "passage_span"
    DICT_ENTRY = "dict_entry"
    GRAMMAR_RULE = "grammar_rule"
    WORK_SPAN = "work_span"


class EvidenceAnchor(BaseModel):
    """문항 해설이 참조하는 근거 위치."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: EvidenceKind
    ref: str = Field(min_length=1)
    quote: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_quote(self) -> Self:
        if self.kind in {EvidenceKind.PASSAGE_SPAN, EvidenceKind.WORK_SPAN} and self.quote is None:
            raise ValueError("passage_span과 work_span에는 원문 quote가 필요하다")
        return self


class GeneratedItem(BaseModel):
    """문항 생성 LLM의 구조화 출력."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    area_tag: AreaTag
    type_tag: TypeTag
    item_format: ItemFormat
    skill_node_id: str | None = Field(default=None, min_length=1)
    stem: str = Field(min_length=1)
    choices: tuple[Choice, ...] = Field(min_length=5, max_length=5)
    answer: Answer
    rationale: str = Field(min_length=1)
    evidence: tuple[EvidenceAnchor, ...] = Field(min_length=1)

    @field_validator("item_format")
    @classmethod
    def validate_supported_format(cls, value: ItemFormat) -> ItemFormat:
        if value not in SUPPORTED_ITEM_FORMATS:
            raise ValueError("v1 생성 결과는 mcq만 허용한다")
        return value

    @model_validator(mode="after")
    def validate_choices(self) -> Self:
        numbers = {choice.no for choice in self.choices}
        if numbers != {1, 2, 3, 4, 5}:
            raise ValueError("선지 번호는 중복 없이 1부터 5까지여야 한다")
        if self.answer.correct_no not in numbers:
            raise ValueError("정답 번호가 선지 범위를 벗어났다")
        for choice in self.choices:
            if choice.no != self.answer.correct_no and choice.why_wrong is None:
                raise ValueError("모든 오답 선지에는 why_wrong이 필요하다")
        return self


class SolveResult(BaseModel):
    """blind 교차 풀이와 약점 의미 정렬의 구조화 출력."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chosen: int = Field(ge=1, le=5)
    reasoning: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    multiple_answers_possible: bool = False
    target_skill_node_id: str | None = Field(default=None, min_length=1)
    measured_skill_node_id: str | None = Field(default=None, min_length=1)
    aligned: bool
    alignment_confidence: float = Field(ge=0.0, le=1.0)
    alignment_reason: str = Field(min_length=1)


class ProblemSetStatus(StrEnum):
    """B 세트 상태. 공용 상태 사전 편입은 승인된 제안의 동기화 대상이다."""

    QUEUED = "queued"
    GENERATING = "generating"
    GENERATED = "generated"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"


class ProblemItemStatus(StrEnum):
    """B 문항 내부 검증 상태. 승인·학생 노출 상태가 아니다."""

    VERIFIED = "verified"
    NEEDS_REVIEW = "needs_review"
    DROPPED = "dropped"
    VERIFICATION_UNAVAILABLE = "verification_unavailable"


class ProblemFailureReason(StrEnum):
    """B 문항 실패 사유. 공용 상태 사전 편입 동기화 대상이다."""

    GENERATION_EXHAUSTED = "generation_exhausted"
    SOURCE_UNVERIFIED = "source_unverified"
    BANNED_TOPIC = "banned_topic"


class SetStopReason(StrEnum):
    """세트 조기 중단 원인 — 임계값 자체는 verify_config가 소유한다."""

    DROP_RATIO_EXCEEDED = "drop_ratio_exceeded"
    VERIFIER_OUTAGE = "verifier_outage"
    TIME_BUDGET_EXCEEDED = "time_budget_exceeded"
    BANNED_TOPIC_PASSAGE = "banned_topic_passage"


class ItemResult(BaseModel):
    """문항 1개의 최종 처리 결과."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: UUID | None = None
    status: ProblemItemStatus
    attempt_no: int = Field(ge=1)
    failure_reason: ProblemFailureReason | None = None
    failure_detail: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status in {
            ProblemItemStatus.VERIFIED,
            ProblemItemStatus.NEEDS_REVIEW,
            ProblemItemStatus.VERIFICATION_UNAVAILABLE,
        }:
            if self.item_id is None:
                raise ValueError("저장된 문항 상태에는 item_id가 필요하다")
        if self.status in {ProblemItemStatus.VERIFIED, ProblemItemStatus.NEEDS_REVIEW}:
            if self.failure_reason is not None:
                raise ValueError("검증 완료 문항에는 failure_reason을 기록하지 않는다")
        if self.status is ProblemItemStatus.DROPPED and self.failure_reason is None:
            raise ValueError("dropped 문항에는 failure_reason이 필요하다")
        return self


class ProblemSetResult(BaseModel):
    """비동기 문항 세트의 진행 또는 최종 결과."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    set_id: UUID
    status: ProblemSetStatus
    stop_reason: SetStopReason | None = None
    target_source: TargetSource
    personalized: bool
    items: tuple[ItemResult, ...] = ()
    summary: str | None = Field(default=None, min_length=1)
    dropped_reasons: tuple[ProblemFailureReason, ...] = ()

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        expected_personalized = self.target_source is TargetSource.WEAKNESS_AUTO
        if self.personalized is not expected_personalized:
            raise ValueError("target_source와 personalized 값이 일치하지 않는다")

        succeeded = sum(
            item.status in {ProblemItemStatus.VERIFIED, ProblemItemStatus.NEEDS_REVIEW}
            for item in self.items
        )
        failed = len(self.items) - succeeded
        if self.status is ProblemSetStatus.GENERATED and (not self.items or failed):
            raise ValueError("generated 세트는 모든 문항이 검증 완료 상태여야 한다")
        if self.status is ProblemSetStatus.PARTIAL_SUCCESS and not (succeeded and failed):
            raise ValueError("partial_success는 성공과 실패 문항을 모두 포함해야 한다")
        if self.status is ProblemSetStatus.FAILED and succeeded:
            raise ValueError("failed 세트에는 성공 문항이 있을 수 없다")
        return self


class ItemAction(StrEnum):
    REFINE = "refine"
    REPLACE = "replace"
    TEACHER_DIRECT = "teacher_direct"
    DELETE = "delete"
    ROLLBACK = "rollback"


class RevisionKind(StrEnum):
    AI_REFINE = "ai_refine"
    TEACHER_DIRECT = "teacher_direct"
    ROLLBACK = "rollback"


class ItemRevisionRequest(BaseModel):
    """AI 수정·강사 직접 수정·롤백의 낙관적 잠금 요청."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    item_id: UUID
    base_revision_no: int = Field(ge=0)
    revision_kind: RevisionKind
    instruction: str | None = Field(default=None, min_length=1)
    edited_item: GeneratedItem | None = None
    revert_to: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_revision_kind(self) -> Self:
        if self.revision_kind is RevisionKind.AI_REFINE:
            if (
                self.instruction is None
                or self.edited_item is not None
                or self.revert_to is not None
            ):
                raise ValueError("ai_refine은 instruction만 필요하다")
        elif self.revision_kind is RevisionKind.TEACHER_DIRECT:
            if self.edited_item is None or self.revert_to is not None:
                raise ValueError("teacher_direct는 edited_item이 필요하다")
        else:
            if (
                self.revert_to is None
                or self.instruction is not None
                or self.edited_item is not None
            ):
                raise ValueError("rollback은 revert_to만 필요하다")
            if self.revert_to >= self.base_revision_no:
                raise ValueError("revert_to는 현재 리비전보다 이전이어야 한다")
        return self


class ItemFieldChange(BaseModel):
    """변경값을 canonical JSON 문자열로 보존하는 문항 diff 항목."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(min_length=1)
    before_json: str | None = None
    after_json: str | None = None


class ItemRevision(BaseModel):
    """ITEM_REVISION 1행의 계약 표현."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    revision_no: int = Field(ge=1)
    revision_kind: RevisionKind
    instruction: str | None = Field(default=None, min_length=1)
    result_snapshot: GeneratedItem | None = None
    diff: tuple[ItemFieldChange, ...] = ()
    verifications_passed: bool
    blocked_reason: BlockedReason | None = None
    llm_call_id: UUID | None = None

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        if self.verifications_passed and self.result_snapshot is None:
            raise ValueError("검증 통과 리비전에는 result_snapshot이 필요하다")
        if self.verifications_passed and self.blocked_reason is not None:
            raise ValueError("검증 통과 리비전에는 blocked_reason을 기록하지 않는다")
        return self
