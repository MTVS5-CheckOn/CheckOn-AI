"""리포트 생성 실 LLM 지연 측정기 — 명시적 opt-in·6회·100콜 상한.

본문과 프롬프트는 출력하지 않는다. 게이트웨이 recorder의 콜당 지연과 HTTP 응답의
섹션 상태만 집계하며, PostgreSQL이나 원격 저장소를 사용하지 않는다.

실행::

    CHECKON_ALLOW_REAL_LLM=1 LLM_PROVIDER=openai_compat \
      uv run --frozen python -m ai.evaluation.report_llm_smoke \
      --runs 6 --max-calls 100
"""

from __future__ import annotations

import argparse
import asyncio
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final
from unittest.mock import patch
from uuid import NAMESPACE_URL, UUID, uuid5

from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from ai.api.app import create_app
from ai.api.routers.report import (
    reset_report_narrator,
    reset_report_store,
    set_report_narrator,
    set_report_store,
)
from ai.contracts.diagnosis import CellVerdict, MisconceptionReport, WeaknessCell, WeaknessMap
from ai.contracts.execution import ExecutionContext
from ai.contracts.llm import (
    CallOutcome,
    FieldMissing,
    LLMProvider,
    LLMRequest,
    LLMResult,
    ParseFailed,
    TokenUsage,
)
from ai.contracts.problem_generation import DifficultyBand, ItemResult, ProblemItemStatus
from ai.contracts.report import (
    ReportAudience,
    ReportBlock,
    ReportBlockKind,
    ReportEvidenceRef,
    ReportMetricInput,
)
from ai.contracts.report_narration import ReportNarrationDraft
from ai.db.repositories.run_store import CollectedCall, LlmCallCollector
from ai.llm.structured import parse
from ai.report.gate import (
    DeterministicTextGate,
    ReportGateResult,
    check_report_block,
)
from ai.report.memory_store import InMemoryReportStore
from ai.report.provider import (
    ReportProviderSettings,
    build_report_llm_provider,
    build_report_narrator,
)
from ai.report.store import ReportSourceSnapshot
from ai.runtime.console import ConsoleSettings
from ai.runtime.real_llm import REAL_LLM_OPTIN_ENV, real_llm_optin

_MAX_RUNS: Final = 20
_MAX_EXTERNAL_CALLS: Final = 100
_MAX_ATTEMPTS_PER_BLOCK: Final = 3
_DEFAULT_OUTPUT: Final = Path("local_data/2026-08-25_report_llm_smoke_retry3.txt")
_PROMPT_IDS: Final = (
    "report.greeting.v1",
    "report.fact.v1",
    "report.chart_analysis.v1",
    "report.suggestion.v1",
    "report.closing.v1",
)
_PROMPT_ID_BY_BLOCK: Final = dict(zip(ReportBlockKind, _PROMPT_IDS, strict=True))
_CAP_FALLBACK = ReportNarrationDraft(
    content="측정 호출 상한에 도달했습니다.",
    numbers_used=(),
).model_dump_json()


class MeasurementLimitExceeded(RuntimeError):
    """외부 호출 100회를 넘기려 해 측정 결과를 폐기해야 하는 경우."""


@dataclass(frozen=True)
class CallObservation:
    """본문 없이 남기는 외부 콜 순번·벽시계·종단 관측."""

    sequence: int
    prompt_id: str
    started_offset_ms: int
    completed_offset_ms: int
    outcome: str
    finish_reason_state: str
    finish_reason_value: str | None


