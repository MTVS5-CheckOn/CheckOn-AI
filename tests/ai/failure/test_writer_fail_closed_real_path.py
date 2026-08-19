"""writer 의 **불변식 3 방어가 실제로 돈다** — 실 `redact()` 로 (99 #103 ㉡).

🔴 **결손은 「검사가 없다」가 아니라 「가장 무거운 불변식의 방어가 한 번도 안 돌았다」였다.**
PR-06 의 줄 추적: `provider.py:300`(`raise RedactionBlockedError`)이 검사 3475건에서
**미실행**. 그 세 줄이 학부모에게 갈 본문을 만드는 경로의 **유일한 문지기**다.

⚠ `RedactionBlockedError` 를 참조하는 검사는 여럿 있는데(6파일) **전부 다른 방식**이었다:
  · 예외 **인스턴스를 주입**하거나(`_plan_outcome_harness` · `test_failure_vs_judgement` ·
    `test_ledger_survives_every_failure` · `test_counsel_graph`)
  · `redact` 를 **monkeypatch** 로 갈거나(`test_counsel_plan_grounding` — planner 축)
  · 🔴 실 writer 의 fail-closed 순서를 **대역에 복제**하거나
    (`test_refine_failure_http::_RedactingWriter` — 실 `assemble_prompt`+실 `redact()` 지만
    `GatewayDraftWriter` 가 **아니다**)
⇒ **실 writer 의 그 세 줄이 지워져도 전부 green 이었다.** 99 #36 이 등재한
「검사가 프로덕션이 안 하는 일을 대신 해 줬다」와 같은 계열이다.

🔴 **이 파일은 그 자리 하나를 실물로 태운다** — 갈아도 되는 건 게이트웨이 **아래**(provider)
뿐이고, 그마저 **안 불려야** 한다(전송 **전에** 막히므로).
"""

from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers.counsel import reset_counsel_stores, set_counsel_provider
from ai.composition.counsel.assembly import build_counsel_gateway, build_gateway_writer
from ai.composition.counsel.prompt import assemble_prompt
from ai.composition.counsel.provider import (
    GatewayPlanner,
    RedactionBlockedError,
)
from ai.contracts.composition import DraftContext, EvidenceFact
from ai.contracts.counsel import WIRE_STATUS_REASONS
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import CallOutcome, LLMRequest, LLMResult, TokenUsage
from ai.db.store_factory import reset_shared_agent_runtime
from ai.runtime.redaction import redact

#: 🔴 **실측 오탐 어절** — 「성씨 1자 + 이름 2자」 휴리스틱이 평범한 활용형을 인명 후보로
#: 잡는다(2026-08-19 재현). ⚠ **실명을 넣지 않는다** — 넣으면 *"진짜 실명을 막는가"* 라는
#: **다른 축**을 재게 된다. 여기서 재는 것은 「불확실하면 전송 전에 멈추는가」다.
_UNCERTAIN_SUMMARY: Final = "가정에서도 같은 방향으로 지켜봐 주시면 좋겠습니다"
_CLEAN_SUMMARY: Final = "6월 지문 42개·312문항"

_HEADERS: Final = {
    "X-Tenant-Id": "t_fail_closed",
    "X-Request-Id": "rq-fail-closed",
    "Idempotency-Key": "iq_fail_closed",
}


def _request_body() -> dict[str, Any]:
    """🔴 계약 §4-① 예시를 복제하지 않는다 — 정본을 **파일로** 읽는다."""
    target = (
        Path(__file__).resolve().parents[1] / "integration" / "test_counsel_router.py"
    )
    spec = importlib.util.spec_from_file_location("_counsel_router_contract", target)
    assert spec is not None and spec.loader is not None, target
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    body: dict[str, Any] = module._REQUEST
    return dict(body)


def _context(summary: str) -> DraftContext:
    """라우터 정본으로 컨텍스트를 만들고 **fact 요약문만** 바꾼다."""
    from ai.api.routers.counsel import _draft_context  # noqa: PLC0415
    from ai.contracts.counsel import CounselDraftRequest  # noqa: PLC0415

    base = _draft_context(CounselDraftRequest.model_validate(_request_body()))
    return base.model_copy(
        update={"facts": (EvidenceFact(label="관찰", value=summary),)}
    )


