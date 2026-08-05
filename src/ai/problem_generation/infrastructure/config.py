"""검증 설정 yaml 로딩 — 유일한 파일 I/O 경계."""

from __future__ import annotations

from pathlib import Path

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ValidationError

from ai.problem_generation.domain.policy import BannedTopicsConfig, VerifyConfig

_DATA_ROOT = Path(__file__).resolve().parents[1] / "data"
_DEFAULT_VERIFY_CONFIG_PATH = _DATA_ROOT / "verify_config.yaml"
_DEFAULT_BANNED_TOPICS_PATH = _DATA_ROOT / "pg_banned_topics.yaml"

#: R-1 문법 대조 정본 — 패키지에 동봉한다. 절대 경로·외부 마운트를 쓰지 않는다.
#: 자료가 없으면 R-1 대조가 불가능해 T1 전체와 T2 어휘 문항이 발행 차단되므로
#: (05 §1.1), 배포 환경마다 경로가 달라지는 구조를 두면 서비스가 서지 않는다.
#: 버전 폴더명이 곧 GRAMMAR_NORM_VERSION이며 자료 개정 시 폴더를 새로 만든다.
GRAMMAR_NORM_ROOT = _DATA_ROOT / "grammar_norm"
DEFAULT_GRAMMAR_NORM_VERSION = "nikl-kornorms-20250902"


def grammar_norm_dir(version: str = DEFAULT_GRAMMAR_NORM_VERSION) -> Path:
    """어문 규범 자료 버전 디렉터리 — 없으면 즉시 실패한다(fail-closed).

    조용히 빈 경로를 돌려주면 R-1이 대조 없이 통과하는 경로가 생긴다.
    """
    path = GRAMMAR_NORM_ROOT / version
    if not path.is_dir():
        raise VerificationConfigError(
            f"어문 규범 자료를 찾을 수 없다: {path} — "
            "패키지 동봉본이 누락됐다(05 §1.1.1)."
        )
    return path


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
