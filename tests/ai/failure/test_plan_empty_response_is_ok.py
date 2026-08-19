"""빈 plan 응답은 **적법한 답**이다 — `plan_outcome=ok` (99 ㉲의 잃어버린 절반).

🔴 **PR-05 의 형제 결함이다.** `GatewayPlanner.plan` 의 «빈 응답 = 모델이 비워 뒀다»
판정(`if not text: return {}`)이 **한 번도 실행되지 않았다**(실측 8/19 · 줄 추적).
실 provider 는 빈 content 를 `ParseFailed` 로 올리고 게이트웨이는 그걸 **예외로
re-raise** 하므로 `result.outcome` 검사에는 애초에 안 온다 — `GatewayDraftWriter` 에서
확인한 것과 **정확히 같은 이유**다(결정 로그 125).

⇒ 그 사건은 `graph.py` 의 `except LlmError` 로 떨어져 **`plan_outcome=llm_failed`** 로
계상됐다. **초안은 안 깨진다**(두 길 다 무강조 진행) — 깨지는 것은 **집계 축**이다:
*"plan 이 얼마나 실패하나"* 를 세면 **정상 동작이 장애로 잡힌다.** 99 ㉲가 셋을 가르려고
만든 작업의 절반이 조용히 되돌아가 있었다.

🔴 **전송 장애는 안 건드린다** — `timeout`·`provider_error` 는 여전히 `llm_failed` 다.
그 전제가 아래 검사 2 다(PR-05 의 같은 자리와 대칭).
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, Final

import httpx
import pytest
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers.counsel import reset_counsel_stores, set_counsel_provider
from ai.composition.counsel.assembly import build_counsel_gateway
from ai.composition.counsel.provider import (
    GatewayDraftWriter,
    GatewayPlanner,
    PlanUnparsedError,
)
from ai.contracts.composition import PlanOutcome
from ai.contracts.execution import ExecutionContext
from ai.contracts.llm import (
    CallOutcome,
    LLMRequest,
    LLMResult,
    LlmTimeout,
    ParseFailed,
    TokenUsage,
)
from ai.db.store_factory import reset_shared_agent_runtime

_HEADERS: Final = {
    "X-Tenant-Id": "t_plan_empty",
    "X-Request-Id": "rq-plan-empty",
    "Idempotency-Key": "iq_plan_empty",
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


def _grounded_draft() -> str:
    from counsel_text import DEFAULT_DRAFT  # noqa: PLC0415

    text: str = DEFAULT_DRAFT
    return text


class _ScriptedLlmProvider:
    """plan 호출과 write 호출에 **각각 다른 대본**을 준다.

    🔴 게이트웨이·`GatewayPlanner`·`GatewayDraftWriter` 는 **실제 코드**가 돈다 —
    provider 만 대역이다(통째로 갈면 이 PR 이 고친 층을 안 지난다).
    ⚠ 두 역할이 같은 role(`counselor`)이라 **`prompt_id` 로 가른다.**
    """

    name = "scripted-plan"

    def __init__(
        self, plan_step: Exception | str, write_script: Sequence[Exception | str]
    ) -> None:
        self._plan_step = plan_step
        self._write_script = list(write_script)
        self.plan_calls = 0
        self.write_calls = 0

    async def complete(
        self, request: LLMRequest, context: ExecutionContext
    ) -> LLMResult:
        del context
        if "plan" in request.prompt_id:
            self.plan_calls += 1
            step: Exception | str = self._plan_step
        else:
            #: 대본이 끝나면 마지막 것을 반복한다 — 「계속 비는 모델」을 그렇게 만든다.
            step = self._write_script[
                min(self.write_calls, len(self._write_script) - 1)
            ]
            self.write_calls += 1
        if isinstance(step, Exception):
            raise step
        return LLMResult(
            text=step,
            provider=self.name,
            model="scripted-model",
            usage=TokenUsage(tokens_in=0, tokens_out=0, cost_usd=0.0),
            latency_ms=0,
            outcome=CallOutcome.OK,
        )


class _ScriptedProvider:
    """실물 `GatewayPlanner` + `GatewayDraftWriter` 를 한 객체로 묶는다."""

    def __init__(
        self,
        plan_step: Exception | str,
        write_script: Sequence[Exception | str] | None = None,
    ) -> None:
        self.llm = _ScriptedLlmProvider(
            plan_step, write_script or [_grounded_draft()]
        )
        gateway = build_counsel_gateway(self.llm)
        self._planner = GatewayPlanner(gateway)
        self._writer = GatewayDraftWriter(gateway)

    async def plan(self, **kwargs: object) -> dict[str, list[str]]:
        return await self._planner.plan(**kwargs)  # type: ignore[arg-type]

    async def write(self, **kwargs: object) -> str:
        return await self._writer.write(**kwargs)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    reset_shared_agent_runtime()
    reset_counsel_stores()
    yield
    reset_shared_agent_runtime()
    reset_counsel_stores()


def _run(provider: _ScriptedProvider, *, key: str) -> tuple[dict[str, Any], PlanOutcome]:
    """POST → GET 하고, 팩 레코드의 `plan_outcome` 을 함께 돌려준다."""
    from ai.api.routers import counsel as counsel_router  # noqa: PLC0415

    set_counsel_provider(provider)
    headers = {**_HEADERS, "Idempotency-Key": key}
    with TestClient(create_app()) as client:
        posted: httpx.Response = client.post(
            "/v1/counsel/drafts", json=_request_body(), headers=headers
        )
        assert posted.status_code == 202, posted.text
        fetched: httpx.Response = client.get(
            f"/v1/counsel/drafts/{posted.json()['data']['job_id']}", headers=headers
        )
    assert fetched.status_code == 200, fetched.text

    #: 🔴 `plan_outcome` 은 와이어에 안 실린다 — **팩 레코드**가 정본이다(99 #101).
    rows = getattr(counsel_router._pack_store, "_rows", None)
    assert isinstance(rows, dict) and rows, "팩 결과가 없다 — 이 검사가 눈이 멀었다"
    record = next(iter(rows.values()))
    result: dict[str, Any] = fetched.json()["data"]["result"]
    return result, record.plan_outcome


# ── 1 · 빈 plan 응답은 ok 다 (본체) ───────────────────────────────


def test_an_empty_plan_response_is_recorded_as_ok() -> None:
    """🔴 **이 PR 의 본체.** 모델이 비워 둔 것은 장애가 아니다.

    종전에는 `plan_outcome=llm_failed` 였다 — 정상 동작이 장애로 계상됐다.
    """
    provider = _ScriptedProvider(ParseFailed("plan 빈 응답"))
    result, outcome = _run(provider, key="iq_plan_empty_ok")

    assert outcome is PlanOutcome.OK, (
        f"빈 plan 응답이 {outcome} 로 계상됐다 — 정상 동작이 장애로 세어진다(99 ㉲)"
    )
    assert result["draft_status"] == "generated", result


# ── 2 · 🔴 전송 장애 plan 은 여전히 llm_failed 다 ─────────────────


def test_a_transport_failure_in_plan_is_still_llm_failed() -> None:
    """🔴 **이 PR 이 안 깨야 하는 것.**

    ⚠ `except ParseFailed` 를 `except LlmError` 로 넓히면 **전송 장애를 「적법하게
    비웠다」로 삼킨다** — 그러면 plan 장애가 영영 안 보인다. `LlmTimeout` 은
    `ParseFailed` 의 **형제**라(둘 다 `LlmError` 하위) 잡는 형이 넓어지면 여기가 먼저 운다.
    """
    provider = _ScriptedProvider(LlmTimeout("plan 이 모델에 못 닿았다"))
    result, outcome = _run(provider, key="iq_plan_transport")

    assert outcome is PlanOutcome.LLM_FAILED, (
        f"전송 장애가 {outcome} 로 계상됐다 — 장애를 정상으로 삼켰다"
    )
    assert result["draft_status"] == "generated", result


# ── 3 · 형식 위반은 여전히 unparsed 다 ────────────────────────────


def test_a_malformed_plan_response_is_still_unparsed() -> None:
    """㉲가 가른 셋이 **셋으로 남는다** — `ok` · `llm_failed` · `unparsed`.

    ⚠ `PlanUnparsedError` 도 `LlmError` 하위형이라 `except` 순서가 뒤집히면 이 값이 죽는다.
    """
    provider = _ScriptedProvider(PlanUnparsedError("plan 응답 형식 위반"))
    result, outcome = _run(provider, key="iq_plan_unparsed")

    assert outcome is PlanOutcome.UNPARSED, outcome
    assert result["draft_status"] == "generated", result


# ── 4 · plan 이 죽어도 초안은 난다 ────────────────────────────────


@pytest.mark.parametrize(
    ("step", "label"),
    [
        (ParseFailed("빈 응답"), "empty"),
        (LlmTimeout("전송 장애"), "transport"),
        (PlanUnparsedError("형식 위반"), "unparsed"),
    ],
    ids=["empty", "transport", "unparsed"],
)
def test_a_failing_plan_never_kills_the_draft(step: Exception, label: str) -> None:
    """plan 은 **부가정보**다 — 어느 사유로 죽어도 초안은 무강조로 난다."""
    provider = _ScriptedProvider(step)
    result, _ = _run(provider, key=f"iq_plan_survives_{label}")

    assert result["draft_status"] == "generated", result
    assert result["text"], result


# ── 5 · 🔴 같은 입력, 다른 결말 (B-1 주석이 말하는 차이) ──────────


def test_writer_and_plan_converge_the_same_but_end_differently() -> None:
    """🔴 빈 응답의 **수렴 형태는 같아졌고 후속 처리는 다르다.**

    PR-05 가 writer 도 빈 값으로 수렴시켜 «비대칭이 의도다» 라는 종전 주석이 거짓이 됐다.
    **그래도 결말은 다르다:**
      · writer 의 빈 값 → 게이트 `empty` → `gate_feedback` → **재생성(≥2회 호출)**
        (본문이 결과물이라 비면 다시 만들어야 한다)
      · plan 의 빈 값 → **무강조 진행(재시도 0회 · 1회 호출)**
        ("고를 것이 없다"가 유효한 결과라 다시 물을 이유가 없다)

    ⚠ 주석이 말하는 차이를 **행동으로** 세운다 — 안 그러면 다음 사람이 둘을 같게 만든다.
    """
    #: ⓐ plan 만 비운다 — write 는 정상. **재시도 0회**여야 한다.
    plan_empty = _ScriptedProvider(ParseFailed("plan 빈 응답"))
    plan_result, plan_outcome = _run(plan_empty, key="iq_plan_only_empty")

    assert plan_outcome is PlanOutcome.OK, plan_outcome
    assert plan_empty.llm.plan_calls == 1, (
        f"plan 이 {plan_empty.llm.plan_calls}회 불렸다 — 빈 plan 은 다시 묻지 않는다"
    )
    assert plan_result["draft_status"] == "generated", plan_result

    #: ⓑ write 를 비운다 — **재생성이 돈다**(99 #87). plan 은 정상(빈 텍스트 = 강조점 0건).
    write_empty = _ScriptedProvider(
        "", write_script=[ParseFailed("초안 빈 응답"), _grounded_draft()]
    )
    write_result, _ = _run(write_empty, key="iq_write_only_empty")

    assert write_result["draft_status"] == "generated", write_result
    assert write_empty.llm.write_calls == 2, (
        f"write 가 {write_empty.llm.write_calls}회 불렸다 — 빈 초안은 재생성으로 살린다"
    )
    #: 🔴 **같은 사건, 다른 결말** — 이 두 숫자의 차이가 B-1 주석의 내용이다.
    assert plan_empty.llm.plan_calls < write_empty.llm.write_calls
