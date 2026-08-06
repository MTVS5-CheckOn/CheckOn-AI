"""refine 장애가 **HTTP로 정직하게** 나가는가 — §4 A 판정이 요구한 통합 고정.

§4 트리 A 판정(7/22)이 *"통합 테스트로 409·503·504를 고정"* 하라고 했는데, 지금까지
503·504를 **낼 수 있는 코드가 없어서** 그 요구가 서 있지 못했다(runtime 예외의 raise 0곳).
이 PR이 도달 경로를 만들면서 처음 성립한다.

| 주입 | HTTP | code |
| --- | --- | --- |
| `LlmTimeout` | **504** | `TIMEOUT` |
| `LlmUnavailable` | **503** | `LLM_UPSTREAM_DOWN` |
| plain `LlmError`(4xx) | **500** | `INTERNAL` |
| 게이트 소진 | **200** | (판단이다 — 불변식 4) |
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers import counsel as counsel_router
from ai.api.routers.counsel import reset_counsel_stores, set_counsel_provider
from ai.composition.counsel.provider import FakeCounselProvider
from ai.contracts.composition import DraftContext
from ai.contracts.execution import ExecutionContext
from ai.contracts.llm import LlmError, LlmTimeout, LlmUnavailable
from ai.db.repositories.run_store import InMemoryRunStore
from ai.db.store_factory import reset_shared_agent_runtime

_HEADERS = {
    "X-Tenant-Id": "t1",
    "X-Request-Id": "rq-refine-1",
    "Idempotency-Key": "t1:counsel:refine-http",
}

#: 이 요청의 근거로 통과하는 본문 — `_REQUEST`의 facts에서 나온 수치만 쓴다.
_GROUNDED = "지문 42개를 함께 살펴봤습니다."


class _FailingWriter:
    """초안 1회는 성공시키고, **다듬기 턴부터** 정해진 예외를 던진다."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls = 0

    async def plan(self, **_kwargs: object) -> dict[str, list[str]]:
        return {}

    async def write(
        self,
        *,
        context: DraftContext,
        execution_context: ExecutionContext,
        emphasis: Sequence[str] = (),
        gate_feedback: str = "",
        refine_instruction: str = "",
        previous_text: str = "",
    ) -> str:
        del context, execution_context, emphasis, gate_feedback, previous_text
        self.calls += 1
        if refine_instruction:  # 다듬기 턴에만 터진다
            raise self._exc
        return _GROUNDED


def _request_body() -> dict[str, Any]:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "rt", "tests/ai/integration/test_counsel_router.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    body: dict[str, Any] = module._REQUEST
    return body


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    reset_shared_agent_runtime()  # A·B 공용 잡 원장(99 ㊒)
    reset_counsel_stores()
    yield
    reset_shared_agent_runtime()  # A·B 공용 잡 원장(99 ㊒)
    reset_counsel_stores()


def _client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def _generate_then_refine(exc: Exception) -> httpx.Response:
    set_counsel_provider(_FailingWriter(exc))
    with _client() as client:
        job_id = client.post(
            "/v1/counsel/drafts", json=_request_body(), headers=_HEADERS
        ).json()["data"]["job_id"]
        response: httpx.Response = client.post(
            f"/v1/counsel/drafts/{job_id}/refine",
            json={"instruction": "조금 더 부드럽게", "turn_no": 1},
            headers=_HEADERS,
        )
        return response


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (LlmTimeout("t"), 504, "TIMEOUT"),
        (LlmUnavailable("u"), 503, "LLM_UPSTREAM_DOWN"),
        (LlmError("4xx — 컨텍스트 한도"), 500, "INTERNAL"),
    ],
    ids=["timeout→504", "unavailable→503", "plain→500"],
)
def test_llm_failure_reaches_http_honestly(
    exc: Exception, status: int, code: str
) -> None:
    """🔴 종전에는 셋 다 **200 + "안전 기준에 걸려…"** 였다."""
    response = _generate_then_refine(exc)
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code


def test_plain_llm_error_is_not_reported_as_a_vendor_outage() -> None:
    """🔴 4xx를 503으로 뭉개지 않는다.

    503이면 BE 폴백이 "잠시 후 다시"인데 **잠시 후에도 똑같이 실패한다** — 또 하나의
    거짓말이 된다. `openai_compat`이 429만 승격하고 나머지 4xx를 plain으로 두는 이유다.
    """
    assert _generate_then_refine(LlmError("4xx")).status_code != 503


def test_the_failure_detail_is_not_exposed() -> None:
    """5xx의 detail은 응답에서 빠진다(04 §2.3 · 5xx 파생 미노출)."""
    body = _generate_then_refine(LlmTimeout("내부 상세가 담길 수 있다")).json()
    assert body["error"]["detail"] is None


def test_the_failed_turn_still_lands_in_the_ledger() -> None:
    """🔴 장애 턴이 원장에서 사라지면 안 된다(불변식 8) — 하필 **가장 알고 싶은 턴**이다.

    ⚠ **이 테스트가 한 종류만 봐서 결함을 통과시켰다(8/7).** `LlmTimeout`만 확인했는데
    `RedactionUncertain`(= `DomainException`)은 종전 `except LlmError` 절이 못 잡아
    원장에서 사라졌고, 여기는 초록이었다. **종류 전수는
    `tests/ai/failure/test_ledger_survives_every_failure.py`가 파라미터화로 본다** —
    "종류를 늘려도 안 갈린다"는 성질이고, 성질은 한 종류로 증명되지 않는다.
    여기는 HTTP 표면에서의 대표 1건으로 남긴다.
    """
    store = InMemoryRunStore()
    counsel_router.set_counsel_run_store(store)
    before = len(store.runs)

    assert _generate_then_refine(LlmTimeout("t")).status_code == 504
    assert len(store.runs) > before, "장애 턴의 AI_RUN이 남지 않았다"


def test_gate_exhaustion_is_still_two_hundred() -> None:
    """🔴 회귀 방지 — 판단까지 5xx로 올리면 불변식 4 위반이다(리뷰 반려 사유)."""
    set_counsel_provider(
        FakeCounselProvider(drafts=[_GROUNDED, "정답률이 88%까지 올랐습니다."])
    )
    with _client() as client:
        job_id = client.post(
            "/v1/counsel/drafts", json=_request_body(), headers=_HEADERS
        ).json()["data"]["job_id"]
        response = client.post(
            f"/v1/counsel/drafts/{job_id}/refine",
            json={"instruction": "조금 더 부드럽게", "turn_no": 1},
            headers=_HEADERS,
        )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["applied"] is False
    assert response.json()["data"]["blocked_reason"] is not None


def test_instruction_pii_is_still_two_hundred() -> None:
    """강사가 고칠 수 있는 것은 200으로 남는다 — 주체가 다르다."""
    set_counsel_provider(FakeCounselProvider(drafts=[_GROUNDED]))
    with _client() as client:
        job_id = client.post(
            "/v1/counsel/drafts", json=_request_body(), headers=_HEADERS
        ).json()["data"]["job_id"]
        response = client.post(
            f"/v1/counsel/drafts/{job_id}/refine",
            json={"instruction": "서연이가 힘들대요 라고 써줘", "turn_no": 1},
            headers=_HEADERS,
        )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["blocked_reason"] == "pii_exposure"
