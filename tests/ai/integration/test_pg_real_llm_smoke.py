"""문제출제 5영역 실 LLM 왕복 스모크 — integration 전용."""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast
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
    ItemResult,
    LiteratureGenre,
    MediaSourceKind,
    MediaSourceRequest,
    PassageDomain,
    PassageRequest,
    ProblemItemStatus,
    ProblemRequest,
    ProblemSetResult,
    ProblemSetStatus,
    SentenceComplexity,
    SourceRequest,
    SpeechWritingSourceKind,
    SpeechWritingSourceRequest,
    TargetKind,
    TargetSource,
    WorkSelection,
)
from ai.contracts.taxonomy import AreaTag, ItemFormat, TypeTag
from ai.diagnosis.skill_graph import load_skill_graph
from ai.llm.gateway import LlmCallRecord
from ai.llm.providers.openai_compat import OpenAiSettings, get_llm_settings
from ai.llm.structured import parse
from ai.problem_generation.bootstrap import build_problem_workflow
from ai.problem_generation.domain.policy import supports_source_procurement
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
from ai.runtime.real_llm import (
    REAL_LLM_OPTIN_ENV,
    real_llm_optin,
    real_llm_skip_reason,
)
from ai.runtime.tracing import external_tracing_active

_FAKES_DIR = Path(__file__).parents[1] / "fakes"
sys.path.insert(0, str(_FAKES_DIR))

from fake_graph_context import (  # noqa: E402
    FakeGraphContextService,
)
from fake_provider import FakeProvider  # noqa: E402

pytestmark = pytest.mark.integration

_GRAPH_VERSION = "curriculum-five-area-v1"
_SNAPSHOT_HASH = "snapshot-pg-real-llm-smoke"
_TAXONOMY_VERSION = "v1"
_KST = ZoneInfo("Asia/Seoul")
_GRAPH_PATH = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "ai"
    / "diagnosis"
    / "data"
    / "curriculum_graph.yaml"
)
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
_TRACKS = {
    AreaTag.LANGUAGE: "T1",
    AreaTag.READING: "T2",
    AreaTag.LITERATURE: "T3",
    AreaTag.SPEECH_WRITING: "T4",
    AreaTag.MEDIA: "T5",
}
_UNKNOWN = "unknown"


class RealLlmSmokeUnavailable(RuntimeError):
    """실측 환경 또는 provider가 없어 스모크를 실행할 수 없음."""


@dataclass(frozen=True, slots=True)
class ProviderFailureObservation:
    """본문·URL 없이 provider 실패의 분류 정보만 남기는 실측 메타."""

    exception_type: str
    cause_type: str | None
    http_status: int | None


@dataclass(frozen=True, slots=True)
class _AreaSmokeCase:
    area_tag: AreaTag
    skill_node_id: str
    passage: SourceRequest | None = None
    work_selection: WorkSelection | None = None


_AREA_CASES = (
    _AreaSmokeCase(
        area_tag=AreaTag.LANGUAGE,
        skill_node_id="language.grammar.phonological_change",
    ),
    _AreaSmokeCase(
        area_tag=AreaTag.READING,
        skill_node_id="reading.reasoning.inference",
        passage=PassageRequest(
            domain=PassageDomain.SCIENCE,
            topic_hint="생태계의 상호 작용",
            word_count=500,
            sentence_complexity=SentenceComplexity.STANDARD,
            paragraph_count=2,
            banned_topics_version="pg-banned-v1",
        ),
    ),
    _AreaSmokeCase(
        area_tag=AreaTag.LITERATURE,
        skill_node_id="literature.structure.composition",
        work_selection=WorkSelection(
            genre=LiteratureGenre.MODERN_NOVEL,
            era="근대",
            concept_keywords=("달",),
        ),
    ),
    _AreaSmokeCase(
        area_tag=AreaTag.SPEECH_WRITING,
        skill_node_id="speech_writing.speech.audience",
        passage=SpeechWritingSourceRequest(
            source_kind=SpeechWritingSourceKind.PRESENTATION,
            topic_hint="교내 자원 절약",
            banned_topics_version="pg-banned-v1",
        ),
    ),
    _AreaSmokeCase(
        area_tag=AreaTag.MEDIA,
        skill_node_id="media.reception.intent",
        passage=MediaSourceRequest(
            source_kind=MediaSourceKind.PAIRED,
            topic_hint="온라인 정보 검증",
            banned_topics_version="pg-banned-v1",
        ),
    ),
)