def _execution_context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=uuid4(),
        tenant_id="t_fail_closed",
        capability=Capability.COMPOSITION,
        input_snapshot_hash="sha256:" + "a" * 64,
        versions=VersionSet(
            pipeline_version="0.1",
            engine_version="counsel-pack-0.1",
            schema_version="0.1",
            contract_version="0.1",
        ),
    )


class _CountingProvider:
    """호출되면 **센다** — 이 검사에서는 0이어야 한다(전송 전에 막히므로)."""

    name = "counting"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, request: LLMRequest, context: ExecutionContext
    ) -> LLMResult:
        del request, context
        self.calls += 1
        return LLMResult(
            text="보내면 안 되는 응답",
            provider=self.name,
            model="counting-model",
            usage=TokenUsage(tokens_in=0, tokens_out=0, cost_usd=0.0),
            latency_ms=0,
            outcome=CallOutcome.OK,
        )


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    reset_shared_agent_runtime()
    reset_counsel_stores()
    yield
    reset_shared_agent_runtime()
    reset_counsel_stores()


# ── 1 · 🔴 전송 **전에** 막는다 ───────────────────────────────────


def test_an_uncertain_fact_stops_the_call_before_it_is_sent() -> None:
    """🔴 **「막았다」가 아니라 「전송 전에 막았다」가 불변식 3이다.**

    호출 수를 안 세면 절반만 잰 것이다 — 보내 놓고 결과를 버리는 것과 구분이 안 된다.

    🔴 **`GatewayDraftWriter` 를 대역으로 갈지 않는다** — 갈면 이 검사는 종전 검사들과
    같은 것이 된다(실 코드의 세 줄을 지워도 green). 갈아도 되는 건 게이트웨이 **아래**뿐이다.
    """
    provider = _CountingProvider()
    writer = build_gateway_writer(provider)

    with pytest.raises(RedactionBlockedError):
        asyncio.run(
            writer.write(
                context=_context(_UNCERTAIN_SUMMARY),
                execution_context=_execution_context(),
            )
        )

    assert provider.calls == 0, (
        f"마스킹이 불확실한데 LLM 을 {provider.calls}회 불렀다 — "
        "fail-closed 는 **전송 전**에 막는 것이다(불변식 3)"
    )


# ── 2 · 그 예외가 **실제 redact()** 에서 온다 ─────────────────────


def test_the_block_comes_from_the_real_redact_not_a_stub() -> None:
    """🔴 **주입 0건** — 예외를 만든 것은 실 `assemble_prompt` + 실 `redact()` 다.

    ⚠ 이 단언이 없으면 «`RedactionBlockedError` 가 났다» 는 사실이 **누가 던졌는지**를
    안 말한다. 종전 검사 6파일이 전부 그 자리에서 멈춰 있었다(99 #36 계열).
    """
    context = _context(_UNCERTAIN_SUMMARY)
    prompt = assemble_prompt(context, (), "", "", "")

    #: ⓐ 프롬프트에 그 문면이 실제로 실린다 — 안 실리면 아래가 우연이다.
    assert _UNCERTAIN_SUMMARY in prompt, "fact 요약문이 프롬프트에 안 실렸다"
    #: ⓑ 실 `redact()` 가 그 프롬프트에서 `uncertain` 을 낸다 — **주입이 아니다.**
    assert redact(prompt).uncertain, (
        "실 redact() 가 uncertain 을 안 낸다 — 휴리스틱이 바뀌었으면 "
        "_UNCERTAIN_SUMMARY 를 다시 골라야 한다(이 검사의 전제다)"
    )
    #: ⓒ 그 조합이 실 writer 에서 예외가 된다.
    with pytest.raises(RedactionBlockedError):
        asyncio.run(
            build_gateway_writer(_CountingProvider()).write(
                context=context, execution_context=_execution_context()
            )
        )


# ── 3 · HTTP 결말 ─────────────────────────────────────────────────