class CappedProvider:
    """외부 provider 호출 직전에 승인된 총량을 강제하는 측정 전용 래퍼."""

    def __init__(
        self,
        inner: LLMProvider,
        *,
        max_calls: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner = inner
        self._max_calls = max_calls
        self._clock = clock
        self._started_at: float | None = None
        self.external_calls = 0
        self.denied_calls = 0
        self.observations: list[CallObservation] = []

    @property
    def name(self) -> str:
        return self._inner.name

    async def complete(
        self, request: LLMRequest, context: ExecutionContext
    ) -> LLMResult:
        if self.external_calls >= self._max_calls:
            self.denied_calls += 1
            return LLMResult(
                outcome=CallOutcome.OK,
                text=_CAP_FALLBACK,
                provider="measurement-cap",
                model="none",
                usage=TokenUsage(tokens_in=0, tokens_out=0, cost_usd=0.0),
                latency_ms=0,
            )
        started_at = self._clock()
        if self._started_at is None:
            self._started_at = started_at
        self.external_calls += 1
        sequence = self.external_calls
        try:
            result = await self._inner.complete(request, context)
        except Exception as error:
            completed_at = self._clock()
            self.observations.append(
                CallObservation(
                    sequence=sequence,
                    prompt_id=request.prompt_id,
                    started_offset_ms=int((started_at - self._started_at) * 1000),
                    completed_offset_ms=int((completed_at - self._started_at) * 1000),
                    outcome=f"exception:{type(error).__name__}",
                    finish_reason_state="uncollected",
                    finish_reason_value=None,
                )
            )
            raise
        completed_at = self._clock()
        self.observations.append(
            CallObservation(
                sequence=sequence,
                prompt_id=request.prompt_id,
                started_offset_ms=int((started_at - self._started_at) * 1000),
                completed_offset_ms=int((completed_at - self._started_at) * 1000),
                outcome=result.outcome.value,
                finish_reason_state=result.finish_reason.state.value,
                finish_reason_value=result.finish_reason.value,
            )
        )
        return result


@dataclass
class PromptStats:
    """prompt_id 한 자리의 비민감 측정 집계."""

    success_latencies_ms: list[int] = field(default_factory=list)
    failure_latencies_ms: list[int] = field(default_factory=list)
    failures: Counter[str] = field(default_factory=Counter)
    failure_shapes: Counter[str] = field(default_factory=Counter)
    terminal_reasons: Counter[str] = field(default_factory=Counter)
    gate_rejections: Counter[str] = field(default_factory=Counter)
    attempt_outcomes: Counter[str] = field(default_factory=Counter)
    calls: int = 0


class GateRecorder:
    """프로덕션 게이트 판정은 바꾸지 않고 시도별 거부 사유만 센다."""

    def __init__(self) -> None:
        self.rejections: dict[str, Counter[str]] = defaultdict(Counter)

    def __call__(
        self,
        block: ReportBlock,
        allowed_numbers: frozenset[str],
        *,
        max_length: int,
        text_gate: DeterministicTextGate,
    ) -> ReportGateResult:
        result = check_report_block(
            block,
            allowed_numbers,
            max_length=max_length,
            text_gate=text_gate,
        )
        if not result.passed:
            self.rejections[_PROMPT_ID_BY_BLOCK[block.block_type]][result.reason] += 1
        return result


def _validate_limits(runs: int, max_calls: int) -> None:
    if not 1 <= runs <= _MAX_RUNS:
        raise ValueError(f"리포트 생성 횟수는 1..{_MAX_RUNS}이어야 한다")
    if not 1 <= max_calls <= _MAX_EXTERNAL_CALLS:
        raise ValueError(f"외부 LLM 콜 상한은 1..{_MAX_EXTERNAL_CALLS}이어야 한다")
    worst_case_calls = runs * len(_PROMPT_IDS) * _MAX_ATTEMPTS_PER_BLOCK
    if worst_case_calls > max_calls:
        raise ValueError(
            "블록당 재생성 3회를 포함한 최악 호출 수가 외부 LLM 콜 상한을 넘는다"
        )


def _source() -> ReportSourceSnapshot:
    evidence = (
        ReportEvidenceRef(
            source_table="feature_week",
            record_id="measurement-row",
            summary="학습 기록 근거",
        ),
    )
    return ReportSourceSnapshot(
        weakness_map=WeaknessMap(
            graph_version="graph-measurement",
            taxonomy_version="taxonomy-measurement",
            config_version="config-measurement",
            snapshot_hash="sha256:report-llm-measurement",
            cells={
                "language×concept": WeaknessCell(
                    acc=0.75,
                    n=12,
                    verdict=CellVerdict.OK,
                ),
                "reading×fact": WeaknessCell(
                    acc=0.25,
                    n=12,
                    verdict=CellVerdict.WEAK,
                    severity=0.75,
                ),
            },
        ),
        misconceptions=MisconceptionReport(
            by_area={"reading": {"scope_confusion": 2}}
        ),
        item_results=(
            ItemResult(
                item_id=UUID("00000000-0000-4000-8000-000000000981"),
                status=ProblemItemStatus.VERIFIED,
                attempt_no=1,
                difficulty_band=DifficultyBand.MEDIUM,
            ),
        ),
        metrics=(
            ReportMetricInput(
                metric_key="home_practice_rate",
                value=70,
                audience=ReportAudience.GUARDIAN,
                evidence=evidence,
            ),
            ReportMetricInput(
                metric_key="class_average",
                value=63,
                audience=ReportAudience.TEACHER_ONLY,
                evidence=evidence,
            ),
        ),
        cell_min_items=1,
    )


def _body(turn: int) -> dict[str, object]:
    source = _source()
    return {
        "report_id": str(uuid5(NAMESPACE_URL, f"report-llm-measurement:{turn}")),
        "guardian_ref": "guardian-measurement",
        "source": source.model_dump(mode="json"),
    }


def _quantile(values: Sequence[int], point: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * point))]


