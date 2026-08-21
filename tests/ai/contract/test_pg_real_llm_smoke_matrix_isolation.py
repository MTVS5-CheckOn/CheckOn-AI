"""실 LLM을 부르지 않고 매트릭스의 영역별 실패 격리를 고정한다."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from ai.contracts.taxonomy import AreaTag

_SMOKE_PATH = Path(__file__).resolve().parents[1] / "integration" / "test_pg_real_llm_smoke.py"
_MODULE_NAME = "_pg_real_llm_smoke_matrix_isolation"


def _smoke_module() -> Any:  # noqa: ANN401 — 실행 스크립트의 비공개 하네스를 검증한다.
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _SMOKE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def test_matrix_keeps_other_area_rows_when_one_area_raises() -> None:
    smoke = _smoke_module()
    observation = asyncio.run(smoke._fake_cli_observation())
    status_reason = "화법과작문·매체 생성 자료를 승인 근거로 고정할 수 없다"

    async def fake_run(area_tag: AreaTag) -> Any:  # noqa: ANN401 — 동적 하네스 산출 타입.
        if area_tag is AreaTag.SPEECH_WRITING:
            raise smoke._UnexpectedSmokeOutcome(
                area_tag,
                "RejectedInsufficientOutcome",
                status_reason,
            )
        return replace(observation, area_tag=area_tag)

    smoke.run_real_llm_smoke = fake_run

    rows, refine_rows, failures = asyncio.run(smoke._run_matrix(repetitions=1))

    surviving = set(AreaTag) - {AreaTag.SPEECH_WRITING}
    assert {row.area_tag for row in rows} == surviving
    assert {row.area_tag for row in refine_rows} == surviving
    assert len(failures) == 1
    assert failures[0].area == AreaTag.SPEECH_WRITING.value
    assert failures[0].exception_type == "_UnexpectedSmokeOutcome"
    assert failures[0].status_reason == status_reason

    rendered = smoke._render_failures(failures)
    assert status_reason in rendered
    assert "RAW_COMPLETION_MUST_NOT_PRINT" not in rendered
    assert "secret.example" not in rendered

    unsafe = smoke._safe_failure_from_exception(
        AreaTag.MEDIA,
        RuntimeError(r"C:\private\response.json https://secret.example 원문"),
    )
    assert unsafe.status_reason == "unknown"
    assert "private" not in smoke._render_failures((unsafe,))
    assert "secret.example" not in smoke._render_failures((unsafe,))
