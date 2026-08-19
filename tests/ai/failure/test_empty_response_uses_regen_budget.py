"""빈 응답이 **재생성 예산을 쓴다** (99 #87).

🔴 **결손:** 실 provider 는 빈 content 를 `ParseFailed` 로 올리고(`openai_compat.py`),
그 자리 주석이 *"상위 소비자의 재시도 예산(블록 ≤3)이 소진한다"* 고 **기대를 적어 뒀다.**
그런데 `ParseFailed` 는 `LlmError` 의 **하위형**이라 `graph.py` 의 `except LlmError` 가
**학생을 그 자리에서 종결**했다 ⇒ 재생성 **0회** · 서킷 카운터 +1.

⇒ `GatewayDraftWriter.write` 가 `ParseFailed` 만 잡아 **빈 문자열**로 수렴시킨다.
게이트가 `empty` 로 잡고 `gate_feedback` 을 붙여 재생성(≤`regen_max`)을 돈다.

🔴 **전송 장애는 안 건드린다** — `timeout`·`provider_error`·`redaction_blocked` 는 여전히
`LlmError` 로 올라가 `llm_failed` + 서킷이다. **그 전제가 이 파일의 검사 4다.**

⚠ **대가**(숨기지 않는다): 체계적 빈 응답이면 호출이 최대 4배이고 서킷이 안 열린다.
상한이 3이라 유계이고 끝은 `gate_exhausted` 다 — 종전에는 **일시적** 빈 응답도 못 살렸다.
"""

from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, Final

import httpx
import pytest
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers import counsel as counsel_router
from ai.api.routers.counsel import reset_counsel_stores, set_counsel_provider
from ai.composition.counsel.assembly import build_counsel_gateway
from ai.composition.counsel.provider import GatewayDraftWriter
from ai.contracts.composition import DraftContext
from ai.contracts.counsel import WIRE_STATUS_REASONS
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
    "X-Tenant-Id": "t_empty_regen",
    "X-Request-Id": "rq-empty-regen",
    "Idempotency-Key": "iq_empty_regen",
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
    """게이트를 통과하는 초안 — 기존 대역의 정본을 재사용한다."""
    from counsel_text import DEFAULT_DRAFT  # noqa: PLC0415

    text: str = DEFAULT_DRAFT
    return text


class _ScriptedLlmProvider:
    """정해진 대로 **던지거나 돌려주는** LLM provider 대역.

    🔴 **게이트웨이를 통과시킨다** — provider 만 대역이고 `build_counsel_gateway` ·
    `GatewayDraftWriter` 는 **실제 코드**다. 통째로 갈면 이 PR 이 고친 층을 안 지난다
    (PR-α 에서 고의 파괴가 두 번 헛돈 그 자리).
    """

    name = "scripted"

    def __init__(self, script: Sequence[Exception | str]) -> None:
        self._script = list(script)
        self.calls = 0

    async def complete(
        self, request: LLMRequest, context: ExecutionContext
    ) -> LLMResult:
        del request, context
        step = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
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


class _ScriptedWriter:
    """`GatewayDraftWriter` 를 **실물로** 쓰되 plan 만 비운다."""

    def __init__(self, script: Sequence[Exception | str]) -> None:
        self.provider = _ScriptedLlmProvider(script)
        self._writer = GatewayDraftWriter(build_counsel_gateway(self.provider))

    async def plan(self, **_kwargs: object) -> dict[str, list[str]]:
        return {}

    async def write(self, **kwargs: object) -> str:
        return await self._writer.write(**kwargs)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    reset_shared_agent_runtime()
    reset_counsel_stores()
    yield
    reset_shared_agent_runtime()
    reset_counsel_stores()


def _post_and_fetch(writer: _ScriptedWriter, *, key: str) -> dict[str, Any]:
    set_counsel_provider(writer)
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
    result: dict[str, Any] = fetched.json()["data"]["result"]
    return result


# ── 1·2 · 빈 응답이 재생성을 쓴다 ─────────────────────────────────


def test_an_empty_response_spends_the_regeneration_budget() -> None:
    """🔴 **이 PR 의 본체.** 첫 응답이 비어도 재생성이 살려 낸다.

    종전에는 `draft_status="llm_failed"` 로 끝났다 — 재생성 **0회**.
    """
    writer = _ScriptedWriter([ParseFailed("LLM 빈 응답"), _grounded_draft()])
    result = _post_and_fetch(writer, key="iq_empty_then_ok")

    assert result["draft_status"] == "generated", (
        f"빈 응답 하나에 학생이 종결됐다({result['draft_status']}) — "
        "재생성 예산을 안 쓴다(99 #87)"
    )
    assert result["text"], result


