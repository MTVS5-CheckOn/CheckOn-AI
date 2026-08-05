"""문제출제 프롬프트 registry·템플릿 로더 검증."""

from pathlib import Path

import pytest

from ai.contracts.llm import ModelRole
from ai.llm.prompts.loader import (
    PromptLoadError,
    PromptNotFoundError,
    PromptRenderError,
    load_prompt_registry,
    load_prompt_template,
)

PROMPTS_ROOT = (
    Path(__file__).resolve().parents[4] / "src" / "ai" / "llm" / "prompts"
)
REGISTRY_PATH = PROMPTS_ROOT / "registry.yaml"
TEMPLATES_ROOT = PROMPTS_ROOT / "templates"


def test_problem_generation_registry_has_three_versioned_prompts() -> None:
    registry = load_prompt_registry(REGISTRY_PATH)

    # ⚠ registry에는 pg 외 프롬프트도 산다(classify 등) — **pg.* 만** 본다.
    assert {
        entry.prompt_id
        for entry in registry.prompts
        if entry.prompt_id.startswith("pg.")
    } == {
        "pg.passage.v1",
        "pg.items.v1",
        "pg.cross_solve.v1",
    }
    assert registry.get("pg.cross_solve.v1").role is ModelRole.VERIFIER


@pytest.mark.parametrize(
    ("prompt_id", "expected_variables"),
    [
        (
            "pg.passage.v1",
            {"context_pack_json", "passage_request_json", "banned_topics_json"},
        ),
        (
            "pg.items.v1",
            {"context_pack_json", "generation_input_json", "retry_context_json"},
        ),
        (
            "pg.cross_solve.v1",
            {"blind_item_json", "target_metadata_json"},
        ),
    ],
)
def test_prompt_templates_load_with_expected_structured_slots(
    prompt_id: str,
    expected_variables: set[str],
) -> None:
    template = load_prompt_template(prompt_id, REGISTRY_PATH, TEMPLATES_ROOT)

    assert set(template.variables) == expected_variables
    assert template.content


def test_prompt_template_renders_only_registered_variables() -> None:
    template = load_prompt_template(
        "pg.cross_solve.v1",
        REGISTRY_PATH,
        TEMPLATES_ROOT,
    )

    rendered = template.render(
        {
            "blind_item_json": '{"stem":"문항","choices":[]}',
            "target_metadata_json": '{"area_tag":"language"}',
        }
    )

    assert '{"stem":"문항","choices":[]}' in rendered
    assert '{"area_tag":"language"}' in rendered


def test_prompt_template_rejects_missing_variable() -> None:
    template = load_prompt_template(
        "pg.cross_solve.v1",
        REGISTRY_PATH,
        TEMPLATES_ROOT,
    )

    with pytest.raises(PromptRenderError, match="누락"):
        template.render({"blind_item_json": "{}"})


def test_prompt_registry_rejects_unknown_prompt_id() -> None:
    registry = load_prompt_registry(REGISTRY_PATH)

    with pytest.raises(PromptNotFoundError, match="등록되지 않은"):
        registry.get("pg.unknown.v1")


def test_prompt_registry_rejects_template_path_traversal(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(
        """
schema_version: prompt-registry.v1
prompts:
  - prompt_id: pg.invalid.v1
    version: v1
    role: generator
    template: ../outside.txt
    response_schema_name: Invalid
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(PromptLoadError, match="상대 .txt 경로"):
        load_prompt_registry(registry_path)


def test_prompt_loader_rejects_missing_template(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(
        """
schema_version: prompt-registry.v1
prompts:
  - prompt_id: pg.missing.v1
    version: v1
    role: generator
    template: problem_generation/missing.txt
    response_schema_name: Missing
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(PromptLoadError, match="읽을 수 없다"):
        load_prompt_template("pg.missing.v1", registry_path, tmp_path / "templates")
