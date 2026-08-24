"""결정론 리포트 데이터에서 근거가 있는 본문 다섯 블록만 생성한다."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai.contracts.execution import ExecutionContext, VersionSet
from ai.contracts.llm import CallOutcome, LlmError, LLMRequest, ModelRole, RedactionBlocked
from ai.contracts.report import (
    ReportAudience,
    ReportBlock,
    ReportBlockKind,
    ReportBlockRevision,
    ReportEvidenceRef,
    ReportRevisionKind,
    ReportStudioData,
)
from ai.contracts.report_narration import ReportNarrationDraft
from ai.llm.determinism import deterministic_params
from ai.llm.gateway import LlmGateway
from ai.llm.prompts.loader import LoadedPromptTemplate, load_prompt_template
from ai.llm.structured import parse
from ai.report.assembler import filter_report_metrics
from ai.report.gate import DeterministicTextGate, check_report_block
from ai.runtime.redaction import redact

_PROMPT_IDS = {
    ReportBlockKind.GREETING: "report.greeting.v1",
    ReportBlockKind.FACT: "report.fact.v1",
    ReportBlockKind.CHART_ANALYSIS: "report.chart_analysis.v1",
    ReportBlockKind.SUGGESTION: "report.suggestion.v1",
    ReportBlockKind.CLOSING: "report.closing.v1",
}
_MAX_LENGTHS = {
    ReportBlockKind.GREETING: 160,
    ReportBlockKind.FACT: 360,
    ReportBlockKind.CHART_ANALYSIS: 480,
    ReportBlockKind.SUGGESTION: 320,
    ReportBlockKind.CLOSING: 160,
}


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _prompt_value(value: object) -> object:
    """원본은 보존하고 프롬프트의 실수만 표시 정밀도로 결정론 투영한다."""

    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, dict):
        return {key: _prompt_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_prompt_value(item) for item in value]
    return value


class ReportNarrationStatus(StrEnum):
    """리포트 섹션 한 곳의 생성 결과."""

    READY = "ready"
    REJECTED_INSUFFICIENT = "rejected_insufficient"
    TEMPLATE_ONLY = "template_only"


class ReportNarrationContext(BaseModel):
    """프롬프트 투영과 게이트가 공유하는 결정론 입력."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    context_json: str = Field(min_length=1)
    allowed_numbers: frozenset[str]
    evidence: tuple[ReportEvidenceRef, ...]


class ReportNarrationSection(BaseModel):
    """성공 블록 또는 정직하게 비운 섹션과 사유."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    block_type: ReportBlockKind
    status: ReportNarrationStatus
    prompt_id: str
    prompt_version: str
    attempts: int = Field(ge=0, le=3)
    block: ReportBlock | None = None
    reason: str = ""

    @model_validator(mode="after")
    def validate_result(self) -> ReportNarrationSection:
        if self.status is ReportNarrationStatus.READY:
            if self.block is None or self.reason:
                raise ValueError("ready 섹션에는 블록만 있어야 한다")
            return self
        if self.block is not None or not self.reason:
            raise ValueError("빈 섹션에는 블록 없이 사유가 필요하다")
        return self


class ReportNarrationResult(BaseModel):
    """본문 다섯 섹션과 생성 시각·버전의 재현 스냅숏."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sections: tuple[ReportNarrationSection, ...]
    generated_at: datetime
    versions: VersionSet

    @model_validator(mode="after")
    def validate_sections(self) -> ReportNarrationResult:
        if tuple(section.block_type for section in self.sections) != tuple(ReportBlockKind):
            raise ValueError("본문 섹션은 정본 순서의 다섯 종류가 모두 필요하다")
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("generated_at에는 timezone이 필요하다")
        return self


