"""문제출제 T1 실 LLM 왕복 스모크 — integration 전용."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from ai.contracts.diagnosis import (
    CellVerdict,
    DiagnosisResult,
    DiagnosisStatus,
    NodeVerdict,
    WeaknessCell,
    WeaknessMap,
    WeaknessNode,
)
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import (
    CallOutcome,
    LlmError,
    LLMProvider,
    LLMRequest,
    LLMResult,
    ModelRole,
)
from ai.contracts.problem_generation import (
    GeneratedItem,
    ProblemItemStatus,
    ProblemRequest,
    ProblemSetResult,
    TargetKind,
    TargetSource,
)
from ai.contracts.taxonomy import AreaTag, ItemFormat, TypeTag
from ai.llm.gateway import LlmCallRecord
from ai.llm.providers.openai_compat import OpenAiSettings, get_llm_settings
from ai.llm.structured import parse
from ai.problem_generation.bootstrap import build_problem_workflow
from ai.problem_generation.infrastructure.config import load_verify_config
from ai.problem_generation.infrastructure.graph_context import (
    GrammarNormGraphContextService,
)
from ai.problem_generation.infrastructure.memory_store import (
    InMemoryCandidateStore,
    InMemoryProblemItemStore,
)
from ai.problem_generation.provider import (
    ProblemProviders,
    ProblemProviderSettings,
    build_problem_gateway,
    build_problem_providers,
    get_problem_provider_settings,
)
from ai.runtime.real_llm import real_llm_skip_reason
from ai.runtime.tracing import external_tracing_active

pytestmark = pytest.mark.integration

_GRAPH_VERSION = "curriculum-graph.v1"
_SKILL_NODE_ID = "language.grammar.phonological_change"
_SNAPSHOT_HASH = "snapshot-pg-real-llm-smoke"
_TAXONOMY_VERSION = "taxonomy-v1"
_KST = ZoneInfo("Asia/Seoul")
_VALID_GATE_STATUSES = frozenset(
    {
        ProblemItemStatus.VERIFIED,
        ProblemItemStatus.NEEDS_REVIEW,
        ProblemItemStatus.DROPPED,
    }
)
_UPSTREAM_FAILURES = frozenset(
    {CallOutcome.TIMEOUT, CallOutcome.PROVIDER_ERROR}
)


class RealLlmSmokeUnavailable(RuntimeError):
    """실측 환경 또는 provider가 없어 스모크를 실행할 수 없음."""


@dataclass(frozen=True, slots=True)
class RealLlmSmokeObservation:
    called_at: datetime
    duration_s: float
    generator_endpoint: str
    generator_model: str
    verifier_endpoint: str
    verifier_model: str
    dedicated_verifier: bool
    generator_provider_name: str
    verifier_provider_name: str
    result: ProblemSetResult
    records: tuple[LlmCallRecord, ...]
    generator_completions: tuple[LLMResult, ...]
    verifier_completions: tuple[LLMResult, ...]
    parsed_items: tuple[GeneratedItem, ...]
    generated_items: tuple[GeneratedItem, ...]


class _ObservingProvider:
    def __init__(self, delegate: LLMProvider) -> None:
        self._delegate = delegate
        self.completions: list[LLMResult] = []

    @property
    def name(self) -> str:
        return self._delegate.name

    async def complete(
        self,
        request: LLMRequest,
        context: ExecutionContext,
    ) -> LLMResult:
        result = await self._delegate.complete(request, context)
        self.completions.append(result)
        return result


async def _diagnose(_: ProblemRequest) -> DiagnosisResult:
    return DiagnosisResult(
        status=DiagnosisStatus.GENERATED,
        weakness_map=WeaknessMap(
            graph_version=_GRAPH_VERSION,
            taxonomy_version=_TAXONOMY_VERSION,
            config_version="verify-config.v1",
            snapshot_hash=_SNAPSHOT_HASH,
            cells={
                "language×infer": WeaknessCell(
                    acc=0.4,
                    n=10,
                    verdict=CellVerdict.WEAK,
                    severity=0.8,
                )
            },
            nodes={
                _SKILL_NODE_ID: WeaknessNode(
                    verdict=NodeVerdict.WEAK_CONFIRMED,
                    basis=("cell:language×infer",),
                )
            },
        ),
    )


def _request() -> ProblemRequest:
    return ProblemRequest(
        request_id="req-pg-real-llm-smoke",
        idempotency_key="idem-pg-real-llm-smoke",
        tenant_id="tenant-pg-real-smoke",
        target_kind=TargetKind.STUDENT,
        target_ref="student-pg-real-smoke",
        target_source=TargetSource.WEAKNESS_AUTO,
        snapshot_hash=_SNAPSHOT_HASH,
        taxonomy_version=_TAXONOMY_VERSION,
        area_tag=AreaTag.LANGUAGE,
        type_tags=(TypeTag.INFER,),
        item_format=ItemFormat.MCQ,
        count=1,
    )


def _execution_context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=UUID("33333333-3333-4333-8333-333333333333"),
        tenant_id="tenant-pg-real-smoke",
        capability=Capability.PROBLEM_GENERATION,
        input_snapshot_hash=_SNAPSHOT_HASH,
        versions=VersionSet(
            pipeline_version="pipeline-v1",
            engine_version="engine-v1",
            schema_version="schema-v1",
            contract_version="contract-v1",
            prompt_version="v2",
            graph_version=_GRAPH_VERSION,
            taxonomy_version=_TAXONOMY_VERSION,
            verify_config_version="verify-config.v1",
        ),
    )


def _provider_endpoints(
    local: OpenAiSettings,
    settings: ProblemProviderSettings,
) -> tuple[str, str, str, str]:
    if not settings.has_dedicated_verifier:
        return (
            local.openai_base_url,
            local.openai_model,
            local.openai_base_url,
            local.openai_model,
        )
    assert settings.openai_base_url is not None
    assert settings.openai_model is not None
    return (
        local.openai_base_url,
        local.openai_model,
        settings.openai_base_url,
        settings.openai_model,
    )


def _role_is_unavailable(
    records: tuple[LlmCallRecord, ...],
    role: ModelRole,
) -> bool:
    role_records = tuple(record for record in records if record.role is role)
    return bool(role_records) and all(
        record.outcome in _UPSTREAM_FAILURES for record in role_records
    )


def _parse_generated_items(
    completions: tuple[LLMResult, ...],
) -> tuple[GeneratedItem, ...]:
    parsed: list[GeneratedItem] = []
    for completion in completions:
        text = completion.text
        if text is None:
            continue
        try:
            parsed.append(parse(text, GeneratedItem))
        except LlmError:
            continue
    return tuple(parsed)


async def run_real_llm_smoke() -> RealLlmSmokeObservation:
    """실 provider 두 역할로 T1 한 문항을 실행하고 비민감 관측값을 반환한다."""

    local_settings = get_llm_settings()
    provider_settings = get_problem_provider_settings()
    base_providers = build_problem_providers(
        settings=provider_settings,
        local_settings=local_settings,
    )
    generator = _ObservingProvider(base_providers.generator)
    verifier = _ObservingProvider(base_providers.verifier)
    providers = ProblemProviders(
        generator=generator,
        verifier=verifier,
        has_dedicated_verifier=base_providers.has_dedicated_verifier,
    )
    records: list[LlmCallRecord] = []
    verify_config = load_verify_config()
    item_store = InMemoryProblemItemStore()
    gateway = build_problem_gateway(
        verify_config=verify_config,
        recorder=lambda record, _context: records.append(record),
        providers=providers,
    )
    workflow = build_problem_workflow(
        gateway=gateway,
        graph_context=GrammarNormGraphContextService(),
        diagnosis=_diagnose,
        candidate_store=InMemoryCandidateStore(),
        item_store=item_store,
        checkpointer=InMemorySaver(),
        verify_config=verify_config,
    )

    called_at = datetime.now(_KST)
    started = time.perf_counter()
    outcome = await workflow.run(_request(), _execution_context())
    duration_s = time.perf_counter() - started
    if not isinstance(outcome, ProblemSetResult):
        raise AssertionError("T1 실측이 ProblemSetResult로 끝나지 않았다")

    frozen_records = tuple(records)
    if not generator.completions and _role_is_unavailable(
        frozen_records, ModelRole.GENERATOR
    ):
        raise RealLlmSmokeUnavailable("generator provider 미가용")
    if (
        outcome.items
        and outcome.items[0].status is ProblemItemStatus.VERIFICATION_UNAVAILABLE
        and _role_is_unavailable(frozen_records, ModelRole.VERIFIER)
    ):
        raise RealLlmSmokeUnavailable("verifier provider 미가용")

    parsed_items = _parse_generated_items(tuple(generator.completions))
    generated_items: list[GeneratedItem] = []
    for slot_index in range(len(outcome.items)):
        try:
            stored = await item_store.get(outcome.set_id, slot_index)
        except LookupError:
            continue
        if stored.item is not None:
            generated_items.append(stored.item)
    generator_endpoint, generator_model, verifier_endpoint, verifier_model = (
        _provider_endpoints(local_settings, provider_settings)
    )
    return RealLlmSmokeObservation(
        called_at=called_at,
        duration_s=duration_s,
        generator_endpoint=generator_endpoint,
        generator_model=generator_model,
        verifier_endpoint=verifier_endpoint,
        verifier_model=verifier_model,
        dedicated_verifier=base_providers.has_dedicated_verifier,
        generator_provider_name=generator.name,
        verifier_provider_name=verifier.name,
        result=outcome,
        records=frozen_records,
        generator_completions=tuple(generator.completions),
        verifier_completions=tuple(verifier.completions),
        parsed_items=parsed_items,
        generated_items=tuple(generated_items),
    )


def test_t1_problem_generation_real_llm_roundtrip() -> None:
    """실 모델이 스키마 응답을 내고 게이트가 정상 상태를 결정한다."""

    settings = get_llm_settings()
    # 🔴 **opt-in 없이는 안 부른다**(99 #32) — 종전 조건은 `.env`가 덮으면 열렸다.
    reason = real_llm_skip_reason(settings.openai_base_url)
    if reason is not None:
        pytest.skip(reason)
    if external_tracing_active():
        pytest.skip("B-14 P2 전 외부 트레이싱 비활성 전제 — 스모크 skip")

    try:
        observation = asyncio.run(run_real_llm_smoke())
    except RealLlmSmokeUnavailable as exc:
        pytest.skip(str(exc))
    except LlmError as exc:
        pytest.skip(f"로컬 LLM 미가용 — {type(exc).__name__}")

    assert observation.generator_provider_name != observation.verifier_provider_name
    assert len(observation.result.items) == 1
    assert observation.result.items[0].status in _VALID_GATE_STATUSES
    assert observation.parsed_items, "generator 응답이 GeneratedItem 스키마로 파싱되지 않았다"

    item = observation.parsed_items[-1]
    assert len(item.choices) == 5
    assert 1 <= item.answer.correct_no <= 5
    assert observation.generated_items
    assert observation.generated_items[0].evidence[0].quote is not None


__all__ = [
    "RealLlmSmokeObservation",
    "RealLlmSmokeUnavailable",
    "run_real_llm_smoke",
]
