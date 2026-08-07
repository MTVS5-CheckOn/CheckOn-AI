"""검증 규칙의 파라미터 스키마 — 순수 값 객체. I/O 없음."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai.contracts.problem_generation import DifficultyBand
from ai.contracts.taxonomy import V1_TYPE_TAGS, TypeTag


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


class ReservedTypeTagWeight(ValueError):
    """예약 태그의 난이도 가중치를 조회했다 — v1엔 그 값이 없다 (99 ㊣).

    ⚠ **`LookupError`·`KeyError` 계열로 만들지 마라.** 같은 파트에 그걸 삼키는 절이 셋이다
    (`application/workflow.py`의 `_existing_result` · `infrastructure/memory_store.py`의
    두 `get`). 폴백을 거부해 놓고 예외를 **삼켜지는 종류**로 만들면 같은 실수를 한 층
    아래에서 반복한다.
    """


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
        # 🔴 **예약 태그(`RESERVED_TYPE_TAGS`)는 가중치를 갖지 않는다**(99 ㊣). 여는 날
        #    `V1_TYPE_TAGS`가 자동으로 넓어지고 이 검사가 **가중치 누락을 red로 알려 준다**
        #    — 가드가 죽는 게 아니라 **기준선이 옮겨간다.**
        if set(self.type_tag) != set(V1_TYPE_TAGS):
            raise ValueError(
                "difficulty_weights.type_tag는 v1 TypeTag를 모두 포함해야 한다 "
                "— 예약 태그는 가중치를 갖지 않는다"
            )
        return self

    def weight_for(self, tag: TypeTag) -> float:
        """유형 가중치를 조회한다 — **누락은 예외이지 0이 아니다.**

        🔴 **위 완전성 검사와 이 조회가 같은 클래스에 산다.** 검사를 좁히는 사람이 그 검사에
        기대던 자리를 찾아다니지 않아도 되게 하려는 것이다 — 규율로 남기면 다음 사람이
        기억해야 하고, 구조로 두면 기억할 필요가 없다(99 ㊣).

        ⚠ `.get(tag, 0.0)`을 쓰지 마라. `verify_config.yaml`에 `apply: 0.0`을 미리 넣는 것을
        기각한 것과 같은 이유다 — 가산 0은 *"적용·창의는 난이도 가산이 없다"* 는 **없는
        사실**이고, 조용히 통과해 그 문항이 난이도까지 달고 나간다. 🔴 폴백이 있는 룩업은
        누락이 예외가 아니라 **오염**이다(#134 블로커 ② 실증).

        ⚠ **도달 불가 방어다** — 요청 문(`enqueue.reject_unsupported_type_tags`)이 예약
        태그를 400으로 끊으므로, 여기 도달했다는 것은 그 문이 뚫렸다는 뜻이다. 그래서 4xx로
        올리지 않는다(BE에게 고칠 것 없는 요청을 고치라고 말하게 된다).
        """

        try:
            return self.type_tag[tag]
        except KeyError as error:
            raise ReservedTypeTagWeight(
                f"예약 태그 {tag.value}에는 난이도 가중치가 없다 "
                "— v1 산출 축이 아니다(요청 문에서 걸러졌어야 한다)"
            ) from error


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