def build_report_narration_context(studio_data: ReportStudioData) -> ReportNarrationContext:
    """guardian 지표와 여섯 데이터 블록을 참조 ID 없는 프롬프트 입력으로 투영한다."""

    metrics = filter_report_metrics(
        studio_data.metrics,
        audience=ReportAudience.GUARDIAN,
    )
    evidence = _unique_evidence(
        tuple(
            reference
            for block in studio_data.blocks
            for number in block.numbers_used
            for reference in number.evidence
        )
        + tuple(reference for metric in metrics for reference in metric.evidence)
    )
    allowed_numbers = frozenset(
        str(value)
        for value in (
            *(
                number.value
                for block in studio_data.blocks
                for number in block.numbers_used
            ),
            *(metric.value for metric in metrics),
        )
        if _is_non_negative_integer(value)
    )
    prompt_projection = {
        "audience": ReportAudience.GUARDIAN.value,
        "data_blocks": [
            {
                "kind": block.kind.value,
                "payload": _prompt_value(block.payload.model_dump(mode="json")),
                "numbers": [
                    {
                        "name": number.name,
                        "value": _prompt_value(number.value),
                        "evidence_summaries": [item.summary for item in number.evidence],
                    }
                    for number in block.numbers_used
                ],
            }
            for block in studio_data.blocks
        ],
        "metrics": [
            {
                "metric_key": metric.metric_key,
                "value": _prompt_value(metric.value),
                "evidence_summaries": [item.summary for item in metric.evidence],
            }
            for metric in metrics
        ],
        "unproduced": [item.value for item in studio_data.unproduced],
    }
    return ReportNarrationContext(
        context_json=_canonical_json(prompt_projection),
        allowed_numbers=allowed_numbers,
        evidence=evidence,
    )


class ReportNarrator:
    """버전 프롬프트·redaction·주입 게이트를 거쳐 다섯 섹션을 만든다."""

    def __init__(
        self,
        gateway: LlmGateway,
        *,
        text_gate: DeterministicTextGate,
        clock: Callable[[], datetime],
        prompts: dict[ReportBlockKind, LoadedPromptTemplate] | None = None,
        max_attempts: int = 3,
    ) -> None:
        if not 1 <= max_attempts <= 3:
            raise ValueError("블록 생성 시도는 1~3회여야 한다")
        self._gateway = gateway
        self._text_gate = text_gate
        self._clock = clock
        self._max_attempts = max_attempts
        self._prompts = prompts or {
            kind: load_prompt_template(prompt_id) for kind, prompt_id in _PROMPT_IDS.items()
        }
        if set(self._prompts) != set(ReportBlockKind):
            raise ValueError("리포트 본문 프롬프트는 다섯 블록을 모두 덮어야 한다")
        for kind, prompt in self._prompts.items():
            if prompt.role is not ModelRole.REPORTER:
                raise ValueError(f"{kind.value} 프롬프트 role은 reporter여야 한다")
            if prompt.response_schema_name != ReportNarrationDraft.__name__:
                raise ValueError(f"{kind.value} 응답 스키마가 ReportNarrationDraft가 아니다")

    async def generate(
        self,
        studio_data: ReportStudioData,
        *,
        execution_context: ExecutionContext,
        teacher_instruction: str | None = None,
    ) -> ReportNarrationResult:
        """guardian 조립 결과 한 벌에서 정본 순서의 다섯 섹션을 만든다."""

        context = build_report_narration_context(studio_data)
        sections = tuple(
            [
                await self.generate_section(
                    kind,
                    context=context,
                    execution_context=execution_context,
                    teacher_instruction=teacher_instruction,
                )
                for kind in ReportBlockKind
            ]
        )
        generated_at = self._clock()
        return ReportNarrationResult(
            sections=sections,
            generated_at=generated_at,
            versions=execution_context.versions,
        )

    async def generate_section(
        self,
        block_type: ReportBlockKind,
        *,
        context: ReportNarrationContext,
        execution_context: ExecutionContext,
        teacher_instruction: str | None = None,
    ) -> ReportNarrationSection:
        """한 섹션을 최대 세 번 생성하고 실패 시 본문 없이 사유만 남긴다."""

        prompt = self._prompts[block_type]
        if not context.evidence:
            return _empty_section(
                block_type,
                prompt,
                ReportNarrationStatus.REJECTED_INSUFFICIENT,
                reason="evidence_missing",
                attempts=0,
            )

        retry_reason = ""
        for attempt in range(1, self._max_attempts + 1):
            prompt_text = prompt.render(
                {
                    "context_json": context.context_json,
                    "allowed_numbers_json": _canonical_json(sorted(context.allowed_numbers)),
                    "teacher_instruction_json": _canonical_json(teacher_instruction),
                    "retry_reason_json": _canonical_json(retry_reason),
                    "response_schema_json": _canonical_json(
                        ReportNarrationDraft.model_json_schema()
                    ),
                }
            )
            redacted = redact(prompt_text)
            if redacted.findings or redacted.uncertain:
                return _empty_section(
                    block_type,
                    prompt,
                    ReportNarrationStatus.TEMPLATE_ONLY,
                    reason="redaction_blocked",
                    attempts=attempt,
                )
            try:
                result = await self._gateway.complete(
                    LLMRequest(
                        role=ModelRole.REPORTER,
                        prompt=redacted.masked_text,
                        prompt_id=prompt.prompt_id,
                        prompt_version=prompt.version,
                        response_schema_name=prompt.response_schema_name,
                        generation_params=deterministic_params(max_tokens=256),
                    ),
                    execution_context,
                )
            except RedactionBlocked:
                return _empty_section(
                    block_type,
                    prompt,
                    ReportNarrationStatus.TEMPLATE_ONLY,
                    reason="redaction_blocked",
                    attempts=attempt,
                )
            except LlmError:
                return _empty_section(
                    block_type,
                    prompt,
                    ReportNarrationStatus.TEMPLATE_ONLY,
                    reason="llm_unavailable",
                    attempts=attempt,
                )
            if result.outcome is not CallOutcome.OK or result.text is None:
                retry_reason = f"llm_{result.outcome.value}"
                continue
            try:
                draft = parse(result.text, ReportNarrationDraft)
            except LlmError as error:
                retry_reason = type(error).__name__
                continue

            block = _draft_block(
                block_type,
                draft,
                evidence=context.evidence,
                execution_id=execution_context.execution_id,
            )
            gate = check_report_block(
                block,
                context.allowed_numbers,
                max_length=_MAX_LENGTHS[block_type],
                text_gate=self._text_gate,
            )
            if not gate.passed:
                retry_reason = gate.reason
                continue
            revision = block.active_revision.model_copy(update={"gate_passed": True})
            passed = block.model_copy(update={"revisions": (revision,)})
            return ReportNarrationSection(
                block_type=block_type,
                status=ReportNarrationStatus.READY,
                prompt_id=prompt.prompt_id,
                prompt_version=prompt.version,
                attempts=attempt,
                block=passed,
            )

        return _empty_section(
            block_type,
            prompt,
            ReportNarrationStatus.TEMPLATE_ONLY,
            reason=retry_reason or "generation_exhausted",
            attempts=self._max_attempts,
        )


