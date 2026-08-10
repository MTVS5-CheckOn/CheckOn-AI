"""문항 생성·검증·수정의 경계 계약.

사양 원본: docs/part_b/05_problem_generation.md §4
         docs/part_b/07_refine_policy.md
소유: member-B(염준영) 단독 (docs/02_ownership.md §3)

v1은 객관식 5지선다만 처리한다. ItemFormat.SHORT·ESSAY는 공용 enum의 예약값일
뿐이며 이 계약에는 답안·채점 필드나 처리 분기를 선반영하지 않는다.
"""

from enum import StrEnum
from typing import Annotated, Final, Literal, Self
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


class LiteratureGenre(StrEnum):
    """버전 고정 문학 풀의 갈래 어휘."""

    CLASSICAL_POETRY = "classical_poetry"
    MODERN_POETRY = "modern_poetry"
    MODERN_NOVEL = "modern_novel"


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


class WorkSelection(BaseModel):
    """T3 문학 작품 선택 조건 — 작품 생성 파라미터가 아니다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    genre: LiteratureGenre
    era: str | None = Field(default=None, min_length=1)
    concept_keywords: tuple[Annotated[str, Field(min_length=1)], ...] = ()

    @model_validator(mode="after")
    def validate_keywords(self) -> Self:
        if len(set(self.concept_keywords)) != len(self.concept_keywords):
            raise ValueError("concept_keywords는 중복될 수 없다")
        return self


class WorkExcerpt(BaseModel):
    """결정론 선택기가 고른 원문 오프셋 구간."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str = Field(min_length=1)
    title: str = Field(min_length=1)
    author: str = Field(min_length=1)
    era: str = Field(min_length=1)
    genre: LiteratureGenre
    source_ref: str = Field(min_length=1)
    revision_id: int = Field(gt=0)
    content_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_span(self) -> Self:
        if self.end <= self.start:
            raise ValueError("work excerpt의 end는 start보다 커야 한다")
        return self

    @property
    def evidence_ref(self) -> str:
        return f"work:{self.slug}:{self.revision_id}:{self.start}:{self.end}"


class PassageDraft(BaseModel):
    """승인 근거에만 기반한 T2 지문 생성 성공 출력.

    paragraph_count는 독립 산출물도 요청과 같은 2..6 범위를 벗어나지 않게 한다.
    요청값과의 일치는 이 모델이 PassageRequest를 포함하지 않으므로 생성기 경계에서
    대조한다. generation_unavailable은 성공 출력이 아니며 구조화 파싱 실패로 닫는다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    passage_text: str = Field(min_length=1)
    paragraph_count: int = Field(ge=2, le=6)
    evidence_anchor_ids: tuple[
        Annotated[str, Field(min_length=1)], ...
    ] = Field(min_length=1)


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


class DifficultyBand(StrEnum):
    """문항 요청·결과에 사용하는 내부 난이도 밴드."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


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
    requested_difficulty: DifficultyBand | None = None
    target: TargetSelection = TargetSelection.AUTO
    passage: PassageRequest | None = None
    work_selection: WorkSelection | None = None
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

        if self.passage is not None and self.work_selection is not None:
            raise ValueError("passage와 work_selection은 함께 사용할 수 없다")
        if self.passage is not None and self.area_tag is not AreaTag.READING:
            raise ValueError("PassageRequest는 reading 영역에서만 사용할 수 있다")
        if (
            self.work_selection is not None
            and self.area_tag is not AreaTag.LITERATURE
        ):
            raise ValueError("WorkSelection은 literature 영역에서만 사용할 수 있다")
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


class ReviewReason(StrEnum):
    """검증 완료 문항에 강사 검토 배지를 부여한 사유."""

    LOW_CONFIDENCE = "low_confidence"
    AREA_MISMATCH = "area_mismatch"
    T3_LITERATURE = "t3_literature"
    DIAGNOSTIC_PURPOSE = "diagnostic_purpose"
    MANUAL_TARGET_FIRST = "manual_target_first"
    DIFFICULTY_BAND_MISMATCH = "difficulty_band_mismatch"


