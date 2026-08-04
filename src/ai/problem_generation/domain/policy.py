"""검증 규칙의 파라미터 스키마 — 순수 값 객체. I/O 없음."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai.contracts.problem_generation import DifficultyBand
from ai.contracts.taxonomy import TypeTag


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
            # T2~T5 밴드 경계는 06 부록 'B 기본값 시트' 사안이며 아직 값이 없다.
            # 트랙 개방(05 §1.2)과 함께 키를 늘린다 — 값 없이 키만 여는 것을 막는다.
            raise ValueError(
                "M2 난이도 밴드는 T1(문법)만 정의해야 한다 — T2~T5 경계는 06 부록 미확정"
            )
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
