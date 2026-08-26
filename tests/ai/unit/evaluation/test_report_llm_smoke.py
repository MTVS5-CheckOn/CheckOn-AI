"""리포트 실 LLM 측정기의 opt-in·승인량·통계 경계."""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from pathlib import Path
from uuid import UUID

import pytest
from fake_provider import FakeProvider

from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import FieldMissing, LLMRequest, ModelRole, ParseFailed
from ai.contracts.report_narration import ReportNarrationDraft
from ai.evaluation import report_llm_smoke
from ai.evaluation.report_llm_smoke import CappedProvider
from ai.llm.structured import parse


def _context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=UUID("00000000-0000-4000-8000-000000000982"),
        tenant_id="measurement",
        capability=Capability.COMPOSITION,
        input_snapshot_hash="sha256:measurement",
        versions=VersionSet(
            pipeline_version="0.1",
            engine_version="0.1",
            schema_version="0.1",
            contract_version="0.1",
        ),
    )


def _request() -> LLMRequest:
    return LLMRequest(
        role=ModelRole.REPORTER,
        prompt="측정",
        prompt_id="report.greeting.v1",
        prompt_version="v1",
    )


def test_measurement_limits_include_three_attempts_for_each_of_five_blocks() -> None:
    report_llm_smoke._validate_limits(6, 100)

    with pytest.raises(ValueError, match="재생성 3회"):
        report_llm_smoke._validate_limits(7, 100)
    with pytest.raises(ValueError):
        report_llm_smoke._validate_limits(6, 101)


def test_lower_call_limit_cannot_cover_the_worst_case_regenerations() -> None:
    with pytest.raises(ValueError, match="재생성 3회"):
        report_llm_smoke._validate_limits(6, 89)


def test_capped_provider_never_makes_an_external_call_past_the_limit() -> None:
    inner = FakeProvider(("{}", "{}", "{}"), name="real-shaped")
    capped = CappedProvider(inner, max_calls=2)

    asyncio.run(capped.complete(_request(), _context()))
    asyncio.run(capped.complete(_request(), _context()))
    fallback = asyncio.run(capped.complete(_request(), _context()))

    assert capped.external_calls == 2
    assert len(inner.requests) == 2
    assert capped.denied_calls == 1
    assert fallback.provider == "measurement-cap"


def test_capped_provider_records_order_timeline_and_finish_reason() -> None:
    provider = FakeProvider(("{}", "{}", "{}"), name="observed")
    moments = iter((0.00, 0.01, 0.05, 0.06, 0.09, 0.12))
    capped = CappedProvider(provider, max_calls=3, clock=lambda: next(moments))

    asyncio.run(capped.complete(_request(), _context()))
    asyncio.run(capped.complete(_request(), _context()))
    asyncio.run(capped.complete(_request(), _context()))

    report = report_llm_smoke._render_report(
        defaultdict(report_llm_smoke.PromptStats),
        reports=0,
        external_calls=3,
        regenerations=0,
        statuses=Counter(),
        models=set(),
        observations=capped.observations,
    )

    assert [item.sequence for item in capped.observations] == [1, 2, 3]
    assert "timeline_phases=back=1,front=1,middle=1 total_ms=120" in report
    assert "| 1 | 0 | front | `report.greeting.v1` | ok | uncollected | - |" in report
    assert "| 2 | 50 | middle | `report.greeting.v1` | ok | uncollected | - |" in report
    assert "| 3 | 90 | back | `report.greeting.v1` | ok | uncollected | - |" in report


def test_measurement_refuses_to_start_without_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(report_llm_smoke, "real_llm_optin", lambda: False)
    monkeypatch.setattr(
        report_llm_smoke,
        "build_report_llm_provider",
        lambda *args, **kwargs: pytest.fail("opt-in 전에 provider를 만들었다"),
    )

    with pytest.raises(SystemExit, match="실 LLM 미실행"):
        report_llm_smoke.run_measurement(runs=1, max_calls=15)


def test_measurement_uses_http_without_starting_unrelated_app_lifespans(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    draft = ReportNarrationDraft(
        content="학습 기록을 함께 살펴보겠습니다.",
        numbers_used=(),
    ).model_dump_json()
    provider = FakeProvider((draft,) * 5, name="fake-measurement")
    monkeypatch.setattr(
        report_llm_smoke,
        "build_report_llm_provider",
        lambda *args, **kwargs: provider,
    )

    asyncio.run(
        report_llm_smoke._run_measurement(
            runs=1,
            max_calls=15,
            output_path=tmp_path / "fake-report.txt",
        )
    )

    output = capsys.readouterr().out
    assert "reports=1 external_calls=5" in output
    assert len(provider.requests) == 5


def test_measurement_report_is_ascii_safe_when_samples_are_empty(
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = report_llm_smoke._render_report(
        defaultdict(report_llm_smoke.PromptStats),
        reports=0,
        external_calls=0,
        regenerations=0,
        statuses=Counter(),
        models=set(),
    )

    assert "| `report.greeting.v1` | 0 | - | - | - |" in report
    report.encode("ascii")
    assert capsys.readouterr().out == ""


def test_measurement_writes_utf8_result_before_console_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_path = tmp_path / "report.txt"

    def fail_console(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise UnicodeEncodeError("cp949", "-", 0, 1, "측정용")

    monkeypatch.setattr("builtins.print", fail_console)
    with pytest.raises(UnicodeEncodeError):
        report_llm_smoke._emit_report("ascii-only\n", output_path)

    assert output_path.read_text(encoding="utf-8") == "ascii-only\n"


def test_section_statistics_split_success_and_exhaustion_by_attempt() -> None:
    stats: dict[str, report_llm_smoke.PromptStats] = defaultdict(
        report_llm_smoke.PromptStats
    )

    regenerations = report_llm_smoke._classify_sections(
        {
            "sections": [
                {
                    "prompt_id": "report.fact.v1",
                    "status": "ready",
                    "attempts": 1,
                },
                {
                    "prompt_id": "report.fact.v1",
                    "status": "ready",
                    "attempts": 2,
                },
                {
                    "prompt_id": "report.fact.v1",
                    "status": "ready",
                    "attempts": 3,
                },
                {
                    "prompt_id": "report.fact.v1",
                    "status": "template_only",
                    "attempts": 3,
                    "reason": "generation_exhausted",
                }
            ]
        },
        stats,
    )

    assert regenerations == 5
    assert stats["report.fact.v1"].terminal_reasons == {
        "generation_exhausted": 1
    }
    assert stats["report.fact.v1"].attempt_outcomes == {
        "success_1": 1,
        "success_2": 1,
        "success_3": 1,
        "exhausted_3": 1,
    }


@pytest.mark.parametrize(
    ("payload", "error_type", "shape"),
    [
        ("not-json", ParseFailed, "parse_fail:JSONDecodeError"),
        ("{}", FieldMissing, "field_missing:content+numbers_used"),
    ],
)
def test_failure_shape_keeps_only_exception_type_and_field_names(
    payload: str,
    error_type: type[Exception],
    shape: str,
) -> None:
    with pytest.raises(error_type) as caught:
        parse(payload, ReportNarrationDraft)

    assert report_llm_smoke._failure_shape(caught.value) == shape