def _case_for(area_tag: AreaTag) -> _AreaSmokeCase:
    return next(case for case in _AREA_CASES if case.area_tag is area_tag)


@dataclass(frozen=True, slots=True)
class RealLlmSmokeObservation:
    area_tag: AreaTag
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


@dataclass(frozen=True, slots=True)
class RealLlmAreaSummary:
    area_tag: AreaTag
    observations: tuple[RealLlmSmokeObservation, ...]
    unavailable_reasons: tuple[str, ...]

    @property
    def generation_attempts(self) -> int:
        return sum(len(item.generator_completions) for item in self.observations)

    @property
    def schema_passed(self) -> int:
        return sum(len(item.parsed_items) for item in self.observations)

    @property
    def schema_pass_rate(self) -> float | None:
        attempts = self.generation_attempts
        return self.schema_passed / attempts if attempts else None


@dataclass(frozen=True, slots=True)
class SmokeReportRow:
    area_tag: AreaTag
    generation_attempts: int
    schema_passed: int
    final_status: str
    failure_reason: str
    stored_body_count: int
    model: str


@dataclass(frozen=True, slots=True)
class SafeFailureRow:
    area: str
    outcome: str
    exception_type: str
    cause_type: str
    http_status: str


class _ObservingProvider:
    def __init__(self, delegate: LLMProvider) -> None:
        self._delegate = delegate
        self.completions: list[LLMResult] = []
        self.failures: list[ProviderFailureObservation] = []

    @property
    def name(self) -> str:
        return self._delegate.name

    async def complete(
        self,
        request: LLMRequest,
        context: ExecutionContext,
    ) -> LLMResult:
        try:
            result = await self._delegate.complete(request, context)
        except Exception as error:
            self.failures.append(_provider_failure_observation(error))
            raise
        self.completions.append(result)
        return result


def _provider_failure_observation(error: Exception) -> ProviderFailureObservation:
    """계약 예외의 원인 체인에서 타입과 정수 상태 코드만 추출한다."""

    cause = error.__cause__
    deepest = cause
    while deepest is not None and deepest.__cause__ is not None:
        deepest = deepest.__cause__
    status = getattr(deepest, "status_code", None)
    return ProviderFailureObservation(
        exception_type=type(error).__name__,
        cause_type=type(deepest).__name__ if deepest is not None else None,
        http_status=status if isinstance(status, int) else None,
    )


def _unavailable_reason(
    *,
    role: ModelRole,
    records: tuple[LlmCallRecord, ...],
    failures: tuple[ProviderFailureObservation, ...],
) -> str:
    outcomes = sorted(
        {
            record.outcome.value
            for record in records
            if record.role is role and record.outcome in _UPSTREAM_FAILURES
        }
    )
    exception_types = sorted({failure.exception_type for failure in failures})
    cause_types = sorted(
        {failure.cause_type for failure in failures if failure.cause_type is not None}
    )
    statuses = sorted(
        {failure.http_status for failure in failures if failure.http_status is not None}
    )
    return (
        f"{role.value} provider 미가용"
        f" outcomes={','.join(outcomes) or 'unknown'}"
        f" exceptions={','.join(exception_types) or 'unknown'}"
        f" causes={','.join(cause_types) or 'unknown'}"
        f" http_statuses={','.join(str(status) for status in statuses) or 'none'}"
    )


