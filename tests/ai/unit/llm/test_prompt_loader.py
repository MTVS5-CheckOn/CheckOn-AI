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


def test_problem_generation_registry_has_six_versioned_prompts() -> None:
    registry = load_prompt_registry(REGISTRY_PATH)

    # ⚠ registry에는 pg 외 프롬프트도 산다(classify 등) — **pg.* 만** 본다.
    assert {
        entry.prompt_id
        for entry in registry.prompts
        if entry.prompt_id.startswith("pg.")
    } == {
        "pg.passage.v1",
        "pg.source_material.v1",
        "pg.items.v1",
        "pg.cross_solve.v1",
        "pg.misconception_check.v1",
        "pg.refine.v1",
    }
    assert registry.get("pg.cross_solve.v1").role is ModelRole.VERIFIER
    assert registry.get("pg.misconception_check.v1").role is ModelRole.VERIFIER
    assert registry.get("pg.refine.v1").role is ModelRole.GENERATOR


def test_items_prompt_forbids_person_names_and_promotes_active_pair() -> None:
    registry = load_prompt_registry(REGISTRY_PATH)
    template = load_prompt_template("pg.items.v1", REGISTRY_PATH, TEMPLATES_ROOT)

    assert "예문·선지·지문에 사람 이름을 쓰지 않는다" in template.content
    assert "인명 대신 '갑·을', '가·나' 또는 사물·역할 명사를 쓴다" in template.content
    # ⚠ 이 규칙은 **인명만** 막는다 — 국어 문법 어휘가 성씨로 읽히는 오탐은 프롬프트가
    #   아니라 `redaction_patterns.yaml` 의 `name_exclude` 가 막는다(99 #178 · 8/27 실측).
    #   두 자리를 헷갈리면 «규칙을 넣었는데 또 막힌다» 가 반복된다.
    assert "검증 단계에서 폐기된다" in template.content
    # 🔴 v8 — 생성 문항 인명 금지 명시(v7 은 v1 발문을 긍정형으로 제한).
    #    프롬프트 문면이 바뀌면 버전이 바뀐다(불변식 8).
    assert template.version == "v8"
    assert "영역 출제 규격" in template.content
    assert "발문 정형 중 하나를 따른다" in template.content
    assert "v1은 부정형 발문" in template.content
    assert "모든 오답 선지에\n    오개념 라벨" in template.content
    # 짝이다 — workflow가 두 버전이 다르면 기동에서 거부한다.
    assert registry.get("pg.cross_solve.v1").version == "v8"
    assert registry.get("pg.misconception_check.v1").version == "v8"


@pytest.mark.parametrize(
    ("prompt_id", "expected_variables"),
    [
        (
            "pg.passage.v1",
            {"context_pack_json", "passage_request_json", "banned_topics_json"},
        ),
        (
            "pg.source_material.v1",
            {
                "area_spec_block",
                "context_pack_json",
                "source_request_json",
                "banned_topics_json",
            },
        ),
        (
            "pg.items.v1",
            {
                "area_spec_block",
                "context_pack_json",
                "generation_input_json",
                "retry_context_json",
                "response_schema_json",
            },
        ),
        (
            "pg.cross_solve.v1",
            {"blind_item_json", "target_metadata_json"},
        ),
        (
            "pg.misconception_check.v1",
            {"misconception_payload_json"},
        ),
        (
            "pg.refine.v1",
            {
                "area_spec_block",
                "context_pack_json",
                "current_item_json",
                "instruction_json",
                "response_schema_json",
            },
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