def test_the_wire_result_is_llm_failed_with_redaction_blocked() -> None:
    """와이어 결말이 어휘 안에 있다(PR-03·04 축).

    ⚠ `redaction_blocked` 는 `_FAILURE_WIRE` 가 `llm_failed` 로 옮기는 접두다.
    """

    class _Provider:
        def __init__(self) -> None:
            self.gateway_provider = _CountingProvider()
            gateway = build_counsel_gateway(self.gateway_provider)
            self._planner = GatewayPlanner(gateway)
            self._writer = build_gateway_writer(self.gateway_provider)

        async def plan(self, **kwargs: object) -> dict[str, list[str]]:
            del kwargs
            return {}

        async def write(self, **kwargs: object) -> str:
            return await self._writer.write(**kwargs)  # type: ignore[arg-type]

    provider = _Provider()
    set_counsel_provider(provider)

    body = _request_body()
    body["context"] = {
        **body["context"],
        "facts": [{"record_id": "le_1", "summary": _UNCERTAIN_SUMMARY}],
    }
    with TestClient(create_app()) as client:
        posted: httpx.Response = client.post(
            "/v1/counsel/drafts", json=body, headers=_HEADERS
        )
        assert posted.status_code == 202, posted.text
        fetched: httpx.Response = client.get(
            f"/v1/counsel/drafts/{posted.json()['data']['job_id']}", headers=_HEADERS
        )

    result = fetched.json()["data"]["result"]
    assert result["draft_status"] == "llm_failed", result
    assert result["status_reason"] == "redaction_blocked", result
    assert result["status_reason"] in WIRE_STATUS_REASONS, result
    assert provider.gateway_provider.calls == 0, (
        f"라우터 경로에서도 전송이 있으면 안 된다(호출 {provider.gateway_provider.calls}회)"
    )


# ── 4 · 🔴 정상 fact 는 통과한다 ──────────────────────────────────


def test_a_clean_fact_still_goes_through() -> None:
    """🔴 없으면 「전부 막는 검사」와 구분이 안 된다.

    ⚠ 같은 장치·같은 경로에서 **문면만** 바꾼다 — 그래야 차이의 원인이 문면 하나로 좁혀진다.
    """
    context = _context(_CLEAN_SUMMARY)
    assert not redact(assemble_prompt(context, (), "", "", "")).uncertain, (
        "정상 요약문인데 uncertain 이다 — 이 검사의 대조군이 성립하지 않는다"
    )

    provider = _CountingProvider()
    text = asyncio.run(
        build_gateway_writer(provider).write(
            context=context, execution_context=_execution_context()
        )
    )

    assert provider.calls == 1, f"정상인데 전송이 {provider.calls}회다"
    assert text, "정상 경로인데 본문이 비었다"


# ── 5 · 재생성 피드백이 **조립된 뒤에도** 이 경로를 안 막는다 ─────


def test_no_gate_feedback_blocks_the_assembled_prompt() -> None:
    """🔴 99 #102 의 회귀 — 재생성 지시가 프롬프트에 **붙은 뒤에도** 통과해야 한다.

    ⚠ **PR-05 의 `test_every_gate_feedback_text_can_actually_be_sent` 와 층이 다르다** —
    그쪽은 문구를 **단독으로** `redact()` 에 넣고, 여기는 **조립된 프롬프트**를 넣는다.
    🔴 **그 차이가 실제로 있다**: 이 휴리스틱은 **인접 어절 결합**으로도 걸린다
    (실측 8/19: `"다른 학생에 비해"` 는 **어절 단독으로는 0건**인데 붙이면 `uncertain`).
    ⇒ 문구가 단독으로 깨끗해도 **템플릿·컨텍스트와 만나는 자리**에서 걸릴 수 있다.

    ⚠ 겹치는 부분(문구 자체의 통과)은 여기서 다시 재지 않는다 — 그쪽 검사를 가리킨다.
    """
    from ai.composition.gate_feedback import load_gate_feedback  # noqa: PLC0415

    context = _context(_CLEAN_SUMMARY)
    feedback = load_gate_feedback()

    blocked = sorted(
        reason
        for reason, text in feedback.instructions.items()
        if redact(assemble_prompt(context, (), text, "", "")).uncertain
    )
    assert not blocked, (
        f"재생성 지시가 조립된 프롬프트를 막는다: {blocked} — "
        "그 사유로 게이트가 걸리면 2회차가 통째로 미전송된다(99 #102)"
    )
    #: 절단 가드 — 대조군이 애초에 깨끗해야 위 단언이 뜻을 갖는다.
    assert not redact(assemble_prompt(context, (), "", "", "")).uncertain
    assert len(feedback.instructions) >= 9, "피드백 문구를 거의 안 읽었다"