class SetStopReason(StrEnum):
    """세트 조기 중단 원인 — 임계값 자체는 verify_config가 소유한다."""

    DROP_RATIO_EXCEEDED = "drop_ratio_exceeded"
    VERIFIER_OUTAGE = "verifier_outage"
    TIME_BUDGET_EXCEEDED = "time_budget_exceeded"
    BANNED_TOPIC_PASSAGE = "banned_topic_passage"


PROBLEM_GENERATION_ITEM_ATTEMPT_LIMIT: Final = 3


class ItemResult(BaseModel):
    """문항 1개의 최종 처리 결과."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: UUID | None = None
    status: ProblemItemStatus
    attempt_no: int = Field(ge=1, le=PROBLEM_GENERATION_ITEM_ATTEMPT_LIMIT)
    failure_reason: ProblemFailureReason | None = None
    failure_detail: str | None = Field(default=None, min_length=1)
    difficulty_est: float | None = None
    difficulty_band: DifficultyBand | None = None
    difficulty_fit: None = None
    review_reason: ReviewReason | None = None

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
        if (
            self.status is not ProblemItemStatus.NEEDS_REVIEW
            and self.review_reason is not None
        ):
            raise ValueError("review_reason은 needs_review 문항에만 기록한다")
        return self


class RejectedInsufficientOutcome(BaseModel):
    """자동 개인화에 필요한 데이터가 부족해 생성 전 정상 종료된 결과."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: Literal["rejected_insufficient"] = "rejected_insufficient"
    status: Literal["rejected_insufficient"] = "rejected_insufficient"
    target_source: Literal[TargetSource.WEAKNESS_AUTO] = TargetSource.WEAKNESS_AUTO
    personalized: Literal[False] = False
    weakness_map_id: None = None
    status_reason: str = Field(min_length=1)