async def _diagnose(request: ProblemRequest) -> DiagnosisResult:
    case = _case_for(request.area_tag)
    cell_key = f"{case.area_tag.value}×infer"
    return DiagnosisResult(
        status=DiagnosisStatus.GENERATED,
        weakness_map=WeaknessMap(
            graph_version=_GRAPH_VERSION,
            taxonomy_version=_TAXONOMY_VERSION,
            config_version="verify-config.v1",
            snapshot_hash=_SNAPSHOT_HASH,
            cells={
                cell_key: WeaknessCell(
                    acc=0.4,
                    n=10,
                    verdict=CellVerdict.WEAK,
                    severity=0.8,
                )
            },
            nodes={
                case.skill_node_id: WeaknessNode(
                    verdict=NodeVerdict.WEAK_CONFIRMED,
                    basis=(f"cell:{cell_key}",),
                )
            },
        ),
    )


def _request(area_tag: AreaTag = AreaTag.LANGUAGE) -> ProblemRequest:
    case = _case_for(area_tag)
    return ProblemRequest(
        request_id=f"req-pg-real-llm-smoke-{area_tag.value}",
        idempotency_key=f"idem-pg-real-llm-smoke-{area_tag.value}",
        tenant_id="tenant-pg-real-smoke",
        target_kind=TargetKind.STUDENT,
        target_ref="student-pg-real-smoke",
        target_source=TargetSource.WEAKNESS_AUTO,
        snapshot_hash=_SNAPSHOT_HASH,
        taxonomy_version=_TAXONOMY_VERSION,
        area_tag=area_tag,
        type_tags=(TypeTag.INFER,),
        item_format=ItemFormat.MCQ,
        count=1,
        passage=case.passage,
        work_selection=case.work_selection,
    )


def _loaded_prompt_version() -> str:
    from ai.llm.prompts.loader import load_prompt_template

    # registry 승격 때 스모크만 낡지 않도록 실행 프롬프트와 같은 정본에서 유도한다.
    return load_prompt_template("pg.items.v1").version


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
            prompt_version=_loaded_prompt_version(),
            graph_version=_GRAPH_VERSION,
            taxonomy_version=_TAXONOMY_VERSION,
            verify_config_version="verify-config.v1",
        ),
    )


def _graph_context(area_tag: AreaTag) -> GrammarNormGraphContextService | FakeGraphContextService:
    if area_tag is AreaTag.LANGUAGE:
        return GrammarNormGraphContextService()
    case = _case_for(area_tag)
    return FakeGraphContextService(((f"curriculum:{case.skill_node_id}",),))


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


async def run_real_llm_smoke(
    area_tag: AreaTag = AreaTag.LANGUAGE,
) -> RealLlmSmokeObservation:
    """실 provider 두 역할로 지정 영역 한 문항을 실행하고 비민감 관측값을 반환한다."""

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
        graph_context=_graph_context(area_tag),
        diagnosis=_diagnose,
        candidate_store=InMemoryCandidateStore(),
        item_store=item_store,
        checkpointer=InMemorySaver(),
        verify_config=verify_config,
    )

    called_at = datetime.now(_KST)
    started = time.perf_counter()
    outcome = await workflow.run(_request(area_tag), _execution_context())
    duration_s = time.perf_counter() - started
    if not isinstance(outcome, ProblemSetResult):
        raise AssertionError(
            f"{area_tag.value} 실측이 ProblemSetResult로 끝나지 않았다: "
            f"{type(outcome).__name__}: {outcome.status_reason}"
        )

    frozen_records = tuple(records)
    if not generator.completions and _role_is_unavailable(
        frozen_records, ModelRole.GENERATOR
    ):
        raise RealLlmSmokeUnavailable(
            _unavailable_reason(
                role=ModelRole.GENERATOR,
                records=frozen_records,
                failures=tuple(generator.failures),
            )
        )
    if (
        outcome.items
        and outcome.items[0].status is ProblemItemStatus.VERIFICATION_UNAVAILABLE
        and _role_is_unavailable(frozen_records, ModelRole.VERIFIER)
    ):
        raise RealLlmSmokeUnavailable(
            _unavailable_reason(
                role=ModelRole.VERIFIER,
                records=frozen_records,
                failures=tuple(verifier.failures),
            )
        )

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
        area_tag=area_tag,
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


