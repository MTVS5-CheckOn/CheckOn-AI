"""실 로컬 LLM 스모크 — 어댑터가 실서버와 1회 왕복하는가 (B-5 어댑터 · integration).

PG 왕복(test_pg_store_roundtrip)과 같은 패턴: `integration` 마커라 기본 실행에서 제외
(pyproject addopts `-m 'not integration'`). env에 `OPENAI_*`가 잡혀 있고 서버가
살아 있을 때만 한국어 한 문장을 실제로 받아 본다 — 없으면 skip(거짓 실패 방지).

PR 전 로컬 검증에서는 명시적 opt-in이 없어 skip한다(Fake/mock 결정론 검증은 단위 테스트가 담당).
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import CallOutcome, LlmError, LLMRequest, ModelRole
from ai.llm.providers.openai_compat import OpenAICompatProvider, get_llm_settings
from ai.runtime.real_llm import real_llm_skip_reason

pytestmark = pytest.mark.integration


def _context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=UUID("00000000-0000-4000-8000-00000000000a"),
        tenant_id="teacher_alias_smoke",
        capability=Capability.COMPOSITION,
        input_snapshot_hash="sha256:smoke",
        versions=VersionSet(
            pipeline_version="v2.1",
            engine_version="rules-1.0",
            schema_version="0.1",
            contract_version="0.1",
            prompt_version="v0.1",
        ),
    )


def test_local_server_single_roundtrip() -> None:
    """실서버가 있으면 OK + 비어있지 않은 한국어 응답. 없으면 skip."""
    settings = get_llm_settings()
    # 🔴 **opt-in 없이는 안 부른다**(99 #32) — 종전 조건은 `.env`가 덮으면 열렸다.
    reason = real_llm_skip_reason(settings.openai_base_url)
    if reason is not None:
        pytest.skip(reason)

    provider = OpenAICompatProvider(settings=settings)
    request = LLMRequest(
        role=ModelRole.GENERATOR,
        prompt="한 문장으로 짧게 인사해 주세요.",
        prompt_id="smoke/greeting",
        prompt_version="v0.1",
    )
    try:
        result = asyncio.run(provider.complete(request, _context()))
    except LlmError as exc:
        pytest.skip(f"로컬 LLM 미가용 — {type(exc).__name__}")

    assert result.outcome is CallOutcome.OK
    assert result.text is not None and result.text.strip()
