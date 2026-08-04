"""검증 설정 yaml 로딩 — 유일한 파일 I/O 경계."""

from __future__ import annotations

from pathlib import Path

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ValidationError

from ai.problem_generation.domain.policy import BannedTopicsConfig, VerifyConfig

_DATA_ROOT = Path(__file__).resolve().parents[1] / "data"
_DEFAULT_VERIFY_CONFIG_PATH = _DATA_ROOT / "verify_config.yaml"
_DEFAULT_BANNED_TOPICS_PATH = _DATA_ROOT / "pg_banned_topics.yaml"


class VerificationConfigError(ValueError):
    """검증 설정 파일을 읽거나 검증할 수 없음."""


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