class ProblemSetResult(BaseModel):
    """비동기 문항 세트의 진행 또는 최종 결과."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: Literal["problem_set"] = "problem_set"
    set_id: UUID
    status: ProblemSetStatus
    stop_reason: SetStopReason | None = None
    target_source: TargetSource
    personalized: bool
    requested_count: int = Field(ge=1, le=20)
    processed_count: int = Field(ge=0)
    unstarted_count: int = Field(ge=0)
    items: tuple[ItemResult, ...] = ()
    summary: str | None = Field(default=None, min_length=1)
    dropped_reasons: tuple[ProblemFailureReason, ...] = ()

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        expected_personalized = self.target_source is TargetSource.WEAKNESS_AUTO
        if self.personalized is not expected_personalized:
            raise ValueError("target_source와 personalized 값이 일치하지 않는다")

        if self.processed_count != len(self.items):
            raise ValueError("processed_count는 items 길이와 일치해야 한다")
        if self.requested_count != self.processed_count + self.unstarted_count:
            raise ValueError("requested_count는 processed_count와 unstarted_count의 합이어야 한다")

        succeeded = sum(
            item.status in {ProblemItemStatus.VERIFIED, ProblemItemStatus.NEEDS_REVIEW}
            for item in self.items
        )
        unsuccessful = self.processed_count - succeeded

        if self.status is ProblemSetStatus.QUEUED:
            if self.processed_count or self.stop_reason is not None:
                raise ValueError("queued 세트는 처리 문항이나 stop_reason을 가질 수 없다")
            return self

        if self.status is ProblemSetStatus.GENERATING:
            if self.unstarted_count == 0:
                raise ValueError("모든 문항을 처리한 세트는 generating 상태일 수 없다")
            if self.stop_reason is not None:
                raise ValueError("generating 세트에는 stop_reason을 기록할 수 없다")
            return self

        if self.unstarted_count and self.stop_reason is None:
            raise ValueError("미처리 문항이 남은 최종 세트에는 stop_reason이 필요하다")

        if self.status is ProblemSetStatus.GENERATED:
            if self.unstarted_count or unsuccessful:
                raise ValueError("generated 세트는 요청 문항을 모두 검증 완료해야 한다")
            if self.stop_reason is not None:
                raise ValueError("generated 세트에는 stop_reason을 기록할 수 없다")
        elif self.status is ProblemSetStatus.PARTIAL_SUCCESS:
            if not succeeded or not (unsuccessful or self.unstarted_count):
                raise ValueError(
                    "partial_success는 성공 문항과 실패 또는 미처리 문항을 포함해야 한다"
                )
        elif self.status is ProblemSetStatus.FAILED and succeeded:
            raise ValueError("failed 세트에는 성공 문항이 있을 수 없다")
        return self


type ProblemGenerationOutcome = Annotated[
    RejectedInsufficientOutcome | ProblemSetResult,
    Field(discriminator="outcome"),
]


class ProblemGenerationState(BaseModel):
    """문항 슬롯 경계에서 재개하는 문제생성 워커 체크포인트."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state_schema_version: Literal["problem_generation.v1"] = "problem_generation.v1"
    request_ref: str = Field(min_length=1)
    request_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    set_id: UUID
    target_source: TargetSource
    requested_count: int = Field(ge=1, le=20)
    cursor: int = Field(default=0, ge=0)
    items: tuple[ItemResult, ...] = ()
    item_attempt: int = Field(
        default=0,
        ge=0,
        le=PROBLEM_GENERATION_ITEM_ATTEMPT_LIMIT,
    )
    stop_reason: SetStopReason | None = None
    fallback_ref: str | None = Field(default=None, min_length=1)
    difficulty_regen_used: bool = False
    passage_draft: PassageDraft | None = None

    @model_validator(mode="after")
    def validate_checkpoint(self) -> Self:
        if self.cursor > self.requested_count:
            raise ValueError("cursor는 requested_count를 초과할 수 없다")
        if self.cursor != len(self.items):
            raise ValueError("cursor는 완료된 items 길이와 일치해야 한다")
        if self.is_terminal and self.item_attempt:
            raise ValueError("종료 state에는 현재 문항 item_attempt를 남길 수 없다")
        if self.item_attempt == 0 and (
            self.fallback_ref is not None or self.difficulty_regen_used
        ):
            raise ValueError(
                "fallback_ref와 difficulty_regen_used는 처리 중인 문항에만 기록한다"
            )
        if self.difficulty_regen_used and self.fallback_ref is None:
            raise ValueError("난이도 재생성 state에는 fallback_ref가 필요하다")

        succeeded = sum(
            item.status in {ProblemItemStatus.VERIFIED, ProblemItemStatus.NEEDS_REVIEW}
            for item in self.items
        )
        if (
            self.stop_reason is not None
            and self.cursor == self.requested_count
            and succeeded == self.requested_count
        ):
            raise ValueError("모든 문항을 성공한 state에는 stop_reason을 기록할 수 없다")
        return self

    @property
    def is_terminal(self) -> bool:
        """모든 슬롯을 처리했거나 조기중단 사유가 확정됐는지 반환한다."""

        return self.stop_reason is not None or self.cursor == self.requested_count

    @property
    def unstarted_count(self) -> int:
        """현재 처리 중인 슬롯을 제외하고 아직 시작하지 않은 슬롯 수."""

        active_slot_count = int(self.item_attempt > 0)
        return self.requested_count - self.cursor - active_slot_count

    @property
    def current_slot_key(self) -> str | None:
        """현재 슬롯의 멱등 저장 키. 종료 state에는 슬롯이 없다."""

        if self.is_terminal:
            return None
        return f"problem-set:{self.set_id}:slot:{self.cursor}"

    def to_result(self, *, summary: str | None = None) -> ProblemSetResult:
        """종료 체크포인트를 최종 세트 결과로 변환한다."""

        if not self.is_terminal:
            raise ValueError("종료되지 않은 state는 최종 ProblemSetResult로 변환할 수 없다")

        succeeded = sum(
            item.status in {ProblemItemStatus.VERIFIED, ProblemItemStatus.NEEDS_REVIEW}
            for item in self.items
        )
        if self.cursor == self.requested_count and succeeded == self.requested_count:
            status = ProblemSetStatus.GENERATED
        elif succeeded:
            status = ProblemSetStatus.PARTIAL_SUCCESS
        else:
            status = ProblemSetStatus.FAILED

        dropped_reasons = tuple(
            item.failure_reason
            for item in self.items
            if item.status is ProblemItemStatus.DROPPED and item.failure_reason is not None
        )
        return ProblemSetResult(
            set_id=self.set_id,
            status=status,
            stop_reason=self.stop_reason,
            target_source=self.target_source,
            personalized=self.target_source is TargetSource.WEAKNESS_AUTO,
            requested_count=self.requested_count,
            processed_count=self.cursor,
            unstarted_count=self.unstarted_count,
            items=self.items,
            summary=summary,
            dropped_reasons=dropped_reasons,
        )