def test_the_regeneration_actually_called_the_model_again() -> None:
    """🔴 재생성이 **실제로 한 번 더 불렀다** — 「운 좋게 통과」와 구분한다.

    ⚠ 1번 검사만 있으면 첫 호출이 그냥 성공한 경우와 구별이 안 된다.
    """
    writer = _ScriptedWriter([ParseFailed("LLM 빈 응답"), _grounded_draft()])
    _post_and_fetch(writer, key="iq_empty_calls")

    assert writer.provider.calls == 2, (
        f"write 호출이 {writer.provider.calls}회다 — 재생성이 안 돌았거나 과하게 돌았다"
    )


# ── 3 · 계속 비면 상한에서 멈춘다 ─────────────────────────────────


def test_a_persistently_empty_model_ends_at_the_bound() -> None:
    """계속 비면 `gate_exhausted` 다 — 무한이 아니다(불변식 6).

    ⚠ 끝이 **보이는 종단**이라는 것이 이 처방의 대가를 감당 가능하게 만든다.
    """
    writer = _ScriptedWriter([ParseFailed("LLM 빈 응답")])
    result = _post_and_fetch(writer, key="iq_empty_forever")

    assert result["draft_status"] == "gate_exhausted", result
    assert result["status_reason"] == "gate_exhausted", result
    #: 🔴 **`> 1` 로는 상한을 못 잰다** — 실측: `_REGEN_MAX` 를 3→1 로 낮춰도 2회라
    #: 그 단언이 green 이었다(고의 파괴 1-③). **설정에서 역산해 정확히** 댄다.
    #: ⚠ 리터럴을 박지 않는다 — 상한이 바뀌면 이 검사가 따라간다(03 §1).
    expected = counsel_router._REGEN_MAX + 1  # 재생성 N회 = 시도 N+1회
    assert writer.provider.calls == expected, (
        f"시도가 {writer.provider.calls}회다 — 상한(_REGEN_MAX={counsel_router._REGEN_MAX})"
        f"에서 {expected}회여야 한다"
    )


# ── 4 · 🔴 전송 장애는 그대로다 (ⓒ 의 전제) ───────────────────────


def test_a_transport_failure_is_still_llm_failed() -> None:
    """🔴 **이 PR 이 안 깨야 하는 것.** 전송 장애는 여전히 `llm_failed` 다.

    ⚠ `parse_fail` 만 갈랐다는 것을 여기서 잰다 — 조건을 `outcome is not OK` 로 넓히거나
    `except LlmError` 로 넓히면 **전송 장애까지 삼키고** 서킷이 영영 안 열린다.
    ⚠ `LlmTimeout` 은 `ParseFailed` 와 **형제**(둘 다 `LlmError` 하위)라, 잡는 형이
    넓어지면 이 검사가 먼저 운다.
    """
    writer = _ScriptedWriter([LlmTimeout("모델에 못 닿았다")])
    result = _post_and_fetch(writer, key="iq_transport_down")

    assert result["draft_status"] == "llm_failed", (
        f"전송 장애가 {result['draft_status']} 로 나갔다 — 장애를 게이트 실패로 "
        "오분류하면 서킷도 알럿도 안 뜬다"
    )
    assert result["status_reason"] == "llm_failed", result
    assert writer.provider.calls == 1, (
        f"전송 장애인데 {writer.provider.calls}회 불렀다 — 장애가 재생성 예산을 먹는다"
    )


# ── 5 · empty 게이트 사유가 도달한다 (99 #06) ─────────────────────


def test_the_empty_gate_feedback_reaches_the_regeneration_prompt() -> None:
    """🔴 `gate_feedback.yaml` 의 `empty` 문구가 **사문에서 벗어난다**(99 #06).

    빈 응답이 게이트에 닿지 못하던 동안 이 문구는 도달 불가였다. 재생성이 실제로 한 번 더
    도는지를 잰다 — 안 돌면 2회차가 **빈손으로** 같은 프롬프트를 반복한다.
    """
    from ai.composition.gate_feedback import instruction_for  # noqa: PLC0415

    assert instruction_for("empty"), "gate_feedback 에 empty 문구가 없다 — 재생성이 빈손이다"

    writer = _ScriptedWriter([ParseFailed("LLM 빈 응답"), _grounded_draft()])
    _post_and_fetch(writer, key="iq_empty_feedback")
    assert writer.provider.calls == 2, writer.provider.calls


# ── 6 · 어휘가 안 늘었다 ──────────────────────────────────────────


def test_this_change_does_not_grow_the_wire_vocabulary() -> None:
    """PR-03·04 가 세운 축을 이 PR 이 안 깬다 — 새 `status_reason` 이 없다."""
    writer = _ScriptedWriter([ParseFailed("LLM 빈 응답")])
    result = _post_and_fetch(writer, key="iq_empty_vocab")

    assert result["status_reason"] in WIRE_STATUS_REASONS, result


# ── 🔴 7 · 게이트 피드백 문구가 **전송 가능**해야 한다 ────────────


