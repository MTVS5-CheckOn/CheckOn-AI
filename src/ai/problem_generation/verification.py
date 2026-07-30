"""문항 게이트 ①·③의 결정론 검증과 버전 설정 로더."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Self

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ai.contracts.graphrag import ContextPack
from ai.contracts.problem_generation import (
    DifficultyBand,
    GeneratedItem,
    ProblemRequest,
    SolveResult,
)
from ai.contracts.taxonomy import TypeTag

_DATA_ROOT = Path(__file__).resolve().parent / "data"
_DEFAULT_VERIFY_CONFIG_PATH = _DATA_ROOT / "verify_config.yaml"
_DEFAULT_BANNED_TOPICS_PATH = _DATA_ROOT / "pg_banned_topics.yaml"
_NORMALIZE_PATTERN = re.compile(r"[\W_]+", flags=re.UNICODE)


class VerificationConfigError(ValueError):
    """검증 설정 파일을 읽거나 검증할 수 없음."""


class DifficultyRange(BaseModel):
    """난이도 밴드의 폐구간."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    min: float
    max: float

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.max < self.min:
            raise ValueError("난이도 밴드의 max는 min 이상이어야 한다")
        return self

    @property
    def midpoint(self) -> float:
        return (self.min + self.max) / 2

    def contains(self, value: float) -> bool:
        return self.min <= value <= self.max


class T1DifficultyBandMap(BaseModel):
    """M2에서 열린 T1 난이도 밴드만 표현한다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    low: DifficultyRange
    medium: DifficultyRange
    high: DifficultyRange

    @model_validator(mode="after")
    def validate_non_overlapping(self) -> Self:
        if not (self.low.max < self.medium.min and self.medium.max < self.high.min):
            raise ValueError("T1 난이도 밴드는 순서가 고정되고 서로 겹칠 수 없다")
        return self

    def get(self, band: DifficultyBand) -> DifficultyRange:
        return {
            DifficultyBand.LOW: self.low,
            DifficultyBand.MEDIUM: self.medium,
            DifficultyBand.HIGH: self.high,
        }[band]


class PassageWordCountWeights(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    over_700: float
    over_1000: float


class SentenceComplexityWeights(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    advanced: float


class DifficultyWeights(BaseModel):
    """T1 난이도 추정의 코드 소유 외부 값."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base: float
    passage_word_count: PassageWordCountWeights
    sentence_complexity: SentenceComplexityWeights
    type_tag: dict[TypeTag, float]
    low_cross_solve_confidence: float
    multiple_evidence: float

    @model_validator(mode="after")
    def validate_type_weights(self) -> Self:
        if set(self.type_tag) != set(TypeTag):
            raise ValueError("difficulty_weights.type_tag는 공용 TypeTag를 모두 포함해야 한다")
        return self


class VerifyConfig(BaseModel):
    """verify_config.yaml의 실행 계약."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(min_length=1)
    regen_max: int = Field(ge=0)
    transport_retry: int = Field(ge=0, le=1)
    dup_similarity_max: float = Field(ge=0.0, le=1.0)
    cross_confidence_high: float = Field(ge=0.0, le=1.0)
    alignment_confidence_min: float = Field(ge=0.0, le=1.0)
    set_drop_ratio_max: float = Field(ge=0.0, le=1.0)
    verify_outage_streak_max: int = Field(ge=1)
    t1_light_mode: bool
    difficulty_regen_enabled: bool
    difficulty_regen_max: int = Field(ge=0, le=1)
    difficulty_band_tolerance: int = Field(ge=0)
    difficulty_band_map: dict[str, T1DifficultyBandMap]
    diag_relative_cut_pp: float
    diag_decay: float
    diag_propagate_threshold: float
    diag_severity_saturation: float
    diag_suspect_damping: float
    node_min_items: int = Field(ge=1)
    difficulty_weights: DifficultyWeights

    @model_validator(mode="after")
    def validate_m2_scope(self) -> Self:
        if self.regen_max != 2:
            raise ValueError("M2 item_attempt는 최초 1회와 재생성 2회로 고정한다")
        if self.t1_light_mode:
            raise ValueError("M2에서는 t1_light_mode를 활성화할 수 없다")
        if set(self.difficulty_band_map) != {"T1"}:
            raise ValueError("M2 난이도 밴드는 T1만 정의해야 한다")
        return self

    @property
    def item_attempt_limit(self) -> int:
        return self.regen_max + 1

    @property
    def t1_bands(self) -> T1DifficultyBandMap:
        return self.difficulty_band_map["T1"]


class BannedTopicCategory(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    match_terms: tuple[str, ...] = Field(min_length=1)


class BannedTopicsConfig(BaseModel):
    """문항 정적 금칙어와 prompt injection 패턴."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(min_length=1)
    categories: tuple[BannedTopicCategory, ...] = Field(min_length=1)
    prompt_injection_patterns: tuple[str, ...] = Field(min_length=1)

    @property
    def all_terms(self) -> tuple[str, ...]:
        return tuple(
            term
            for category in self.categories
            for term in category.match_terms
        ) + self.prompt_injection_patterns


