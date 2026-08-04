"""T1 난이도 추정·밴드 분류 — 결정론 산식."""

from __future__ import annotations

from ai.contracts.problem_generation import (
    DifficultyBand,
    GeneratedItem,
    SolveResult,
)
from ai.problem_generation.domain.policy import VerifyConfig


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



def _band_index(band: DifficultyBand) -> int:
    return {
        DifficultyBand.LOW: 0,
        DifficultyBand.MEDIUM: 1,
        DifficultyBand.HIGH: 2,
    }[band]