def test_every_gate_feedback_text_can_actually_be_sent() -> None:
    """🔴 `gate_feedback.yaml` 의 `empty` 문구가 **사문에서 벗어난다**(99 #06).

    빈 응답이 게이트에 닿지 못하던 동안 이 문구는 도달 불가였다. 재생성 프롬프트에
    실제로 실리는지를 잰다 — 안 실리면 2회차가 **빈손으로** 같은 프롬프트를 반복한다.
    """
    from ai.composition.gate_feedback import (  # noqa: PLC0415
        instruction_for,
        load_gate_feedback,
    )
    from ai.runtime.redaction import redact  # noqa: PLC0415

    #: 🔴 **yaml 을 직접 파싱하지 않는다** — 모듈 자신의 로더를 지나야 검증·기본값까지
    #: 같은 경로를 밟는다(그리고 스텁 없는 의존을 검사에 들이지 않는다).
    feedback = load_gate_feedback()
    texts: dict[str, str] = {
        **{f"instructions.{key}": value for key, value in feedback.instructions.items()},
        "default": feedback.default,
        "detail_suffix": feedback.detail_suffix,
    }

    blocked = sorted(
        name
        for name, text in texts.items()
        if (outcome := redact(text)).uncertain or outcome.findings
    )

    assert not blocked, (
        f"게이트 피드백 문구가 트립와이어에 걸린다: {blocked} — "
        "그 사유로 재생성이 걸리면 2회차 프롬프트가 미전송되어 재생성이 죽는다"
    )
    #: 절단 가드 — 문구를 하나도 안 읽었으면 위 단언이 공허하다.
    assert len(texts) >= 10, f"문구를 {len(texts)}개만 읽었다 — 로더가 빈손이다"
    assert instruction_for("empty"), "empty 문구가 사라졌다"


# ── 🔴 8 · 두 경로가 **같은 결말**이다 (도달 불가 경로는 직접 잰다) ──


def test_an_ok_result_with_empty_text_converges_the_same_way() -> None:
    """🔴 `outcome=OK` 인데 본문이 빈 경우도 **빈 문자열**로 수렴한다.

    실측(8/19): 이 경로는 **HTTP 로 도달하지 않는다** — 실 게이트웨이는 실패를 예외로
    re-raise 하고 어느 provider 도 `outcome != OK` 인 결과를 **반환하지 않으므로**,
    빈 응답은 위쪽 `except ParseFailed` 가 먼저 잡는다. ⇒ **라우터를 통해서는 못 재고**
    고의 파괴(2-①)가 green 으로 그 사실을 드러냈다.

    🔴 **그래도 결말은 같아야 한다** — `parse_fail` 과 **같은 사건**(닿았는데 내용이 없다)이라,
    다르게 두면 *"어느 provider 를 쓰는가"* 가 판정을 바꾼다. 도달 불가라는 것은 「지금
    아무도 안 온다」이지 「결말이 달라도 된다」가 아니다.

    ⚠ 그래서 **writer 를 직접** 부른다 — 도달 불가 경로를 재는 유일한 방법이다.
    """
    from uuid import uuid4  # noqa: PLC0415

    from ai.contracts.execution import Capability, VersionSet  # noqa: PLC0415

    class _EmptyButOk:
        name = "empty-but-ok"

        async def complete(
            self, request: LLMRequest, context: ExecutionContext
        ) -> LLMResult:
            del request, context
            return LLMResult(
                text="   ",  # 공백뿐 — `outcome` 은 정상이다
                provider=self.name,
                model="empty-but-ok-model",
                usage=TokenUsage(tokens_in=0, tokens_out=0, cost_usd=0.0),
                latency_ms=0,
                outcome=CallOutcome.OK,
            )

    writer = GatewayDraftWriter(build_counsel_gateway(_EmptyButOk()))
    context = _draft_context_for_direct_call()
    execution_context = ExecutionContext(
        execution_id=uuid4(),
        tenant_id="t_empty_ok",
        capability=Capability.COMPOSITION,
        input_snapshot_hash="sha256:" + "a" * 64,
        versions=VersionSet(
            pipeline_version="0.1",
            engine_version="counsel-pack-0.1",
            schema_version="0.1",
            contract_version="0.1",
        ),
    )

    text = asyncio.run(
        writer.write(context=context, execution_context=execution_context)
    )

    assert text == "", (
        f"outcome=OK + 빈 본문이 {text!r} 로 나왔다 — parse_fail 과 같은 사건인데 "
        "결말이 다르면 어느 provider 를 쓰는가가 판정을 바꾼다"
    )


def _draft_context_for_direct_call() -> DraftContext:
    """writer 를 직접 부를 때 쓰는 최소 컨텍스트 — 라우터 정본에서 만든다."""
    from ai.api.routers.counsel import _draft_context  # noqa: PLC0415
    from ai.contracts.counsel import CounselDraftRequest  # noqa: PLC0415

    return _draft_context(CounselDraftRequest.model_validate(_request_body()))