class InvalidProblemGenerationStateTransition(ValueError):
    """문제생성 체크포인트의 단조 전이가 깨진 경우."""


def assert_problem_generation_state_transition(
    previous: ProblemGenerationState,
    current: ProblemGenerationState,
) -> None:
    """순차 슬롯 처리와 현재 문항 재시도 예산의 단조 전이를 검증한다."""

    if previous == current:
        return
    if _problem_state_identity(previous) != _problem_state_identity(current):
        raise InvalidProblemGenerationStateTransition(
            "state 전이 중 불변 문제생성 입력을 변경할 수 없다"
        )
    if previous.is_terminal:
        raise InvalidProblemGenerationStateTransition("종료 state는 더 전이할 수 없다")
    if previous.passage_draft is not None and current.passage_draft != previous.passage_draft:
        raise InvalidProblemGenerationStateTransition(
            "생성된 passage_draft는 변경하거나 제거할 수 없다"
        )
    if previous.passage_draft is None and current.passage_draft is not None:
        if previous.cursor or previous.item_attempt or current.cursor or current.item_attempt:
            raise InvalidProblemGenerationStateTransition(
                "passage_draft는 문항 처리를 시작하기 전에만 설정할 수 있다"
            )

    cursor_delta = current.cursor - previous.cursor
    if cursor_delta not in {0, 1}:
        raise InvalidProblemGenerationStateTransition(
            "cursor는 한 전이에서 완료 문항 하나만큼만 증가할 수 있다"
        )

    if cursor_delta == 0:
        if current.items != previous.items:
            raise InvalidProblemGenerationStateTransition(
                "cursor 증가 없이 완료 items를 변경할 수 없다"
            )
        if current.stop_reason is not None:
            if previous.item_attempt:
                raise InvalidProblemGenerationStateTransition(
                    "현재 문항 처리 중에는 조기중단 state로 전이할 수 없다"
                )
            return
        if current.item_attempt not in {
            previous.item_attempt,
            previous.item_attempt + 1,
        }:
            raise InvalidProblemGenerationStateTransition(
                "현재 문항 item_attempt는 한 번에 1만 증가할 수 있다"
            )
        if (
            previous.fallback_ref is not None
            and current.fallback_ref != previous.fallback_ref
        ):
            raise InvalidProblemGenerationStateTransition(
                "현재 슬롯의 fallback_ref는 설정 후 변경하거나 제거할 수 없다"
            )
        if previous.difficulty_regen_used and not current.difficulty_regen_used:
            raise InvalidProblemGenerationStateTransition(
                "현재 슬롯의 difficulty_regen_used는 되돌릴 수 없다"
            )
        if (
            not previous.difficulty_regen_used
            and current.difficulty_regen_used
            and current.item_attempt != previous.item_attempt + 1
        ):
            raise InvalidProblemGenerationStateTransition(
                "난이도 재생성 전 외부 호출 attempt를 먼저 증가해야 한다"
            )
        return

    if current.items[:-1] != previous.items:
        raise InvalidProblemGenerationStateTransition(
            "완료 문항 추가 시 기존 items 순서를 보존해야 한다"
        )
    if current.item_attempt:
        raise InvalidProblemGenerationStateTransition(
            "문항 완료 후 다음 슬롯의 item_attempt는 0으로 초기화해야 한다"
        )
    if previous.item_attempt == 0:
        raise InvalidProblemGenerationStateTransition(
            "외부 호출 전 item_attempt를 증가·체크포인트해야 한다"
        )
    completed_attempt = current.items[-1].attempt_no
    restored_fallback = (
        previous.fallback_ref is not None
        and previous.difficulty_regen_used
        and completed_attempt < previous.item_attempt
    )
    if completed_attempt != previous.item_attempt and not restored_fallback:
        raise InvalidProblemGenerationStateTransition(
            "완료 문항 attempt_no는 현재 시도 또는 보존 fallback 시도여야 한다"
        )


def _problem_state_identity(state: ProblemGenerationState) -> tuple[object, ...]:
    return (
        state.state_schema_version,
        state.request_ref,
        state.request_hash,
        state.set_id,
        state.target_source,
        state.requested_count,
    )


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
