"""검증 규칙의 파라미터 스키마 — 순수 값 객체. I/O 없음."""

from __future__ import annotations

from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai.contracts.problem_generation import DifficultyBand
from ai.contracts.taxonomy import V1_TYPE_TAGS, AreaTag, TypeTag

#: 자료 조달 능력이 구현된 영역만 명시적으로 연다. 새 영역은 기본 미지원이어야 하므로
#: `V1_TYPE_TAGS`처럼 전체에서 예약값을 빼지 않는다 — 자료 조달 노드가 준비된 영역을 이
#: 집합에 더해야만 열리는 fail-closed 정책이다.
SUPPORTED_AREAS: Final[frozenset[AreaTag]] = frozenset(
    {AreaTag.LANGUAGE, AreaTag.READING}
)


def supports_source_procurement(
    *,
    area_tag: AreaTag,
    has_passage_request: bool,
) -> bool:
    """현재 구현이 자료를 조달할 수 있는 영역·요청 형태인지 반환한다."""

    return area_tag in SUPPORTED_AREAS and (
        (area_tag is AreaTag.READING) == has_passage_request
    )


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


class AreaSpec(BaseModel):
    """영역 하나의 출제 규격 — `data/area_specs.yaml` 한 항목과 1:1.

    🔴 **문항 품질이 여기서 갈린다.** 종전 프롬프트는 `area_tag`를 *"그대로 보존한다"* 고만
    지시해서, 모델이 **그 영역이 무엇을 묻는 영역인지 모른 채** 문항을 만들었다. 그러면
    영역과 무관하게 「지문 읽고 고르기」가 나온다 — 수능 국어는 영역마다 발문·자료·선지의
    형태가 다르고 그 차이가 문항의 값이다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    label_ko: str = Field(min_length=1)
    """강사 화면·출제 프롬프트가 쓰는 **정식 라벨**(정본).

    ⚠ R6 브리핑의 짧은 라벨(`briefing_context._AREA_KO`)과 **다른 축**이다 — 그쪽은
    `"영역·유형"` 조립이라 두 글자여야 한다.
    """

    measures: str = Field(min_length=1)
    """그 영역이 무엇을 측정하는가 — 발문이 향할 대상."""

    stem_forms: tuple[str, ...] = Field(min_length=1)
    """수능 발문 정형. 모델이 스스로 틀을 만들면 모의고사처럼 안 읽힌다."""

    distractors: str = Field(min_length=1)
    """매력적 오답이 만들어지는 방식.

    🔴 없으면 오답이 **명백히 틀린 문장**이 되어 변별력이 0이 된다 — 게이트는 그걸 못
    잡는다(규칙 위반도 근거 미실존도 아니라서 전부 통과한다).
    """

    avoid: str = Field(min_length=1)
    """그 영역에서 반복적으로 나오는 실패 형태."""


class AreaSpecs(BaseModel):
    """영역별 출제 규격 전체 — `AreaTag` 전 항목을 덮어야 한다.

    ⚠ **누락은 `KeyError`가 아니라 「규격 없는 생성」으로 조용히 빠진다.** 그래서
    로딩 시점에 fail-closed로 막는다 — 영역이 늘면 이 파일을 같이 고치게 된다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(min_length=1)
    areas: dict[AreaTag, AreaSpec]

    @model_validator(mode="after")
    def validate_every_area_is_covered(self) -> Self:
        missing = sorted(tag.value for tag in AreaTag if tag not in self.areas)
        if missing:
            raise ValueError(
                f"출제 규격이 없는 영역: {missing} — "
                "영역을 늘렸으면 area_specs.yaml에 규격을 같이 적어야 한다"
            )
        return self

    def spec_for(self, area: AreaTag) -> AreaSpec:
        """규격을 꺼낸다 — 검증이 전수를 보장하므로 여기서 `KeyError`는 안 난다."""
        return self.areas[area]