def _failure_shape(error: Exception) -> str:
    """응답값 없이 파싱 실패의 예외 종류와 필드 경로만 남긴다."""

    cause = error.__cause__
    if isinstance(error, FieldMissing) and isinstance(cause, ValidationError):
        locations = {
            ".".join(str(part) for part in item["loc"])
            for item in cause.errors(
                include_url=False,
                include_context=False,
                include_input=False,
            )
        }
        fields = "+".join(sorted(locations)) or "unknown"
        return f"field_missing:{fields}"
    if isinstance(error, ParseFailed):
        cause_name = type(cause).__name__ if cause is not None else "unknown"
        return f"parse_fail:{cause_name}"
    return type(error).__name__


def _classify_calls(
    calls: Sequence[CollectedCall],
    stats: dict[str, PromptStats],
    models: set[str],
) -> None:
    for collected in calls:
        record = collected.record
        if record.provider == "measurement-cap":
            continue
        prompt = stats[record.prompt_id]
        prompt.calls += 1
        if record.model:
            models.add(record.model)
        if record.outcome is not CallOutcome.OK:
            prompt.failures[record.outcome.value] += 1
            prompt.failure_latencies_ms.append(record.latency_ms)
            continue
        payload = collected.payload
        if payload is None:
            prompt.failures["payload_missing"] += 1
            prompt.failure_latencies_ms.append(record.latency_ms)
            continue
        try:
            parse(payload.response_masked, ReportNarrationDraft)
        except Exception as error:  # noqa: BLE001 — 파서 실패 타입을 측정 결과로 보존한다
            prompt.failures[type(error).__name__] += 1
            prompt.failure_shapes[_failure_shape(error)] += 1
            prompt.failure_latencies_ms.append(record.latency_ms)
            continue
        prompt.success_latencies_ms.append(record.latency_ms)


def _classify_sections(data: dict[str, object], stats: dict[str, PromptStats]) -> int:
    regenerations = 0
    sections = data.get("sections")
    if not isinstance(sections, list):
        return regenerations
    for section in sections:
        if not isinstance(section, dict):
            continue
        prompt_id = section.get("prompt_id")
        if not isinstance(prompt_id, str):
            continue
        attempts = section.get("attempts")
        if isinstance(attempts, int):
            regenerations += max(0, attempts - 1)
            status = section.get("status")
            reason = section.get("reason")
            if status == "ready":
                outcome = f"success_{attempts}"
            elif attempts == _MAX_ATTEMPTS_PER_BLOCK and reason == "generation_exhausted":
                outcome = "exhausted_3"
            else:
                outcome = f"terminal_{attempts}"
            stats[prompt_id].attempt_outcomes[outcome] += 1
        if section.get("status") != "ready":
            reason = section.get("reason")
            if isinstance(reason, str) and reason:
                stats[prompt_id].terminal_reasons[reason] += 1
    return regenerations


