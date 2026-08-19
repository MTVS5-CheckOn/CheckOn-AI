"""counsel GET 이 호출자에게 **언제 다시 오라고** 말한다 (`Retry-After`).

counsel 은 **Kafka 완료 통지가 없다** — 실측(2026-08-19): AI 쪽 프로듀서·컨슈머·outbox
**0건**, `aiokafka` 는 의존성에도 없다. ⇒ 어댑터·BE 에게 **폴링이 유일한 길**인데 주기를
우리가 안 말해 주면 상대가 **자기 상수로 돈다.** 그러면 우리가 인라인 실행을 바꿔도
(이번 회차에 POST 가 최대 3회 드레인하도록 바뀌어 응답이 최악 225s 까지 늘었다 · 99 #21·#85)
그 주기는 안 따라온다.

⚠ 어댑터 쪽 코드 변경은 **0**이다 — `retryAfter == null` 이면 고정 간격으로 떨어지게
이미 짜여 있어서, 우리가 헤더를 붙이는 순간부터 저쪽이 그 값을 쓴다(지시서 №1 [읽음]).

🔴 **각 검사의 이름이 계약 문장이다.** problem 축의 같은 단언은
`tests/ai/contract/test_http_fixtures.py` 가 이미 들고 있다 — 여기는 counsel 축이다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any, Final

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ai.api.app import create_app
from ai.api.routers import counsel as counsel_router
from ai.api.routers.counsel import reset_counsel_stores
from ai.composition.counsel.settings import CounselSettings, get_counsel_settings
from ai.db.store_factory import reset_shared_agent_runtime

_TENANT: Final = "t_retry_after"
_HEADERS: Final = {
    "X-Tenant-Id": _TENANT,
    "X-Request-Id": "rq-retry-after",
    "Idempotency-Key": "iq_retry_after",
}


def _request_body() -> dict[str, Any]:
    """🔴 계약 §4-① 예시를 복제하지 않는다 — 기존 통합 검사의 정본을 재사용한다."""
    from test_counsel_router import _REQUEST  # noqa: PLC0415

    return dict(_REQUEST)


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    """🔴 `get_counsel_settings` 는 `@lru_cache` 다 — **검사마다 비운다.**

    안 비우면 앞 검사가 읽은 값이 굳어 `monkeypatch.setenv` 가 **아무 효과가 없다** —
    그러면 「값이 설정에서 온다」 검사가 **거짓 green** 이 된다.
    """
    get_counsel_settings.cache_clear()
    reset_shared_agent_runtime()
    reset_counsel_stores()
    yield
    get_counsel_settings.cache_clear()
    reset_shared_agent_runtime()
    reset_counsel_stores()


def _post(client: TestClient, *, key: str = _HEADERS["Idempotency-Key"]) -> dict[str, Any]:
    response: httpx.Response = client.post(
        "/v1/counsel/drafts",
        json=_request_body(),
        headers={**_HEADERS, "Idempotency-Key": key},
    )
    assert response.status_code == 202, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def _get(client: TestClient, job_id: str) -> httpx.Response:
    response: httpx.Response = client.get(
        f"/v1/counsel/drafts/{job_id}", headers=_HEADERS
    )
    return response


def _enqueue_ahead(count: int) -> None:
    """큐에 **앞선 잡**을 넣어 내 잡이 `queued` 로 나가게 한다(FIFO).

    ⚠ 리터럴을 박지 않는다 — 상한 `K` 만큼 앞에 두면 회전이 전부 그쪽에 쓰인다.
    """

    async def enqueue() -> None:
        from ai.api.routers.counsel import (  # noqa: PLC0415
            _build_supervisor,
            _clock,
            _draft_context,
        )
        from ai.composition.counsel.enqueue import CounselPackEnqueuer  # noqa: PLC0415
        from ai.contracts.counsel import CounselDraftRequest  # noqa: PLC0415

        request = CounselDraftRequest.model_validate(_request_body())
        for index in range(count):
            await CounselPackEnqueuer(
                supervisor=_build_supervisor(),
                context_store=counsel_router._context_store,
                now=_clock,
            ).enqueue(
                tenant_id=_TENANT,
                class_ref=request.class_ref,
                contexts={f"stu_ahead{index}": _draft_context(request)},
            )

    asyncio.run(enqueue())


# ── 1 · 종단 응답에는 다시 오라고 말하지 않는다 ────────────────────


def test_a_finished_job_is_not_asked_to_be_polled_again() -> None:
    """종단(`succeeded`) GET 에는 `Retry-After` 가 **없다.**

    🔴 있으면 어댑터가 **끝난 잡을 계속 폴링한다.** problem 축의 같은 단언은
    `test_http_fixtures.py` 가 든다.
    """
    with TestClient(create_app()) as client:
        posted = _post(client)
        assert posted["status"] == "succeeded", posted
        fetched = _get(client, posted["job_id"])

    assert fetched.status_code == 200, fetched.text
    assert "Retry-After" not in fetched.headers, (
        f"끝난 잡에 Retry-After={fetched.headers.get('Retry-After')} 가 붙었다 — "
        "어댑터가 안 멈춘다"
    )


# ── 2 · 비종단 응답은 언제 다시 오라고 말한다 ──────────────────────


def test_an_unfinished_job_says_when_to_come_back() -> None:
    """비종단(`queued`) GET 은 `Retry-After` 로 **주기를 말한다** — 이 PR 의 본체.

    ⚠ `queued` 를 만드는 방법: 앞선 잡을 상한(K)만큼 쌓으면 회전이 전부 그쪽에 쓰여
    내 잡이 `queued` 로 나간다(99 #21 의 잔여 · `test_counsel_inline_drain.py` 와 같은 장치).
    """
    _enqueue_ahead(get_counsel_settings().counsel_inline_drain_max)
    with TestClient(create_app()) as client:
        posted = _post(client)
        assert posted["status"] == "queued", (
            f"앞선 잡을 K개 뒀는데 내 잡이 돌았다({posted['status']}) — 비종단을 못 만들었다"
        )
        fetched = _get(client, posted["job_id"])

    assert fetched.status_code == 200, fetched.text
    assert fetched.headers.get("Retry-After") == "2", (
        f"비종단인데 Retry-After 가 {fetched.headers.get('Retry-After')!r} 다 — "
        "주기를 안 말해 주면 상대가 자기 상수로 돈다"
    )


# ── 3 · 취소된 잡은 폴링을 멈추게 한다 (함정 A 의 계약) ────────────


def test_a_cancelled_job_stops_the_polling() -> None:
    """🔴 `cancelled` GET 에는 `Retry-After` 가 **없다.**

    **이 검사가 `_can_report_result` 재사용을 막는 유일한 방어다.**

        _REPORTABLE_PHASES = {succeeded, failed}             ← "결과를 실을 수 있나"
        TERMINAL_PHASES    = {succeeded, failed, cancelled}  ← "폴링을 그만해도 되나"
                                             ↑ 이 하나가 차이다

    취소된 잡은 **결과가 영영 없지만 폴링은 끝나야 한다.** `_can_report_result` 를 쓰면
    cancelled 에 헤더가 붙어 **어댑터가 죽은 잡을 영원히 폴링한다.**
    """
    import uuid as _uuid  # noqa: PLC0415

    from ai.api.routers.counsel import _build_supervisor  # noqa: PLC0415

    _enqueue_ahead(get_counsel_settings().counsel_inline_drain_max)
    with TestClient(create_app()) as client:
        posted = _post(client)
        assert posted["status"] == "queued", posted

        async def cancel() -> str:
            #: `cancel_waiting` 은 queued/paused 만 취소한다 — 위에서 queued 를 만든 이유다.
            job = await _build_supervisor().cancel_waiting(
                tenant_id=_TENANT,
                job_id=_uuid.UUID(posted["job_id"]),
                error_code="CANCELLED_BY_TEST",
            )
            return job.phase.value

        assert asyncio.run(cancel()) == "cancelled"
        fetched = _get(client, posted["job_id"])

    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["data"]["status"] == "cancelled", fetched.json()["data"]
    assert "Retry-After" not in fetched.headers, (
        f"취소된 잡에 Retry-After={fetched.headers.get('Retry-After')} 가 붙었다 — "
        "어댑터가 죽은 잡을 영원히 폴링한다(_can_report_result 를 쓴 것이다)"
    )


# ── 4 · 값은 설정에서 오지 리터럴이 아니다 ─────────────────────────


def test_the_interval_comes_from_settings_not_a_literal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """🔴 env 로 주면 그 값이 헤더에 실린다 — 리터럴이면 이 검사가 red 다(03 §1).

    ⚠ **기본값(2)과 다른 값**을 준다. 기본값과 같은 값을 주면 리터럴이어도 통과한다.
    """
    monkeypatch.setenv("COUNSEL_POLL_RETRY_AFTER_SECONDS", "7")
    get_counsel_settings.cache_clear()

    _enqueue_ahead(get_counsel_settings().counsel_inline_drain_max)
    with TestClient(create_app()) as client:
        posted = _post(client)
        assert posted["status"] == "queued", posted
        fetched = _get(client, posted["job_id"])

    assert fetched.headers.get("Retry-After") == "7", (
        f"env 를 7로 줬는데 헤더가 {fetched.headers.get('Retry-After')!r} 다 — "
        "값이 설정을 안 지난다"
    )


# ── 5 · 0 은 기동에서 거부된다 ─────────────────────────────────────


def test_zero_and_negative_are_rejected_at_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ge=1` — `Retry-After: 0` 은 「즉시 다시 오라」라서 **폴링 폭주**다.

    ⚠ 임계 조정이 아니라 **뜻이 안 되는 값**을 기동에서 막는 것이다.
    """
    for value in ("0", "-1"):
        monkeypatch.setenv("COUNSEL_POLL_RETRY_AFTER_SECONDS", value)
        get_counsel_settings.cache_clear()
        with pytest.raises(ValidationError):
            CounselSettings()


# ── 6 · 404 에는 헤더가 없다 ───────────────────────────────────────


def test_a_missing_job_is_not_asked_to_be_polled() -> None:
    """없는 `job_id` 의 404 에는 `Retry-After` 가 없다.

    ⚠ `NotFound` 가 `success_envelope` **전에** raise 되므로 자연히 성립한다 —
    🔴 **성립 이유가 우연이 아님을 못 박는다.** 헤더를 미들웨어나 응답 후처리로 옮기면
    이 검사가 먼저 red 가 된다.
    """
    with TestClient(create_app()) as client:
        fetched = _get(client, "11111111-1111-4111-8111-111111111111")

    assert fetched.status_code == 404, fetched.text
    assert "Retry-After" not in fetched.headers


# ── 7 · 종단 판정이 StrEnum 성질에 말없이 기대지 않는다 ────────────


def test_the_terminal_check_does_not_lean_on_job_phase_being_a_str() -> None:
    """🔴 **행동으로는 못 재는 자리다 — 그래서 소스로 잰다.**

    실측(2026-08-19): `JobPhase` 는 `StrEnum` 이라 `"succeeded" in TERMINAL_PHASES` 가
    **True** 다. ⇒ `JobPhase(view.status)` 를 맨 `view.status` 로 바꿔도 **검사가 전부
    green** 이다(고의 파괴 1-② 실측). 지시서 №1 이 예상한 함정 B —「문자열 비교는 예외
    없이 항상 True」— 는 **이 저장소에 존재하지 않는다.**

    🔴 **그래도 감싼 채로 두는 이유:** 맨 문자열 형태는 `JobPhase` 가 `StrEnum` 이라는
    사실에 **말없이 기대고 있다.** `StrEnum` → `Enum` 으로 바뀌면 비교가 조용히 항상
    True 가 되고 **종단 응답에도 `Retry-After` 가 붙어** 어댑터가 끝난 잡을 영원히
    폴링한다. 그 의존을 없앤 상태를 여기서 못 박는다.

    ⚠ **소스 검사는 최후 수단이다** — 행동이 두 형태를 구분할 수 있으면 그걸로 재야 한다.
    여기서는 구분이 안 된다는 것을 파괴로 확인하고 나서 쓰는 것이다.
    """
    import inspect  # noqa: PLC0415

    from ai.contracts.agents import JobPhase  # noqa: PLC0415

    source = inspect.getsource(counsel_router.get_counsel_draft)
    assert "JobPhase(view.status) not in TERMINAL_PHASES" in source, (
        "종단 판정이 `JobPhase(...)` 를 안 지난다 — `JobPhase` 가 `StrEnum` 이 아니게 되는 "
        "순간 모든 응답에 Retry-After 가 붙는다"
    )
    #: 위 단언이 무엇을 막는지를 값으로도 남긴다 — 이 성질이 사라지면 위험이 현실이 된다.
    assert issubclass(JobPhase, str), (
        "JobPhase 가 더 이상 str 이 아니다 — 맨 문자열 비교를 쓰는 자리가 있으면 지금 "
        "조용히 깨졌다(이 자리는 감싸 두어 안전하다)"
    )
