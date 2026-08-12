"""게이트 재생성 — S23·S24 (지시서 79 §5 · 79-R §5·§6).

🔴 **실제 GPT가 실패하기를 기다리지 않는다.** 실패를 유도하려고 악성 evidence를 넣지도
않는다 — `set_brief_provider()` seam에 **대본 provider**를 꽂아 결정론으로 잰다.

⚠ **provider seam부터 HTTP까지** 지나는 축이라 integration이다(순수 판정은 unit 파일).
⚠ **실제 OpenAI 검사는 이 파일에 넣지 않는다** — 실 LLM 호출 0건이다.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Final

import pytest
from detect_briefing_scenarios import builder
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers import detect as detect_router
from ai.composition.briefing import MAX_REGEN, PROMPT_VERSION
from ai.contracts.execution import ExecutionContext
from ai.contracts.llm import CallOutcome, LLMRequest, LLMResult, TokenUsage

pytestmark = pytest.mark.integration

#: 🔴 근거에 **없는** 숫자 — 게이트가 `ungrounded_number`로 거부해야 한다.
#: ⚠ 거부 이유가 **하나로 식별 가능**해야 한다(여러 위반을 섞으면 어느 것이 걸렸는지 모른다).
_UNGROUNDED: Final = "정답률이 99.9%까지 올라갔어요."

#: 근거 값만 쓰는 정상 문장(한국어 한 문장 · 해요체 · 금칙어 없음).
_CLEAN: Final = "이번 주 학습 흐름을 함께 살펴보면 좋겠어요."


class _ScriptedNarrator:
    """대본대로 답하는 narrator provider — **프롬프트를 전부 기록한다**.

    🔴 호출 **횟수만** 세지 않는다 — 두 번째 프롬프트에 **게이트 피드백이 실제로 들어갔는지**
    봐야 «재생성이 사유를 전달한다»가 증명된다(79-R §5).
    """

    def __init__(self, script: list[str]) -> None:
        self._script = script
        self.prompts: list[str] = []

    @property
    def name(self) -> str:
        return "scripted-narrator"

    async def complete(
        self, request: LLMRequest, context: ExecutionContext
    ) -> LLMResult:
        del context
        self.prompts.append(request.prompt)
        assert request.prompt_version == PROMPT_VERSION, request.prompt_version
        index = min(len(self.prompts) - 1, len(self._script) - 1)
        return LLMResult(
            outcome=CallOutcome.OK,
            text=self._script[index],
            provider=self.name,
            model="scripted",
            usage=TokenUsage(tokens_in=1, tokens_out=1, cost_usd=0.0),
            latency_ms=1,
        )


@pytest.fixture
def narrator() -> Iterator[list[str]]:
    """대본 슬롯 — 🔴 **끝나면 전역 provider를 원복한다**(다음 검사로 새면 안 된다)."""
    script: list[str] = []
    yield script
    detect_router.reset_brief_provider()
    detect_router.reset_detection_store()
    detect_router.reset_idempotency_store()


def _one_signal_request() -> dict[str, Any]:
    """R1 한 건만 발화하는 최소 요청 — 브리핑 대상이 정확히 하나다."""
    scenario = builder("gate").student("st_a")
    scenario.steady_history("st_a", n=20, correct=18, skip_recent=2)
    for back in (0, 1):
        scenario.solves("st_a", back=back, n=20, correct=14)
    body: dict[str, Any] = scenario.build().model_dump(mode="json")
    return body


def _post(client: TestClient, body: dict[str, Any], *, key: str) -> Any:  # noqa: ANN401
    return client.post(
        "/v1/detect",
        json=body,
        headers={
            "X-Tenant-Id": "t_gate_matrix",
            "X-Request-Id": f"rq-{key}",
            "Idempotency-Key": f"idem-{key}",
        },
    )


def _run(script: list[str], *, key: str) -> tuple[Any, _ScriptedNarrator]:  # noqa: ANN401
    provider = _ScriptedNarrator(script)
    detect_router.set_brief_provider(provider)
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        response = _post(client, _one_signal_request(), key=key)
    return response, provider


# ───────────────────────── S23 ─────────────────────────


def test_s23_a_rejected_first_answer_is_regenerated_with_feedback(
    narrator: list[str],
) -> None:
    """S23 — 첫 응답 거부 → **사유를 실어** 재생성 → 두 번째 성공.

    🔴 최종 결과는 **두 번째 응답**이고 첫 실패 문장은 섞이지 않는다.
    """
    narrator.extend([_UNGROUNDED, _CLEAN])
    response, provider = _run(narrator, key="s23")

    assert response.status_code == 200, response.text
    signals = response.json()["data"]["signals"]
    assert len(signals) == 1, signals
    brief = signals[0]["brief"]

    #: 🔴 호출 **정확히 2회** — 거부 뒤 한 번만 더 부른다.
    assert len(provider.prompts) == 2, len(provider.prompts)
    #: 🔴 두 번째 프롬프트에 **첫 거부 사유가 피드백으로** 들어갔다.
    assert provider.prompts[0] != provider.prompts[1], "재생성 프롬프트가 바이트 동일하다"
    assert len(provider.prompts[1]) > len(provider.prompts[0]), (
        "두 번째 프롬프트가 안 길어졌다 — 수정 지시가 안 실렸다"
    )

    assert brief["gate_passed"] is True, brief
    assert brief["fallback_used"] is False, brief
    assert brief["text"] == _CLEAN, brief["text"]
    #: 🔴 거부된 문장이 최종 결과에 남지 않는다.
    assert "99.9" not in brief["text"]


def test_s23_the_feedback_names_the_gate_reason(narrator: list[str]) -> None:
    """🔴 **절단 가드** — 「길어졌다」만으로는 무엇이 실렸는지 모른다.

    거부 사유(`ungrounded_number`)에 대응하는 **수정 지시 문구**가 두 번째 프롬프트에
    있어야 한다 — 사유 없이 같은 프롬프트를 반복하면 비용만 늘고 개선은 0이다.
    """
    #: ⚠ 정본은 `gate_feedback`이다 — `briefing`의 재수출은 `__all__`에 없다.
    from ai.composition.gate_feedback import instruction_for  # noqa: PLC0415

    narrator.extend([_UNGROUNDED, _CLEAN])
    _response, provider = _run(narrator, key="s23fb")

    expected = instruction_for("ungrounded_number:99.9")
    assert expected, "이 사유에 대응하는 수정 지시가 없다 — 검사의 전제가 깨졌다"
    #: 지시 문구의 앞부분이 프롬프트에 실렸는지 본다(문면 전체 복제 금지).
    assert expected[:12] in provider.prompts[1], provider.prompts[1][-200:]
    assert expected[:12] not in provider.prompts[0], "1회차에 이미 지시가 있다"


# ───────────────────────── S24 ─────────────────────────


def test_s24_exhausting_the_regen_budget_falls_back_honestly(
    narrator: list[str],
) -> None:
    """S24 — 매 시도 같은 위반 → **상한까지만** 부르고 템플릿으로 정직하게 폴백.

    🔴 거부된 GPT 문장을 성공 결과로 저장하지 않는다. 🔴 HTTP는 5xx가 아니라 **200**이다
    (게이트 거부는 에러가 아니다 · 불변식 4).
    """
    narrator.append(_UNGROUNDED)  # 대본이 하나면 매 시도 같은 응답이다
    response, provider = _run(narrator, key="s24")

    assert response.status_code == 200, response.text
    signals = response.json()["data"]["signals"]
    assert len(signals) == 1
    brief = signals[0]["brief"]

    #: 🔴 **상한을 넘지 않는다** — 무한 루프가 아니다.
    assert len(provider.prompts) == MAX_REGEN, len(provider.prompts)
    assert brief["gate_passed"] is False, brief
    assert brief["fallback_used"] is True, brief
    #: 🔴 거부 문장이 최종 결과가 되지 않았다.
    assert "99.9" not in brief["text"], brief["text"]
    assert brief["text"], "폴백 문장이 비었다"


def test_s24_the_deterministic_verdict_survives_the_fallback(
    narrator: list[str],
) -> None:
    """S24 — 브리핑이 폴백해도 **판정·근거는 그대로**다(GPT가 바꿀 수 있는 건 문면뿐)."""
    narrator.append(_UNGROUNDED)
    response, _provider = _run(narrator, key="s24det")
    signal = response.json()["data"]["signals"][0]

    assert signal["rule_id"] == "R1"
    assert signal["signal_type"] == "acc_drop"
    assert signal["lifecycle"] == "new"
    assert signal["advisory"] is False
    assert signal["evidence"], "폴백이 근거를 지웠다"
    assert all(item["record_id"] for item in signal["evidence"])


def test_the_provider_seam_is_restored_between_tests() -> None:
    """🔴 **절단 가드** — 대본 provider가 남으면 다음 검사가 남의 대본을 물려받는다.

    ⚠ 이 검사는 위 넷 **뒤에** 돌아 기본 provider가 복구됐는지 본다(fixture의 원복).

    🔴 **`"99.9"가 없다`로는 못 잡는다**(2026-08-12 실측 — 첫 판이 그래서 green이었다).
    대본이 남아 있으면 응답이 **폴백**이 되고 폴백 문장에도 `99.9`는 없다.
    ⇒ **게이트를 통과했는지**를 본다 — 기본 Fake는 통과하고, 남은 대본은 폴백이다.
    """
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        response = _post(client, _one_signal_request(), key="restored")
    assert response.status_code == 200, response.text
    brief = response.json()["data"]["signals"][0]["brief"]
    assert brief["gate_passed"] is True, f"대본 provider가 남았다: {brief}"
    assert brief["fallback_used"] is False, brief
    assert "99.9" not in brief["text"], brief["text"]
