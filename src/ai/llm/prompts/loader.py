"""버전 고정 프롬프트 registry와 템플릿 로더."""

from functools import lru_cache
from pathlib import Path, PurePosixPath
from string import Template
from typing import Literal, Self

import yaml  # type: ignore[import-untyped]  # PyYAML은 공식 타입 정보를 제공하지 않는다.
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ai.contracts.llm import ModelRole

_PROMPTS_ROOT = Path(__file__).resolve().parent
_DEFAULT_REGISTRY_PATH = _PROMPTS_ROOT / "registry.yaml"
_DEFAULT_TEMPLATES_ROOT = _PROMPTS_ROOT / "templates"


class PromptLoadError(ValueError):
    """프롬프트 registry 또는 템플릿을 안전하게 읽을 수 없음."""


class PromptNotFoundError(PromptLoadError):
    """요청한 prompt_id가 registry에 없음."""


class PromptRenderError(PromptLoadError):
    """템플릿 변수 계약이 지켜지지 않음."""


class PromptRegistryEntry(BaseModel):
    """registry.yaml의 프롬프트 한 행."""

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    prompt_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    role: ModelRole
    template: str = Field(min_length=1)
    response_schema_name: str = Field(min_length=1)

    @field_validator("template")
    @classmethod
    def validate_template_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or path.suffix != ".txt":
            raise ValueError("template은 templates/ 아래의 상대 .txt 경로여야 한다")
        return value


class PromptRegistry(BaseModel):
    """프롬프트 ID·버전·역할·템플릿 파일의 정본."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["prompt-registry.v1"] = "prompt-registry.v1"
    prompts: tuple[PromptRegistryEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_prompt_ids(self) -> Self:
        prompt_ids = [entry.prompt_id for entry in self.prompts]
        duplicates = sorted(
            prompt_id
            for prompt_id in set(prompt_ids)
            if prompt_ids.count(prompt_id) > 1
        )
        if duplicates:
            raise ValueError(f"중복 prompt_id: {', '.join(duplicates)}")
        return self

    def get(self, prompt_id: str) -> PromptRegistryEntry:
        for entry in self.prompts:
            if entry.prompt_id == prompt_id:
                return entry
        raise PromptNotFoundError(f"등록되지 않은 prompt_id: {prompt_id}")


class LoadedPromptTemplate(BaseModel):
    """검증된 registry 메타와 템플릿 본문."""

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    prompt_id: str
    version: str
    role: ModelRole
    response_schema_name: str
    content: str = Field(min_length=1)
    variables: tuple[str, ...]

    def render(self, values: dict[str, str]) -> str:
        expected = set(self.variables)
        provided = set(values)
        missing = sorted(expected - provided)
        unexpected = sorted(provided - expected)
        if missing:
            raise PromptRenderError(f"누락된 프롬프트 변수: {', '.join(missing)}")
        if unexpected:
            raise PromptRenderError(
                f"등록되지 않은 프롬프트 변수: {', '.join(unexpected)}"
            )
        return Template(self.content).substitute(values)


@lru_cache
def load_prompt_registry(
    registry_path: Path = _DEFAULT_REGISTRY_PATH,
) -> PromptRegistry:
    """YAML registry를 읽고 스키마·중복 ID를 검증한다."""

    try:
        raw: object = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise PromptLoadError(f"프롬프트 registry를 읽을 수 없다: {registry_path}") from error
    except yaml.YAMLError as error:
        raise PromptLoadError(f"프롬프트 registry YAML 오류: {error}") from error

    try:
        return PromptRegistry.model_validate(raw)
    except ValidationError as error:
        raise PromptLoadError(f"프롬프트 registry 스키마 오류: {error}") from error


@lru_cache
def load_prompt_template(
    prompt_id: str,
    registry_path: Path = _DEFAULT_REGISTRY_PATH,
    templates_root: Path = _DEFAULT_TEMPLATES_ROOT,
) -> LoadedPromptTemplate:
    """prompt_id에 대응하는 템플릿을 경로 이탈 없이 읽는다."""

    entry = load_prompt_registry(registry_path).get(prompt_id)
    root = templates_root.resolve()
    template_path = (root / PurePosixPath(entry.template)).resolve()
    if not template_path.is_relative_to(root):
        raise PromptLoadError("프롬프트 템플릿 경로가 templates/ 밖을 가리킨다")

    try:
        content = template_path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise PromptLoadError(
            f"프롬프트 템플릿을 읽을 수 없다: {entry.template}"
        ) from error
    if not content:
        raise PromptLoadError(f"프롬프트 템플릿이 비어 있다: {entry.template}")

    template = Template(content)
    try:
        variables = tuple(sorted(template.get_identifiers()))
    except ValueError as error:
        raise PromptLoadError(
            f"프롬프트 템플릿 변수 문법 오류: {entry.template}"
        ) from error
    return LoadedPromptTemplate(
        prompt_id=entry.prompt_id,
        version=entry.version,
        role=entry.role,
        response_schema_name=entry.response_schema_name,
        content=content,
        variables=variables,
    )