def _quantile_text(values: Sequence[int], point: float) -> str:
    if not values:
        return "-"
    return f"{_quantile(values, point) / 1000:.3f}s"


def _counter_text[CounterKey: (str, int)](values: Counter[CounterKey]) -> str:
    if not values:
        return "none"
    return ",".join(f"{key}={values[key]}" for key in sorted(values, key=str))


def _timeline_phase(observation: CallObservation, total_ms: int) -> str:
    """첫 외부 콜 시작부터 마지막 종료까지의 벽시계를 3등분한다."""

    if total_ms <= 0:
        return "front"
    position = observation.started_offset_ms / total_ms
    if position < 1 / 3:
        return "front"
    if position < 2 / 3:
        return "middle"
    return "back"


def _ascii_cell(value: str | None) -> str:
    if value is None:
        return "-"
    return value.encode("ascii", errors="backslashreplace").decode("ascii")


def _timeline_lines(observations: Sequence[CallObservation]) -> list[str]:
    if not observations:
        return ["timeline_phases=none"]
    total_ms = max(item.completed_offset_ms for item in observations)
    phases = Counter(_timeline_phase(item, total_ms) for item in observations)
    lines = [
        f"timeline_phases={_counter_text(phases)} total_ms={total_ms}",
        "| call_no | started_ms | phase | prompt_id | outcome | "
        "finish_reason_state | finish_reason |",
        "| ---: | ---: | --- | --- | --- | --- | --- |",
    ]
    lines.extend(
        f"| {item.sequence} | {item.started_offset_ms} | "
        f"{_timeline_phase(item, total_ms)} | `{item.prompt_id}` | "
        f"{_ascii_cell(item.outcome)} | {_ascii_cell(item.finish_reason_state)} | "
        f"{_ascii_cell(item.finish_reason_value)} |"
        for item in observations
    )
    return lines


def _render_report(
    stats: dict[str, PromptStats],
    *,
    reports: int,
    external_calls: int,
    regenerations: int,
    statuses: Counter[str],
    models: set[str],
    observations: Sequence[CallObservation] = (),
) -> str:
    model = ",".join(sorted(models)) or "unknown"
    lines = [
        f"model: {model}",
        f"reports={reports} external_calls={external_calls} "
        f"regenerations={regenerations} statuses={_counter_text(statuses)}",
        "| prompt_id | success_n | success_p50 | success_p95 | success_max | "
        "failure_n | failure_p50 | failure_p95 | failure_max | failures | "
        "failure_shapes | template_only_reasons | gate_rejections | "
        "attempt_distribution |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | "
        "--- | --- | --- | --- | --- |",
    ]
    for prompt_id in _PROMPT_IDS:
        item = stats[prompt_id]
        success = item.success_latencies_ms
        failure = item.failure_latencies_ms
        lines.append(
            f"| `{prompt_id}` | {len(success)} | {_quantile_text(success, 0.50)} | "
            f"{_quantile_text(success, 0.95)} | "
            f"{f'{max(success) / 1000:.3f}s' if success else '-'} | "
            f"{len(failure)} | {_quantile_text(failure, 0.50)} | "
            f"{_quantile_text(failure, 0.95)} | "
            f"{f'{max(failure) / 1000:.3f}s' if failure else '-'} | "
            f"{_counter_text(item.failures)} | "
            f"{_counter_text(item.failure_shapes)} | "
            f"{_counter_text(item.terminal_reasons)} | "
            f"{_counter_text(item.gate_rejections)} | "
            f"{_counter_text(item.attempt_outcomes)} |"
        )
    lines.extend(["", *_timeline_lines(observations)])
    report = "\n".join(lines) + "\n"
    report.encode("ascii")
    return report


