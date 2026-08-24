"""리포트 문장화의 근거·audience·게이트·재시도 불변식."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fake_provider import FakeProvider

from ai.composition.briefing_gate import check_brief_gate
from ai.contracts.diagnosis import (
    CellVerdict,
    MisconceptionReport,
    WeaknessCell,
    WeaknessMap,
)
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import FieldMissing, ModelRole, ParseFailed, RedactionBlocked
from ai.contracts.problem_generation import DifficultyBand, ItemResult, ProblemItemStatus
from ai.contracts.report import (
    ReportAudience,
    ReportBlockKind,
    ReportEvidenceRef,
    ReportMetricInput,
    ReportStudioData,
)
from ai.contracts.report_narration import ReportNarrationDraft
from ai.llm.gateway import LlmGateway
from ai.report import narration as narration_module
from ai.report.assembler import assemble_report_studio_data
from ai.report.narration import (
    ReportNarrationContext,
    ReportNarrationStatus,
    ReportNarrator,
    build_report_narration_context,
)
from ai.runtime.redaction import RedactionResult, redact

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


def _evidence(record_id: str) -> tuple[ReportEvidenceRef, ...]:
    return (
        ReportEvidenceRef(
            source_table="feature_week",
            record_id=record_id,
            summary="학습 기록 근거",
        ),
    )


def _studio_data() -> ReportStudioData:
    return assemble_report_studio_data(
        WeaknessMap(
            graph_version="graph-v1",
            taxonomy_version="taxonomy-v1",
            config_version="config-v1",
            snapshot_hash="sha256:narration",
            cells={
                "language×concept": WeaknessCell(
                    acc=0.75,
                    n=12,
                    verdict=CellVerdict.OK,
                ),
                "reading×fact": WeaknessCell(
                    acc=0.25,
                    n=10,
                    verdict=CellVerdict.WEAK,
                    severity=0.75,
                ),
            },
        ),
        MisconceptionReport(by_area={"reading": {"scope_confusion": 2}}),
        (
            ItemResult(
                item_id=UUID("00000000-0000-4000-8000-000000000901"),
                status=ProblemItemStatus.VERIFIED,
                attempt_no=1,
                difficulty_band=DifficultyBand.MEDIUM,
            ),
        ),
        cell_min_items=1,
        audience=ReportAudience.TEACHER_ONLY,
        metrics=(
            ReportMetricInput(
                metric_key="guardian_metric",
                value=10,
                audience=ReportAudience.GUARDIAN,
                evidence=_evidence("guardian-record"),
            ),
            ReportMetricInput(
                metric_key="class_average",
                value=99,
                audience=ReportAudience.TEACHER_ONLY,
                evidence=_evidence("teacher-only-record"),
            ),
        ),
    )


def _execution_context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=UUID("00000000-0000-4000-8000-000000000902"),
        tenant_id="tenant-a",
        capability=Capability.COMPOSITION,
        input_snapshot_hash="sha256:report-narration",
        versions=VersionSet(
            pipeline_version="0.1.0",
            engine_version="report-0.1",
            schema_version="0.1",
            contract_version="0.1",
            prompt_version="report-narration.v1",
        ),
    )


def _draft(content: str, *numbers_used: int) -> str:
    return ReportNarrationDraft(
        content=content,
        numbers_used=numbers_used,
    ).model_dump_json()


def _narrator(
    steps: tuple[str | Exception, ...],
    *,
    max_attempts: int = 3,
) -> tuple[ReportNarrator, FakeProvider]:
    provider = FakeProvider(steps, name="fake-reporter")
    gateway = LlmGateway(
        {ModelRole.REPORTER: provider},
        transport_retry={ModelRole.REPORTER: 0},
    )
    return (
        ReportNarrator(
            gateway,
            text_gate=check_brief_gate,
            clock=lambda: NOW,
            max_attempts=max_attempts,
        ),
        provider,
    )


def test_generates_five_blocks_and_records_versions_with_clean_rendered_prompts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[RedactionResult] = []

    def record_redaction(text: str) -> RedactionResult:
        result = redact(text)
        observed.append(result)
        return result

    monkeypatch.setattr(narration_module, "redact", record_redaction)
    narrator, provider = _narrator(
        (
            _draft("학습 기록을 함께 살펴보겠습니다."),
            _draft("이번 기록에는 10문항이 포함됐습니다.", 10),
            _draft("확인된 차트 흐름을 정리했습니다."),
            _draft("취약한 내용을 차근차근 복습해 보세요."),
            _draft("다음 기록에서도 변화를 살펴보겠습니다."),
        )
    )

    result = asyncio.run(
        narrator.generate(_studio_data(), execution_context=_execution_context())
    )

    assert [section.block_type for section in result.sections] == list(ReportBlockKind)
    assert all(section.status is ReportNarrationStatus.READY for section in result.sections)
    assert all(section.block is not None for section in result.sections)
    assert all(
        section.block.active_revision.gate_passed
        for section in result.sections
        if section.block
    )
    assert result.generated_at == NOW
    assert result.versions == _execution_context().versions
    assert len(provider.requests) == 5
    assert all(request.role is ModelRole.REPORTER for request in provider.requests)
    assert observed and all(not item.findings and not item.uncertain for item in observed)


def test_prompt_context_physically_excludes_teacher_only_metrics() -> None:
    narrator, provider = _narrator((_draft("학습 기록을 정리했습니다."),) * 5)

    asyncio.run(narrator.generate(_studio_data(), execution_context=_execution_context()))

    assert all("guardian_metric" in request.prompt for request in provider.requests)
    assert all("guardian-record" not in request.prompt for request in provider.requests)
    assert all("class_average" not in request.prompt for request in provider.requests)
    assert all("teacher-only-record" not in request.prompt for request in provider.requests)


def test_missing_evidence_leaves_section_empty_without_calling_provider() -> None:
    narrator, provider = _narrator(())
    empty_context = ReportNarrationContext(
        context_json='{"data_blocks":[]}',
        allowed_numbers=frozenset(),
        evidence=(),
    )

    section = asyncio.run(
        narrator.generate_section(
            ReportBlockKind.FACT,
            context=empty_context,
            execution_context=_execution_context(),
        )
    )

    assert section.status is ReportNarrationStatus.REJECTED_INSUFFICIENT
    assert section.block is None
    assert section.reason == "evidence_missing"
    assert section.attempts == 0
    assert not provider.requests


@pytest.mark.parametrize(
    ("response", "expected_reason"),
    [
        (_draft("원천에 없는 98을 넣었습니다.", 98), "number_not_grounded:98"),
        (_draft("이번 기록에는 10문항이 포함됐습니다."), "numbers_used_mismatch"),
        (_draft("이 내용은 문제아를 위한 기록입니다."), "forbidden:문제아"),
    ],
)
def test_delegated_gate_rejects_and_stops_after_three_attempts(
    response: str,
    expected_reason: str,
) -> None:
    narrator, provider = _narrator((response,) * 3)
    context = build_report_narration_context(_studio_data())

    section = asyncio.run(
        narrator.generate_section(
            ReportBlockKind.FACT,
            context=context,
            execution_context=_execution_context(),
        )
    )

    assert section.status is ReportNarrationStatus.TEMPLATE_ONLY
    assert section.block is None
    assert section.reason == expected_reason
    assert section.attempts == 3
    assert len(provider.requests) == 3


@pytest.mark.parametrize("error_type", [ParseFailed, FieldMissing])
def test_parse_failures_use_exactly_three_attempts_before_generation_exhaustion(
    error_type: type[ParseFailed],
) -> None:
    narrator, provider = _narrator(
        tuple(error_type("구조화 출력 실패") for _ in range(3))
    )

    section = asyncio.run(
        narrator.generate_section(
            ReportBlockKind.FACT,
            context=build_report_narration_context(_studio_data()),
            execution_context=_execution_context(),
        )
    )

    assert section.status is ReportNarrationStatus.TEMPLATE_ONLY
    assert section.reason == "generation_exhausted"
    assert section.attempts == 3
    assert len(provider.requests) == 3


def test_gateway_redaction_blocked_does_not_consume_regeneration_attempts() -> None:
    narrator, provider = _narrator((RedactionBlocked("마스킹 차단"),))

    section = asyncio.run(
        narrator.generate_section(
            ReportBlockKind.FACT,
            context=build_report_narration_context(_studio_data()),
            execution_context=_execution_context(),
        )
    )

    assert section.status is ReportNarrationStatus.TEMPLATE_ONLY
    assert section.reason == "redaction_blocked"
    assert section.attempts == 1
    assert len(provider.requests) == 1


def test_teacher_instruction_cannot_override_the_number_gate() -> None:
    narrator, provider = _narrator((_draft("반 평균은 98입니다.", 98),) * 3)

    section = asyncio.run(
        narrator.generate_section(
            ReportBlockKind.CHART_ANALYSIS,
            context=build_report_narration_context(_studio_data()),
            execution_context=_execution_context(),
            teacher_instruction="반 평균 98을 반드시 넣어 주세요.",
        )
    )

    assert section.status is ReportNarrationStatus.TEMPLATE_ONLY
    assert section.reason == "number_not_grounded:98"
    assert len(provider.requests) == 3


def test_rendered_prompt_redaction_fails_closed_before_provider() -> None:
    narrator, provider = _narrator(())

    section = asyncio.run(
        narrator.generate_section(
            ReportBlockKind.SUGGESTION,
            context=build_report_narration_context(_studio_data()),
            execution_context=_execution_context(),
            teacher_instruction="김철수가 박영희를 불렀다고 적어 주세요.",
        )
    )

    assert section.status is ReportNarrationStatus.TEMPLATE_ONLY
    assert section.reason == "redaction_blocked"
    assert not provider.requests