class RuleValidationResult(BaseModel):
    """게이트 ①의 결정론 결과."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: bool
    verification_available: bool = True
    failed_checks: tuple[str, ...] = ()
    banned_topic: bool = False
    source_unverified: bool = False

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.passed and (
            not self.verification_available
            or self.failed_checks
            or self.banned_topic
            or self.source_unverified
        ):
            raise ValueError("통과 결과에는 실패 상세를 기록할 수 없다")
        if not self.passed and not self.failed_checks:
            raise ValueError("실패 결과에는 failed_checks가 필요하다")
        return self


class CrossValidationResult(BaseModel):
    """게이트 ② 출력에 대한 코드 판정."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: bool
    low_confidence: bool
    failed_checks: tuple[str, ...] = ()


def load_verify_config(path: Path = _DEFAULT_VERIFY_CONFIG_PATH) -> VerifyConfig:
    """버전 관리된 검증 설정을 엄격히 로드한다."""

    return _load_yaml_model(path, VerifyConfig, "검증 설정")


def load_banned_topics(
    path: Path = _DEFAULT_BANNED_TOPICS_PATH,
) -> BannedTopicsConfig:
    """버전 관리된 문항 금칙 설정을 엄격히 로드한다."""

    return _load_yaml_model(path, BannedTopicsConfig, "금칙 설정")


def _load_yaml_model[ModelT: BaseModel](
    path: Path,
    model_cls: type[ModelT],
    label: str,
) -> ModelT:
    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise VerificationConfigError(f"{label} 파일을 읽을 수 없다: {path}") from error
    except yaml.YAMLError as error:
        raise VerificationConfigError(f"{label} YAML 오류: {error}") from error
    try:
        return model_cls.model_validate(raw)
    except ValidationError as error:
        raise VerificationConfigError(f"{label} 스키마 오류: {error}") from error


class RuleValidator:
    """LLM 판정 없이 구조·근거 참조·금칙을 검사한다."""

    def __init__(
        self,
        banned_topics: BannedTopicsConfig,
        *,
        duplicate_similarity_max: float,
    ) -> None:
        if not 0.0 <= duplicate_similarity_max <= 1.0:
            raise ValueError("dup_similarity_max는 0과 1 사이여야 한다")
        self._banned_topics = banned_topics
        self._duplicate_similarity_max = duplicate_similarity_max

    @property
    def banned_topics_version(self) -> str:
        return self._banned_topics.version

    def validate(
        self,
        *,
        item: GeneratedItem,
        request: ProblemRequest,
        type_tag: TypeTag,
        skill_node_id: str,
        context_pack: ContextPack,
        previous_items: tuple[GeneratedItem, ...] = (),
    ) -> RuleValidationResult:
        failed: list[str] = []

        if (
            item.area_tag is not request.area_tag
            or item.type_tag is not type_tag
            or item.item_format is not request.item_format
        ):
            failed.append("R-4:요청_태그_불일치")
        if item.skill_node_id != skill_node_id:
            failed.append("R-7:목표_노드_불일치")

        normalized_choices = tuple(_normalize(choice.text) for choice in item.choices)
        if len(set(normalized_choices)) != len(normalized_choices):
            failed.append("R-2:중복_선지")

        answer_text = next(
            choice.text for choice in item.choices if choice.no == item.answer.correct_no
        )
        if _normalize(answer_text) in _normalize(item.stem):
            failed.append("R-3:문두_정답_노출")

        inspected_text = "\n".join(
            (
                item.stem,
                *(choice.text for choice in item.choices),
                item.rationale,
            )
        ).casefold()
        banned = any(term.casefold() in inspected_text for term in self._banned_topics.all_terms)
        if banned:
            failed.append("R-5:금칙_오염")

        allowed_refs = _allowed_evidence_refs(context_pack)
        if allowed_refs is None or not allowed_refs:
            failed.append("R-1:기준_자료_없음")
            return RuleValidationResult(
                passed=False,
                verification_available=False,
                failed_checks=tuple(failed),
                banned_topic=banned,
                source_unverified=True,
            )

        evidence_refs = {anchor.ref for anchor in item.evidence}
        source_unverified = not evidence_refs.issubset(allowed_refs)
        if source_unverified:
            failed.append("R-1:근거_참조_불일치")

        normalized_stem = _normalize(item.stem)
        if any(
            normalized_stem == _normalize(previous.stem)
            or _ngram_similarity(normalized_stem, _normalize(previous.stem))
            > self._duplicate_similarity_max
            for previous in previous_items
        ):
            failed.append("R-6:기출제_문항_중복")

        return RuleValidationResult(
            passed=not failed,
            failed_checks=tuple(failed),
            banned_topic=banned,
            source_unverified=source_unverified,
        )


