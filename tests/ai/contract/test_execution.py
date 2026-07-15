"""contracts/execution.py 스모크 — AI_RUN 1:1 + 왕복 직렬화.

핵심은 RunMetadata의 필드 집합이 docs/06_erd.md의 AI_RUN 컬럼과 1:1이라는 것.
어긋나면 재현성(CLAUDE.md 불변식 8)이 깨지므로 ERD와 함께 고쳐야 한다.
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from ai.contracts.execution import (
    Capability,
    ExecutionContext,
    GenerationParams,
    RunMetadata,
    VersionSet,
)

#: docs/06_erd.md의 AI_RUN 컬럼 — 문서를 그대로 옮긴 것. 이 목록을 코드에 맞춰
#: 고치지 말 것: ERD가 원본이고 이 파일은 대조표다.
AI_RUN_COLUMNS = {
    "execution_id",
    "tenant_id",
    "capability",
    "pipeline_version",
    "engine_version",
    "threshold_version",
    "prompt_version",
    "schema_version",
    "contract_version",
    "model_provider",
    "model_name",
    "generation_params",
    "input_snapshot_hash",
    "created_at",
}

#: 버전 세트 6종 — 04_api_contract.md §2.2 meta.versions와 1:1 (7/15 통일).
#: 키 이름은 응답 envelope 표기(접미사 없음) 기준.
META_VERSION_KEYS = {"pipeline", "engine", "threshold", "prompt", "schema", "contract"}

FIXED_TIME = datetime(2026, 7, 15, 3, 0, tzinfo=UTC)
EXECUTION_ID = UUID("00000000-0000-4000-8000-000000000001")


def _context(capability: Capability = Capability.DETECTION) -> ExecutionContext:
    return ExecutionContext(
        execution_id=EXECUTION_ID,
        tenant_id="teacher_alias_001",
        capability=capability,
        input_snapshot_hash="sha256:" + "0" * 64,
        versions=VersionSet(
            pipeline_version="v2.1",
            engine_version="rules-1.0",
            schema_version="0.1",
            contract_version="0.1",
            threshold_version="v4" if capability is Capability.DETECTION else None,
        ),
    )


def test_run_metadata_matches_ai_run_columns() -> None:
    """RunMetadata ↔ AI_RUN 필드 1:1 (docs/06_erd.md)."""
    assert set(RunMetadata.model_fields) == AI_RUN_COLUMNS


def test_version_set_matches_meta_versions() -> None:
    """VersionSet ↔ meta.versions 6종 1:1 (04_api_contract.md §2.2).

    두 문서가 각각 4종씩 다르게 적고 있던 것을 7/15에 합집합 6종으로 통일했다.
    한쪽만 바뀌면 이 단언이 깨진다.
    """
    assert {name.removesuffix("_version") for name in VersionSet.model_fields} == META_VERSION_KEYS


def test_threshold_version_is_optional_for_non_detection() -> None:
    """임계값 시트는 감지 전용 — composition·import_mapping은 쓰지 않는다."""
    metadata = _context(Capability.COMPOSITION).to_run_metadata(created_at=FIXED_TIME)
    assert metadata.threshold_version is None


def test_detection_run_records_threshold_version() -> None:
    """감지가 어느 임계값 버전으로 판정했는지 없으면 과거 경보 재현 불가 (불변식 8)."""
    metadata = _context(Capability.DETECTION).to_run_metadata(created_at=FIXED_TIME)
    assert metadata.threshold_version == "v4"


def test_contract_version_is_required() -> None:
    """meta.versions는 항상 실린다 (§2.2) — contract는 nullable이 아니다."""
    assert RunMetadata.model_fields["contract_version"].is_required()
    assert VersionSet.model_fields["contract_version"].is_required()


def test_capability_values_frozen() -> None:
    """ERD: capability "detection|composition|import_mapping"."""
    assert {item.value for item in Capability} == {
        "detection",
        "composition",
        "import_mapping",
    }


def test_run_metadata_roundtrip() -> None:
    metadata = _context().to_run_metadata(created_at=FIXED_TIME)
    assert RunMetadata.model_validate(metadata.model_dump(mode="json")) == metadata


def test_execution_context_roundtrip() -> None:
    context = _context()
    assert ExecutionContext.model_validate(context.model_dump(mode="json")) == context


def test_to_run_metadata_carries_version_set() -> None:
    metadata = _context().to_run_metadata(created_at=FIXED_TIME)
    assert metadata.pipeline_version == "v2.1"
    assert metadata.engine_version == "rules-1.0"
    assert metadata.schema_version == "0.1"
    assert metadata.contract_version == "0.1"
    assert metadata.threshold_version == "v4"
    assert metadata.execution_id == EXECUTION_ID
    assert metadata.input_snapshot_hash == "sha256:" + "0" * 64


def test_llm_free_run_has_null_prompt_and_model() -> None:
    """감지는 LLM 0회 — ERD: prompt_version "LLM 미사용 시 null"."""
    metadata = _context(Capability.DETECTION).to_run_metadata(created_at=FIXED_TIME)
    assert metadata.prompt_version is None
    assert metadata.model_provider is None
    assert metadata.model_name is None
    assert metadata.generation_params is None


def test_llm_run_records_model_and_params() -> None:
    context = _context(Capability.COMPOSITION)
    params = GenerationParams(temperature=0.2, seed=42)
    metadata = context.to_run_metadata(
        created_at=FIXED_TIME,
        model_provider="fake",
        model_name="fake-1",
        generation_params=params,
    )
    assert metadata.generation_params is not None
    assert metadata.generation_params.seed == 42
    assert RunMetadata.model_validate(metadata.model_dump(mode="json")) == metadata


def test_created_at_is_injected_not_defaulted() -> None:
    """clock 주입 — datetime.now() 기본값 금지 (03_coding_rules.md §3)."""
    assert RunMetadata.model_fields["created_at"].is_required()


def test_context_is_frozen() -> None:
    """실행 중 컨텍스트가 바뀌면 재현성이 깨진다."""
    context = _context()
    with pytest.raises(ValueError, match="frozen"):
        # 런타임에도 막히는지 확인하는 테스트 — 타입 검사기가 먼저 잡는 게 정상이다
        context.tenant_id = "other"  # type: ignore[misc]


def test_empty_tenant_id_rejected() -> None:
    """전 테이블 tenant_id 필수 — 테넌트 격리 (CLAUDE.md §4)."""
    with pytest.raises(ValueError, match="tenant_id"):
        ExecutionContext.model_validate(
            {
                "execution_id": str(EXECUTION_ID),
                "tenant_id": "",
                "capability": "detection",
                "input_snapshot_hash": "sha256:abc",
                "versions": {
                    "pipeline_version": "v2.1",
                    "engine_version": "rules-1.0",
                    "schema_version": "0.1",
                    "contract_version": "0.1",
                },
            },
        )