async def run_real_llm_smoke_matrix(
    *, repetitions: int
) -> tuple[RealLlmAreaSummary, ...]:
    """5영역을 지정 횟수만큼 실행하고 영역별 스키마 통과율 입력값을 모은다."""

    if repetitions < 1:
        raise ValueError("실 LLM 스모크 반복 횟수는 1 이상이어야 한다")
    summaries: list[RealLlmAreaSummary] = []
    for case in _AREA_CASES:
        observations: list[RealLlmSmokeObservation] = []
        unavailable_reasons: list[str] = []
        for _ in range(repetitions):
            try:
                observations.append(await run_real_llm_smoke(case.area_tag))
            except RealLlmSmokeUnavailable as error:
                unavailable_reasons.append(str(error))
        summaries.append(
            RealLlmAreaSummary(
                area_tag=case.area_tag,
                observations=tuple(observations),
                unavailable_reasons=tuple(unavailable_reasons),
            )
        )
    return tuple(summaries)


def _report_row(
    area_tag: AreaTag,
    observations: tuple[RealLlmSmokeObservation, ...],
) -> SmokeReportRow:
    items = tuple(item for observation in observations for item in observation.result.items)
    statuses = ",".join(item.status.value for item in items) or "no_item"
    reasons = (
        ",".join(
            item.failure_reason.value if item.failure_reason is not None else "-"
            for item in items
        )
        or "-"
    )
    models = sorted({observation.generator_model for observation in observations})
    return SmokeReportRow(
        area_tag=area_tag,
        generation_attempts=sum(
            len(observation.generator_completions) for observation in observations
        ),
        schema_passed=sum(len(observation.parsed_items) for observation in observations),
        final_status=statuses,
        failure_reason=reasons,
        stored_body_count=sum(
            len(observation.generated_items) for observation in observations
        ),
        model=",".join(models) or "-",
    )


def _safe_failure_from_reason(area: AreaTag | str, reason: str) -> SafeFailureRow:
    fields = {
        key: value
        for token in reason.split()
        if "=" in token
        for key, value in (token.split("=", 1),)
        if key in {"outcomes", "exceptions", "causes", "http_statuses"}
    }
    return SafeFailureRow(
        area=area.value if isinstance(area, AreaTag) else area,
        outcome=fields.get("outcomes", _UNKNOWN),
        exception_type=fields.get("exceptions", _UNKNOWN),
        cause_type=fields.get("causes", _UNKNOWN),
        http_status=fields.get("http_statuses", "none"),
    )


def _safe_failure_from_exception(
    area: AreaTag | str, error: Exception
) -> SafeFailureRow:
    deepest = error.__cause__
    while deepest is not None and deepest.__cause__ is not None:
        deepest = deepest.__cause__
    status = getattr(deepest, "status_code", None)
    return SafeFailureRow(
        area=area.value if isinstance(area, AreaTag) else area,
        outcome=_UNKNOWN,
        exception_type=type(error).__name__,
        cause_type=type(deepest).__name__ if deepest is not None else _UNKNOWN,
        http_status=str(status) if isinstance(status, int) else "none",
    )


async def _run_single(
    area_tag: AreaTag, repetitions: int
) -> tuple[tuple[SmokeReportRow, ...], tuple[SafeFailureRow, ...]]:
    observations: list[RealLlmSmokeObservation] = []
    failures: list[SafeFailureRow] = []
    for _ in range(repetitions):
        try:
            observations.append(await run_real_llm_smoke(area_tag))
        except RealLlmSmokeUnavailable as error:
            failures.append(_safe_failure_from_reason(area_tag, str(error)))
        except Exception as error:
            failures.append(_safe_failure_from_exception(area_tag, error))
    rows = (_report_row(area_tag, tuple(observations)),) if observations else ()
    return rows, tuple(failures)


async def _run_matrix(
    repetitions: int,
) -> tuple[tuple[SmokeReportRow, ...], tuple[SafeFailureRow, ...]]:
    try:
        summaries = await run_real_llm_smoke_matrix(repetitions=repetitions)
    except Exception as error:
        return (), (_safe_failure_from_exception("all", error),)
    rows: list[SmokeReportRow] = []
    failures: list[SafeFailureRow] = []
    for summary in summaries:
        if summary.observations:
            rows.append(_report_row(summary.area_tag, summary.observations))
        failures.extend(
            _safe_failure_from_reason(summary.area_tag, reason)
            for reason in summary.unavailable_reasons
        )
    return tuple(rows), tuple(failures)