def _emit_report(report: str, output_path: Path) -> None:
    """콘솔과 무관하게 UTF-8 결과를 원자적으로 먼저 남긴다."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary.write_text(report, encoding="utf-8", newline="\n")
    temporary.replace(output_path)
    print(report, end="")


async def _run_measurement(*, runs: int, max_calls: int, output_path: Path) -> None:
    collector = LlmCallCollector(max_calls_per_run=15)
    real_provider = build_report_llm_provider(
        ReportProviderSettings(llm_provider="openai_compat", _env_file=None)
    )
    capped = CappedProvider(real_provider, max_calls=max_calls)
    stats: dict[str, PromptStats] = defaultdict(PromptStats)
    statuses: Counter[str] = Counter()
    models: set[str] = set()
    regenerations = 0
    completed = 0
    gate_recorder = GateRecorder()

    reset_report_store()
    reset_report_narrator()
    set_report_store(InMemoryReportStore())
    set_report_narrator(build_report_narrator(provider=capped, recorder=collector))
    quiet_console = ConsoleSettings(_env_file=None)
    try:
        # lifespan을 일부러 시작하지 않는다. #431 startup 배선은 별도 400 preflight로
        # 검증했고, 측정에서는 무관한 PG 드레인·PostgreSQL을 띄우지 않아야 한다.
        transport = ASGITransport(app=create_app(), raise_app_exceptions=False)
        with (
            patch("ai.runtime.console.get_console_settings", return_value=quiet_console),
            patch("ai.api.console.get_console_settings", return_value=quiet_console),
            patch("ai.report.narration.check_report_block", new=gate_recorder),
        ):
            async with AsyncClient(transport=transport, base_url="http://measurement") as client:
                for turn in range(1, runs + 1):
                    response = await client.post(
                        "/v1/reports",
                        headers={
                            "X-Tenant-Id": "measurement",
                            "X-Request-Id": f"report-measurement-{turn}",
                        },
                        json=_body(turn),
                    )
                    if response.status_code != 200:
                        raise RuntimeError(
                            f"리포트 {turn}회차 HTTP {response.status_code} — 측정을 중단한다"
                        )
                    envelope = response.json()
                    execution_id = UUID(envelope["meta"]["execution_id"])
                    data = envelope["data"]
                    calls = collector.take(execution_id)
                    _classify_calls(calls, stats, models)
                    regenerations += _classify_sections(data, stats)
                    statuses[str(data.get("status", "missing"))] += 1
                    completed += 1
                    if capped.denied_calls:
                        raise MeasurementLimitExceeded(
                            f"외부 LLM {max_calls}콜 뒤 추가 호출이 필요했다 — "
                            f"리포트 {completed}회에서 중단"
                        )
    finally:
        reset_report_narrator()
        reset_report_store()

    for prompt_id, rejections in gate_recorder.rejections.items():
        stats[prompt_id].gate_rejections.update(rejections)
    report = _render_report(
        stats,
        reports=completed,
        external_calls=capped.external_calls,
        regenerations=regenerations,
        statuses=statuses,
        models=models,
        observations=capped.observations,
    )
    _emit_report(report, output_path)


def run_measurement(*, runs: int, max_calls: int, output_path: Path = _DEFAULT_OUTPUT) -> None:
    """인메모리 저장소와 공개 HTTP 경로로 승인된 한 회차를 실행한다."""

    _validate_limits(runs, max_calls)
    if not real_llm_optin():
        raise SystemExit(
            f"실 LLM 미실행: {REAL_LLM_OPTIN_ENV}=1이 이 명령에 명시되지 않았다"
        )
    asyncio.run(
        _run_measurement(runs=runs, max_calls=max_calls, output_path=output_path)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="리포트 생성 실 LLM 콜당 지연 측정")
    parser.add_argument("--runs", type=int, default=6)
    parser.add_argument("--max-calls", type=int, default=100)
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    args = parser.parse_args()
    run_measurement(runs=args.runs, max_calls=args.max_calls, output_path=args.output)


if __name__ == "__main__":
    main()


__all__ = [
    "CallObservation",
    "CappedProvider",
    "MeasurementLimitExceeded",
    "main",
    "run_measurement",
]