def _draft_block(
    block_type: ReportBlockKind,
    draft: ReportNarrationDraft,
    *,
    evidence: tuple[ReportEvidenceRef, ...],
    execution_id: UUID,
) -> ReportBlock:
    revision = ReportBlockRevision(
        revision_no=0,
        revision_kind=ReportRevisionKind.AI_DRAFT,
        ai_original=draft.content,
        evidence=evidence,
        numbers_used=draft.numbers_used,
    )
    return ReportBlock(
        block_id=uuid5(NAMESPACE_URL, f"report:{execution_id}:{block_type.value}"),
        seq=tuple(ReportBlockKind).index(block_type),
        block_type=block_type,
        revisions=(revision,),
        active_revision_no=0,
    )


def _empty_section(
    block_type: ReportBlockKind,
    prompt: LoadedPromptTemplate,
    status: ReportNarrationStatus,
    *,
    reason: str,
    attempts: int,
) -> ReportNarrationSection:
    return ReportNarrationSection(
        block_type=block_type,
        status=status,
        prompt_id=prompt.prompt_id,
        prompt_version=prompt.version,
        attempts=attempts,
        reason=reason,
    )


def _is_non_negative_integer(value: int | float) -> bool:
    return value >= 0 and (isinstance(value, int) or value.is_integer())


def _unique_evidence(
    evidence: tuple[ReportEvidenceRef, ...],
) -> tuple[ReportEvidenceRef, ...]:
    seen: set[tuple[str, str, str]] = set()
    result: list[ReportEvidenceRef] = []
    for item in evidence:
        key = (item.source_table, item.record_id, item.summary)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return tuple(result)


__all__ = [
    "ReportNarrationContext",
    "ReportNarrationResult",
    "ReportNarrationSection",
    "ReportNarrationStatus",
    "ReportNarrator",
    "build_report_narration_context",
]