def _render_report(rows: tuple[SmokeReportRow, ...]) -> str:
    lines = [
        "| 트랙 | 영역 | 생성 시도 N | 스키마 통과 M | 최종 status | "
        "failure_reason | 저장 본문 수 | 모델명 |",
        "| --- | --- | ---: | ---: | --- | --- | ---: | --- |",
    ]
    lines.extend(
        "| "
        + " | ".join(
            (
                _TRACKS[row.area_tag],
                row.area_tag.value,
                str(row.generation_attempts),
                str(row.schema_passed),
                row.final_status,
                row.failure_reason,
                str(row.stored_body_count),
                row.model,
            )
        )
        + " |"
        for row in rows
    )
    return "\n".join(lines)


def _render_failures(rows: tuple[SafeFailureRow, ...]) -> str:
    lines = [
        "| 영역 | outcome | 예외 타입 | 원인 타입 | HTTP 상태 |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines.extend(
        "| "
        + " | ".join(
            (
                row.area,
                row.outcome,
                row.exception_type,
                row.cause_type,
                row.http_status,
            )
        )
        + " |"
        for row in rows
    )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="문제출제 T1 또는 T1~T5 실 LLM 스모크 집계"
    )
    parser.add_argument(
        "--area",
        required=True,
        choices=("all", *(area.value for area in AreaTag)),
        help="단일 영역 또는 all",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=1,
        help="영역별 반복 횟수(1 이상)",
    )
    return parser


async def _main_async(area: str, repetitions: int) -> int:
    if repetitions < 1:
        print("오류: --repetitions는 1 이상이어야 한다")
        return 2
    if area == "all":
        rows, failures = await _run_matrix(repetitions)
    else:
        rows, failures = await _run_single(AreaTag(area), repetitions)
    print(_render_report(rows))
    if failures:
        print("\n비민감 provider 실패 메타")
        print(_render_failures(failures))
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not real_llm_optin():
        print(
            f"실 LLM 미실행: {REAL_LLM_OPTIN_ENV}=1이 현재 프로세스에 없다. "
            "조용한 skip 없이 종료한다."
        )
        return 2
    if external_tracing_active():
        print("실 LLM 미실행: 외부 트레이싱이 활성화돼 있다.")
        return 2
    try:
        return asyncio.run(_main_async(args.area, args.repetitions))
    except KeyboardInterrupt:
        return 130


def test_smoke_cases_use_real_curriculum_nodes_and_supported_source_shapes() -> None:
    graph = load_skill_graph(_GRAPH_PATH, expected_taxonomy_version=_TAXONOMY_VERSION)
    nodes = {node.id: node for node in graph.nodes}

    assert {case.area_tag for case in _AREA_CASES} == set(AreaTag)
    for case in _AREA_CASES:
        node = nodes[case.skill_node_id]
        request = _request(case.area_tag)
        assert node.area_tag is case.area_tag
        assert TypeTag.INFER in node.type_affinity
        assert supports_source_procurement(
            area_tag=case.area_tag,
            has_passage_request=request.passage is not None,
            has_work_selection=request.work_selection is not None,
        )


def test_smoke_matrix_rejects_nonpositive_repetitions() -> None:
    with pytest.raises(ValueError, match="1 이상"):
        asyncio.run(run_real_llm_smoke_matrix(repetitions=0))


def test_provider_failure_observation_excludes_message_and_keeps_safe_metadata() -> None:
    class _HttpFailure(Exception):
        status_code = 400

    cause = _HttpFailure("https://secret.example/v1?api_key=secret")
    error = LlmError("민감한 요청 원문")
    error.__cause__ = cause

    observation = _provider_failure_observation(error)

    assert observation == ProviderFailureObservation(
        exception_type="LlmError",
        cause_type="_HttpFailure",
        http_status=400,
    )
    assert "secret" not in repr(observation)


