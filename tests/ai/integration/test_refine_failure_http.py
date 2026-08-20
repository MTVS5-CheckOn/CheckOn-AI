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
from typing import Any, Final

import httpx
import pytest
from counsel_text import draft
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers import counsel as counsel_router
from ai.api.routers.counsel import reset_counsel_stores, set_counsel_provider
from ai.composition.counsel.prompt import assemble_prompt
from ai.composition.counsel.provider import FakeCounselProvider, RedactionBlockedError
from ai.contracts.composition import DraftContext
from ai.contracts.execution import ExecutionContext
from ai.contracts.llm import LlmError, LlmTimeout, LlmUnavailable
from ai.db.repositories.run_store import InMemoryRunStore
from ai.db.store_factory import reset_shared_agent_runtime
from ai.runtime.redaction import redact

_HEADERS = {
    "X-Tenant-Id": "t1",
    "X-Request-Id": "rq-refine-1",
    "Idempotency-Key": "t1:counsel:refine-http",
}

#: 이 요청의 근거로 통과하는 본문 — `_REQUEST`의 facts에서 나온 수치만 쓴다.
_GROUNDED = draft("지문 42개를 함께 살펴봤습니다.")


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
        FakeCounselProvider(drafts=[_GROUNDED, draft("정답률이 88%까지 올랐습니다.")])
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
            json={"instruction": "서연이가 민준이랑 힘들대요 라고 써줘", "turn_no": 1},
            headers=_HEADERS,
        )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["blocked_reason"] == "pii_exposure"


# ══ 🔴 세 번째 주체 — 직전 본문 (99 #77) ══
#
# 프롬프트에 들어가는 입력은 **셋**(지시문·컨텍스트·직전 본문)인데 종전 검사는 **둘만**
# 갈랐다. `previous_text`가 `redact()` 오탐에 걸리면 `writer.write` 안에서
# `RedactionBlockedError`가 나고 라우터가 그것을 **컨텍스트 쪽**으로 분류해 5xx로 올린다 —
# 그런데 직전 본문은 컨텍스트가 아니라 **LLM이 쓴 글**이고, 강사는 지시를 백 번 고쳐도
# 매번 같은 5xx라 **되돌릴 경로가 없다.**

#: 🔴 **실측 오탐 어절**(`provider.py` 대역 문면 주석 ⓐ′) — 「성씨 1자+이름 2자」 휴리스틱이
#: 평범한 활용형을 인명 후보로 잡는다. 2026-08-19 재현: `redact(...).uncertain is True`.
#: ⚠ **실명을 넣지 않는다** — 넣으면 *"진짜 실명을 막는가"* 라는 **다른 축**을 재게 된다.
#: 🔴 한 문장에 인명 후보 **둘** — 밀도 규칙상 그래야 `uncertain`이 선다
#: (`policies/masking_redaction.md` §2 [A 확정 7/23] · 후보 1개는 토큰만 바꾸고 전송은
#: 막지 않는다). 종전 문면은 후보 1개짜리라 구현이 스펙보다 넓게 막던 시절에만 오탐으로
#: 잡혔다 — 이 파일이 보려는 것(「오탐이어도 5xx가 아니다」)은 그대로 두고 조건만 맞췄다.
_FALSE_POSITIVE_BODY: Final = draft(
    "서연이가 민준이랑 왔습니다. 가정에서도 같은 방향으로 지켜봐 주시면 좋겠습니다."
)


class _RedactingWriter(FakeCounselProvider):
    """🔴 **실 provider의 마스킹 단계를 대역에 되살린다.**

    ⚠ 기본 `FakeCounselProvider`는 `redact()`를 **아예 안 부른다** — LLM을 안 부르니
    전송 전 검사도 없다. 그대로 쓰면 *"직전 본문 오탐이 5xx를 만든다"* 를 재는 검사가
    **처방을 지워도 green**이 된다(2026-08-19 실측: 고의 파괴 ③·④가 둘 다 초록이었다).

    ⇒ `OpenAiCounselProvider.write`와 **같은 순서**로 조립→마스킹→fail-closed를 한다.
    바꾼 것은 「LLM을 부르는 부분」뿐이고 **불변식 3의 경로는 실물과 같다.**
    """

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
        if redact(
            assemble_prompt(
                context, emphasis, gate_feedback, refine_instruction, previous_text
            )
        ).uncertain:
            raise RedactionBlockedError("상담 초안 프롬프트의 마스킹이 불확실하다")
        return await super().write(
            context=context,
            execution_context=execution_context,
            emphasis=emphasis,
            gate_feedback=gate_feedback,
            refine_instruction=refine_instruction,
            previous_text=previous_text,
        )


