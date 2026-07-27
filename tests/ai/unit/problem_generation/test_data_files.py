"""문제출제 verify_config·금칙 사전 실파일 회귀."""

from pathlib import Path

import yaml  # type: ignore[import-untyped]  # PyYAML은 공식 타입 정보를 제공하지 않는다.

DATA_ROOT = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "ai"
    / "problem_generation"
    / "data"
)


def _load_yaml(name: str) -> dict[str, object]:
    raw = yaml.safe_load((DATA_ROOT / name).read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def test_verify_config_matches_m2_pilot_switches() -> None:
    config = _load_yaml("verify_config.yaml")

    assert config["version"] == "verify-config.v1"
    assert config["regen_max"] == 2
    assert config["transport_retry"] == 1
    assert config["t1_light_mode"] is False
    assert config["difficulty_regen_enabled"] is False
    assert config["difficulty_regen_max"] == 1
    assert config["difficulty_band_tolerance"] == 1


def test_verify_config_has_only_t1_difficulty_bands() -> None:
    config = _load_yaml("verify_config.yaml")
    band_map = config["difficulty_band_map"]

    assert band_map == {
        "T1": {
            "low": {"min": 1.0, "max": 1.5},
            "medium": {"min": 2.0, "max": 2.5},
            "high": {"min": 3.0, "max": 3.5},
        }
    }


def test_verify_config_preserves_quality_gate_defaults() -> None:
    config = _load_yaml("verify_config.yaml")

    assert config["dup_similarity_max"] == 0.8
    assert config["cross_confidence_high"] == 0.8
    assert config["alignment_confidence_min"] == 0.7
    assert config["set_drop_ratio_max"] == 0.3
    assert config["verify_outage_streak_max"] == 3


def test_banned_topics_covers_required_categories_and_injection_patterns() -> None:
    config = _load_yaml("pg_banned_topics.yaml")
    categories = config["categories"]

    assert isinstance(categories, list)
    assert {category["id"] for category in categories} == {
        "political_religious_current_dispute",
        "real_person_evaluation",
        "self_harm_or_graphic_violence",
        "discriminatory_content",
    }
    assert config["prompt_injection_patterns"] == [
        "이전 지시 무시",
        "시스템 프롬프트 공개",
        "숨겨진 지시 출력",
        "금칙어 우회",
    ]