def test_unavailable_reason_reports_only_categorical_failure_metadata() -> None:
    record = LlmCallRecord(
        role=ModelRole.GENERATOR,
        prompt_id="pg.items.v1",
        prompt_version="v6",
        provider="local-generator",
        model=None,
        usage=None,
        latency_ms=1,
        outcome=CallOutcome.PROVIDER_ERROR,
    )

    reason = _unavailable_reason(
        role=ModelRole.GENERATOR,
        records=(record,),
        failures=(
            ProviderFailureObservation(
                exception_type="LlmError",
                cause_type="BadRequestError",
                http_status=400,
            ),
        ),
    )

    assert reason == (
        "generator provider 미가용 outcomes=provider_error exceptions=LlmError "
        "causes=BadRequestError http_statuses=400"
    )


async def _fake_cli_observation() -> RealLlmSmokeObservation:
    provider = FakeProvider(("RAW_COMPLETION_MUST_NOT_PRINT",), name="fake-cli")
    completion = await provider.complete(
        LLMRequest(
            role=ModelRole.GENERATOR,
            prompt="[마스킹 통과 프롬프트]",
            prompt_id="pg.items.v1",
            prompt_version="v6",
        ),
        _execution_context(),
    )
    placeholder = cast(GeneratedItem, object())
    item_id = UUID("00000000-0000-4000-8000-000000000903")
    result = ProblemSetResult(
        set_id=UUID("00000000-0000-4000-8000-000000000902"),
        status=ProblemSetStatus.GENERATED,
        target_source=TargetSource.TEACHER_MANUAL,
        personalized=False,
        requested_count=1,
        processed_count=1,
        unstarted_count=0,
        items=(
            ItemResult(
                item_id=item_id,
                status=ProblemItemStatus.VERIFIED,
                attempt_no=1,
            ),
        ),
    )
    return RealLlmSmokeObservation(
        area_tag=AreaTag.LANGUAGE,
        called_at=datetime.now(_KST),
        duration_s=0.01,
        generator_endpoint="https://secret.example/v1?api_key=hidden",
        generator_model="fake-model",
        verifier_endpoint="https://secret.example/v1?api_key=hidden",
        verifier_model="fake-model",
        dedicated_verifier=True,
        generator_provider_name="fake-cli-generator",
        verifier_provider_name="fake-cli-verifier",
        result=result,
        records=(),
        generator_completions=(completion,),
        verifier_completions=(),
        parsed_items=(placeholder,),
        generated_items=(placeholder,),
    )


def test_cli_optin_off_exits_before_runner(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(REAL_LLM_OPTIN_ENV, raising=False)

    def forbidden_run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("opt-in 없이 runner를 호출했다")

    monkeypatch.setattr(asyncio, "run", forbidden_run)

    assert main(["--area", "language"]) == 2
    assert "실 LLM 미실행" in capsys.readouterr().out


@pytest.mark.parametrize("runner_exit_code", [0, 1])
def test_cli_preserves_runner_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    runner_exit_code: int,
) -> None:
    monkeypatch.setattr(sys.modules[__name__], "real_llm_optin", lambda: True)
    monkeypatch.setattr(
        sys.modules[__name__], "external_tracing_active", lambda: False
    )
    def completed(coro: Coroutine[object, object, int]) -> int:
        coro.close()
        return runner_exit_code

    monkeypatch.setattr(asyncio, "run", completed)

    assert main(["--area", "language"]) == runner_exit_code


def test_cli_keyboard_interrupt_returns_130(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.modules[__name__], "real_llm_optin", lambda: True)
    monkeypatch.setattr(
        sys.modules[__name__], "external_tracing_active", lambda: False
    )

    def interrupted(coro: Coroutine[object, object, int]) -> int:
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(asyncio, "run", interrupted)

    assert main(["--area", "language"]) == 130


