"""오개념 닫힌 어휘 데이터와 로더의 불변식을 검증한다."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError

from ai.contracts.taxonomy import AreaTag
from ai.problem_generation.domain.policy import MisconceptionTagsConfig
from ai.problem_generation.infrastructure.config import (
    VerificationConfigError,
    load_misconception_tags,
)

_TAGS_PATH = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "ai"
    / "problem_generation"
    / "data"
    / "misconception_tags.yaml"
)


def test_misconception_tags_match_the_strict_schema() -> None:
    config = load_misconception_tags()

    assert config.version
    assert all(
        3 <= len(tags) <= 6
        for area, tags in config.areas.items()
        if area is not AreaTag.LANGUAGE
    )
    assert all(
        tag.id and tag.label_ko and tag.description
        for tags in config.areas.values()
        for tag in tags
    )


def test_language_loader_exposes_all_thirteen_misconception_tags() -> None:
    tags = load_misconception_tags().tags_for(AreaTag.LANGUAGE)

    assert len(tags) == 13


def test_existing_language_misconception_tag_ids_are_unchanged() -> None:
    tags = load_misconception_tags().tags_for(AreaTag.LANGUAGE)

    assert tuple(tag.id for tag in tags[:3]) == (
        "application_target_substitution",
        "adjacent_change_type_confusion",
        "process_order_reversal",
    )


def test_all_five_areas_have_misconception_tags() -> None:
    config = load_misconception_tags()

    assert len(config.areas) == 5
    assert set(config.areas) == set(AreaTag)


def test_misconception_tag_ids_are_globally_unique() -> None:
    config = load_misconception_tags()
    tag_ids = [tag.id for tags in config.areas.values() for tag in tags]

    assert len(tag_ids) == len(set(tag_ids))


def test_duplicate_misconception_tag_id_fails_closed() -> None:
    raw = yaml.safe_load(_TAGS_PATH.read_text(encoding="utf-8"))
    raw["areas"][AreaTag.MEDIA.value][0]["id"] = raw["areas"][AreaTag.LANGUAGE.value][0]["id"]

    with pytest.raises(ValidationError, match="중복된 오개념 라벨 id"):
        MisconceptionTagsConfig.model_validate(raw)


def test_misconception_tag_area_keys_must_match_area_tag() -> None:
    raw = yaml.safe_load(_TAGS_PATH.read_text(encoding="utf-8"))
    raw["areas"].pop(AreaTag.READING.value)

    with pytest.raises(ValidationError, match="오개념 어휘가 없는 영역"):
        MisconceptionTagsConfig.model_validate(raw)


def test_broken_misconception_tag_file_fails_at_load() -> None:
    with pytest.raises(VerificationConfigError):
        load_misconception_tags(_TAGS_PATH.parent / "does_not_exist.yaml")
