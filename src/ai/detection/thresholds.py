"""R1~R6 임계값 설정 구조 — 버전 있는 Pydantic 설정.

사양 원본: docs/part_a/04_threshold_config.md §1(파라미터 정본)·§2(세그먼트)·§3·§3.1(정규화).
소유: 박진희 (detection — 02_ownership.md §5).

임계값의 종착지는 DB THRESHOLD_CONFIG 테이블(04 §1 머리말)이다. 이번 단계는 순수 함수라
DB 없이 **주입할 v0 기본값**만 이 모델로 모은다 — 나중에 DB/로더가 같은 모델로 주입한다.
값을 코드에 흩어 쓰지 않고 여기 한 곳에 모으는 것이 목적이며(03_coding_rules.md §1),
**기본값의 원본은 04 §1 표다** — 여기 숫자를 임의로 바꾸면 골든셋 기대값이 깨진다.
"""

from pydantic import BaseModel, ConfigDict, Field


class R1Params(BaseModel):
    """R1 정답률 하락 — 04 §1."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    drop_pp: float = 15.0
    """베이스라인 대비 하락 %p 임계 (−15%p 이상)."""

    consecutive_weeks: int = 2
    saturation_drop_pp: float = 25.0
    """score 포화값 — 04 §3.1 (보정 상한 25)."""


class R2Params(BaseModel):
    """R2 제출·성실도 하락 — 04 §1."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    submit_drop_pp: float = 25.0
    consecutive_missing: int = 3
    saturation_drop_pp: float = 40.0
    saturation_missing: int = 5


class R3Params(BaseModel):
    """R3 학습 공백 — 04 §1."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    volume_ratio: float = 0.4
    """베이스라인 학습량의 40% 미만."""

    min_baseline_events: int = 10
    """최소 모수 조건 — 원래 적게 하던 학생 오탐 방지."""


class R4Params(BaseModel):
    """R4 숨은 위기(★핵심) — 정답률 유지 + 어절 정규화 시간 급증. 04 §1."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    acc_stable_band_pp: float = 5.0
    """정답률 유지 판정 폭 (±5%p 이내)."""

    time_ratio: float = 1.5
    """어절 정규화 시간(duration_sec / passage_word_count)의 베이스라인 배율."""

    consecutive_weeks: int = 2
    saturation_time_ratio: float = 2.0
    """score 포화값 — 04 §3.1 (보정 상한 2.0)."""


class R5Params(BaseModel):
    """R5 복귀 케어 — status=returned 복귀 첫 주. 04 §1."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    care_window_weeks: int = 2
    """auto_flag는 항상 true(점수 무관)이므로 파라미터로 두지 않는다."""


class R6Params(BaseModel):
    """R6 오답 유형 편중 — area_tag × type_tag 24셀. 04 §1·§1 R6 유형 축."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cell_error_share: float = 0.5
    """전체 오답의 50% 이상이 한 셀에 집중."""

    cell_min_items: int = 10
    """셀 최소 문항 수 — 비율만으로 오탐 방지."""

    cell_acc_below: float = 0.5
    """셀 절대 정답률 조건."""

    tagging_rate_min: float = 0.6
    """주간 태깅률이 60% 미만이면 R6 미적용 + 사유 기록."""

    saturation_error_share: float = 0.6
    """score 포화값 — 04 §3.1 (보정 상한 0.6)."""


class SegmentCoefficients(BaseModel):
    """세그먼트별 보수화 계수 — 04 §2."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    readapt_relax: float = 1.3
    """readapt(복귀 후 4주): R1·R2·R3 임계 ×1.3 완화."""

    readapt_window_weeks: int = 4
    new_term_relax: float = 1.2
    """new_term(신학기 첫 2주): 전 규칙 임계 ×1.2."""


class ThresholdConfig(BaseModel):
    """R1~R6 + 상한 + 세그먼트 묶음 — THRESHOLD_CONFIG 1버전에 대응.

    version·source는 04 §1 머리말의 DB 컬럼. meta.versions.threshold에 version을 싣는다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = 1
    source: str = "default"
    baseline_window_weeks: int = 8
    """개인 베이스라인 이동 창 — 04 §1 (반 평균 아님)."""

    cap_min: int = 3
    cap_max: int = 5
    """반·일 TOP 3~5 — 04 §3."""

    r1: R1Params = Field(default_factory=R1Params)
    r2: R2Params = Field(default_factory=R2Params)
    r3: R3Params = Field(default_factory=R3Params)
    r4: R4Params = Field(default_factory=R4Params)
    r5: R5Params = Field(default_factory=R5Params)
    r6: R6Params = Field(default_factory=R6Params)
    segments: SegmentCoefficients = Field(default_factory=SegmentCoefficients)

    @property
    def threshold_version(self) -> str:
        """meta.versions.threshold 값 — source와 version의 결합."""
        return f"{self.source}-v{self.version}"


def default_threshold_config() -> ThresholdConfig:
    """04 §1 표 v0 기본값 (source=default, version=1).

    값의 원본은 04 문서다 — 이 팩토리는 문서 값을 코드로 옮긴 것이며, 일치는
    tests/ai/unit/detection/test_thresholds_match_doc.py가 고정한다.
    """
    return ThresholdConfig()