def test_cli_does_not_map_provider_error_to_130(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys.modules[__name__], "real_llm_optin", lambda: True)
    monkeypatch.setattr(
        sys.modules[__name__], "external_tracing_active", lambda: False
    )

    def provider_error(coro: Coroutine[object, object, int]) -> int:
        coro.close()
        raise RuntimeError("provider error")

    monkeypatch.setattr(asyncio, "run", provider_error)

    with pytest.raises(RuntimeError, match="provider error"):
        main(["--area", "language"])


def test_cli_fake_provider_renders_table_without_endpoint() -> None:
    observation = asyncio.run(_fake_cli_observation())
    rendered = _render_report((_report_row(AreaTag.LANGUAGE, (observation,)),))

    assert "| T1 | language | 1 | 1 | verified | - | 1 | fake-model |" in rendered
    assert "secret.example" not in rendered
    assert "api_key" not in rendered
    assert "RAW_COMPLETION_MUST_NOT_PRINT" not in rendered


def test_cli_preserves_success_and_failure_from_repeated_single_area(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    observation = asyncio.run(_fake_cli_observation())
    outcomes: list[RealLlmSmokeObservation | Exception] = [
        observation,
        RealLlmSmokeUnavailable(
            "generator provider 미가용 outcomes=provider_error "
            "exceptions=LlmError causes=BadRequestError http_statuses=400"
        ),
    ]

    async def fake_run(_area_tag: AreaTag) -> RealLlmSmokeObservation:
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(
        sys.modules[__name__],
        "run_real_llm_smoke",
        fake_run,
    )

    assert asyncio.run(_main_async("language", 2)) == 1
    output = capsys.readouterr().out
    assert "| T1 | language | 1 | 1 | verified | - | 1 | fake-model |" in output
    assert "| language | provider_error | LlmError | BadRequestError | 400 |" in output
    assert "secret.example" not in output
    assert "RAW_COMPLETION_MUST_NOT_PRINT" not in output


def test_cli_failure_renderer_keeps_only_categorical_metadata() -> None:
    reason = (
        "generator provider 미가용 outcomes=provider_error exceptions=LlmError "
        "causes=BadRequestError http_statuses=400 "
        "https://secret.example/v1?api_key=hidden"
    )
    rendered = _render_failures(
        (_safe_failure_from_reason(AreaTag.LANGUAGE, reason),)
    )

    assert "| language | provider_error | LlmError | BadRequestError | 400 |" in rendered
    assert "secret.example" not in rendered
    assert "api_key" not in rendered


def test_t1_problem_generation_real_llm_roundtrip() -> None:
    """정본 게이트의 보호된 실 호출은 실 근거 서비스가 있는 T1만 검사한다."""

    settings = get_llm_settings()
    # 🔴 **opt-in 없이는 안 부른다**(99 #32) — 종전 조건은 `.env`가 덮으면 열렸다.
    reason = real_llm_skip_reason(settings.openai_base_url)
    if reason is not None:
        pytest.skip(reason)
    if external_tracing_active():
        pytest.skip("B-14 P2 전 외부 트레이싱 비활성 전제 — 스모크 skip")

    try:
        observation = asyncio.run(run_real_llm_smoke(AreaTag.LANGUAGE))
    except RealLlmSmokeUnavailable as exc:
        pytest.skip(str(exc))
    except LlmError as exc:
        pytest.skip(f"OpenAI 미가용 — {type(exc).__name__}")

    assert observation.generator_provider_name != observation.verifier_provider_name
    assert len(observation.result.items) == 1
    assert observation.result.items[0].status in _VALID_GATE_STATUSES
    assert observation.parsed_items, (
        "language generator 응답이 GeneratedItem 스키마로 파싱되지 않았다"
    )
    item = observation.parsed_items[-1]
    assert len(item.choices) == 5
    assert 1 <= item.answer.correct_no <= 5
    assert observation.generated_items
    assert observation.generated_items[0].evidence[0].quote is not None


__all__ = [
    "RealLlmAreaSummary",
    "RealLlmSmokeObservation",
    "RealLlmSmokeUnavailable",
    "run_real_llm_smoke",
    "run_real_llm_smoke_matrix",
]


if __name__ == "__main__":
    raise SystemExit(main())