def test_the_false_positive_input_really_is_uncertain() -> None:
    """🔴 절단 가드 — 이 어절이 `uncertain`을 안 내면 아래 검사는 아무것도 안 본다.

    ⚠ **대역이 마스킹을 하는지도 같이 걸린다** — `_RedactingWriter`가 그 단계를 지우면
    고의 파괴 ③·④가 green이 되어 바로 드러난다(2026-08-19에 실제로 그랬다).
    """
    from ai.runtime.redaction import redact  # noqa: PLC0415

    assert redact(_FALSE_POSITIVE_BODY).uncertain, (
        "오탐 재현에 실패했다 — 휴리스틱이 바뀌었을 수 있다. 입력을 다시 골라라"
    )


def test_a_previous_text_false_positive_does_not_trap_the_teacher() -> None:
    """🔴 **직전 본문이 마스킹 불확실이어도 5xx가 아니다** (99 #77).

    누적을 끊고(`previous_text=""`) 그 턴을 돌린다 — 그 잡의 다듬기가 처음부터 다시
    쌓이지만 **영구 5xx보다 낫다.** 강사가 빠져나올 길이 생긴다.

    ⚠ 반영으로 끝나든 게이트 차단(200)으로 끝나든 **둘 다 성공**이다 — 이 검사가 잠그는
    것은 *"5xx가 아니다"* 이고, 그 뒤 판정은 게이트의 축이다.
    """
    set_counsel_provider(_RedactingWriter(drafts=[_FALSE_POSITIVE_BODY, _GROUNDED]))
    with _client() as client:
        job_id = client.post(
            "/v1/counsel/drafts", json=_request_body(), headers=_HEADERS
        ).json()["data"]["job_id"]
        response = client.post(
            f"/v1/counsel/drafts/{job_id}/refine",
            json={"instruction": "조금 더 부드럽게", "turn_no": 1},
            headers={**_HEADERS, "Idempotency-Key": "idem-prev-fp"},
        )

    assert response.status_code == 200, (
        f"직전 본문 오탐이 {response.status_code}로 나갔다 — 강사가 못 빠져나온다 "
        f"(99 #77): {response.text[:400]}"
    )
    data = response.json()["data"]
    assert set(data) <= {"applied", "text", "citations", "blocked_reason"}, data
    if not data["applied"]:
        assert data["blocked_reason"], "차단인데 사유가 없다"


def test_the_three_subjects_stay_split() -> None:
    """🔴 **회귀 — 의도된 비대칭을 무너뜨리지 않았다.**

    ```
    지시문      강사가 고칠 수 있다   → 200 + pii_exposure    ← 여기서 잠근다
    직전 본문   LLM이 쓴 글           → 200                   ← 위 검사가 잠근다
    컨텍스트    강사가 못 고친다      → 5xx                   ← 여기서 잠근다
    ```

    🔴 **직전 본문 축은 일부러 여기서 안 본다.** 넣으면 위 검사와 겹쳐서,
    *"직전 본문 처방을 지웠다"* 는 파괴에 **둘 다 red**가 되고 그러면 «비대칭이
    안 무너졌다»를 증명할 검사가 남지 않는다. **겹친 검사는 하나를 지워도 안 보인다.**
    ⚠ 통일하면 *"고칠 수 있다"* 와 *"못 고친다"* 가 같은 화면이 된다 — 그게 이 검사가
    막는 것이다.
    """
    #: ⓐ 지시문 기인 — 200 + pii_exposure
    set_counsel_provider(FakeCounselProvider(drafts=[_GROUNDED]))
    with _client() as client:
        job_id = client.post(
            "/v1/counsel/drafts", json=_request_body(), headers=_HEADERS
        ).json()["data"]["job_id"]
        by_instruction = client.post(
            f"/v1/counsel/drafts/{job_id}/refine",
            json={"instruction": "서연이가 민준이랑 힘들대요 라고 써줘", "turn_no": 1},
            headers={**_HEADERS, "Idempotency-Key": "idem-split-a"},
        )
    assert by_instruction.status_code == 200
    assert by_instruction.json()["data"]["blocked_reason"] == "pii_exposure"

    #: ⓒ 컨텍스트 기인 — **여전히 5xx.** 강사가 못 고치는 것은 못 고치는 대로 나간다.
    by_context = _generate_then_refine(
        RedactionBlockedError("컨텍스트 마스킹 불확실")
    )
    assert by_context.status_code >= 500, (
        f"컨텍스트 기인이 {by_context.status_code}가 됐다 — 의도된 비대칭이 무너졌다"
    )