def validate_cross_solve(
    item: GeneratedItem,
    solve: SolveResult,
    config: VerifyConfig,
) -> CrossValidationResult:
    """교차 풀이 일치·복수정답·의미 정렬을 코드로 확정한다."""

    failed: list[str] = []
    if solve.chosen != item.answer.correct_no:
        failed.append("C-1:정답_불일치")
    if solve.multiple_answers_possible:
        failed.append("C-2:복수_정답_가능")
    if not solve.aligned:
        failed.append("C-3:목표_의미_불일치")

    low_confidence = (
        solve.confidence < config.cross_confidence_high
        or solve.alignment_confidence < config.alignment_confidence_min
    )
    return CrossValidationResult(
        passed=not failed,
        low_confidence=low_confidence,
        failed_checks=tuple(failed),
    )


def estimate_t1_difficulty(
    *,
    item: GeneratedItem,
    solve: SolveResult,
    config: VerifyConfig,
) -> float:
    """버전 설정의 가중치만 사용해 T1 난이도를 결정론적으로 추정한다."""

    weights = config.difficulty_weights
    estimate = weights.base + weights.type_tag[item.type_tag]
    if solve.confidence < config.cross_confidence_high:
        estimate += weights.low_cross_solve_confidence
    if len(item.evidence) > 1:
        estimate += weights.multiple_evidence
    return round(estimate, 4)


def classify_t1_difficulty(value: float, config: VerifyConfig) -> DifficultyBand:
    """구간 밖 값도 가장 가까운 중앙값으로 결정론 분류한다."""

    for band in DifficultyBand:
        if config.t1_bands.get(band).contains(value):
            return band
    return min(
        DifficultyBand,
        key=lambda band: (
            abs(value - config.t1_bands.get(band).midpoint),
            _band_index(band),
        ),
    )


def needs_difficulty_regeneration(
    actual: DifficultyBand,
    requested: DifficultyBand | None,
    config: VerifyConfig,
) -> bool:
    if requested is None:
        return False
    return abs(_band_index(actual) - _band_index(requested)) > config.difficulty_band_tolerance


def distance_to_requested_midpoint(
    value: float,
    requested: DifficultyBand,
    config: VerifyConfig,
) -> float:
    return abs(value - config.t1_bands.get(requested).midpoint)


def has_reference_data(context_pack: ContextPack) -> bool:
    """R-1을 수행할 승인 근거 참조가 ContextPack에 있는지 확인한다."""

    allowed_refs = _allowed_evidence_refs(context_pack)
    return bool(allowed_refs)


def _band_index(band: DifficultyBand) -> int:
    return {
        DifficultyBand.LOW: 0,
        DifficultyBand.MEDIUM: 1,
        DifficultyBand.HIGH: 2,
    }[band]


def _allowed_evidence_refs(context_pack: ContextPack) -> frozenset[str] | None:
    raw = context_pack.retrieval_trace.get("allowed_evidence_refs")
    if not isinstance(raw, list):
        return None
    values: list[str] = []
    for value in raw:
        if not isinstance(value, str):
            return None
        values.append(value)
    return frozenset(values)


def _normalize(value: str) -> str:
    return _NORMALIZE_PATTERN.sub("", value).casefold()


def _ngram_similarity(left: str, right: str, size: int = 3) -> float:
    left_ngrams = _ngrams(left, size)
    right_ngrams = _ngrams(right, size)
    if not left_ngrams and not right_ngrams:
        return 1.0
    union = left_ngrams | right_ngrams
    return len(left_ngrams & right_ngrams) / len(union) if union else 0.0


def _ngrams(value: str, size: int) -> frozenset[str]:
    if len(value) < size:
        return frozenset({value}) if value else frozenset()
    return frozenset(value[index : index + size] for index in range(len(value) - size + 1))


__all__ = [
    "BannedTopicsConfig",
    "CrossValidationResult",
    "DifficultyRange",
    "RuleValidationResult",
    "RuleValidator",
    "T1DifficultyBandMap",
    "VerificationConfigError",
    "VerifyConfig",
    "classify_t1_difficulty",
    "distance_to_requested_midpoint",
    "estimate_t1_difficulty",
    "has_reference_data",
    "load_banned_topics",
    "load_verify_config",
    "needs_difficulty_regeneration",
    "validate_cross_solve",
]
