"""큐에 앞선 잡이 있어도 **내 POST 가 자기 결과를 받는다** (99 #21).

🔴 **`run_next`는 큐를 비우는 함수이지 「내 잡을 실행하는」 함수가 아니다.** `lease_next`가
`worker_kind + tenant_id`로만 집고 **`job_id`를 지정할 수 없어서**(`agents/job_store.py`),
앞선 잡이 있으면 POST가 **남의 잡**을 돌리고 내 잡은 `queued`로 남았다.

⚠ **그리고 그걸 풀 주체가 없었다** — counsel에는 배경 드레인이 없고 GET은 잡을 안 돌린다
(실측 2026-08-19: `get_counsel_draft` 경로에 `run_next` **0건**). ⇒ **BE가 폴링해도 영영
안 풀린다.** 재현(같은 날): 앞선 잡 2개 → POST `queued` → GET 2회 폴링해도 `queued`.

⇒ **POST가 자기 잡이 끝날 때까지 유한 반복한다**(상한 `counsel_inline_drain_max`).

🔴 **검사는 전부 라우터를 통해서 잰다** — 내부 함수를 직접 부르면 배선을 지워도 green이다
(PR-α에서 고의 파괴가 두 번 헛돌았다).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import AbstractAsyncContextManager
from typing import Any, Final

import httpx
import pytest
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers import counsel as counsel_router
from ai.api.routers.counsel import reset_counsel_stores
from ai.composition.counsel.settings import get_counsel_settings
from ai.composition.counsel.worker import CounselPackRunner
from ai.contracts.agents import WorkerJob
from ai.db.store_factory import reset_shared_agent_runtime

_TENANT: Final = "t_inline_drain"
_HEADERS: Final = {
    "X-Tenant-Id": _TENANT,
    "X-Request-Id": "rq-inline-drain",
    "Idempotency-Key": f"{_TENANT}:counsel:1",
}


def _request_body() -> dict[str, Any]:
    """🔴 계약 §4-① 예시를 복제하지 않는다 — 기존 통합 검사의 정본을 재사용한다."""
    from test_counsel_router import _REQUEST  # noqa: PLC0415

    return dict(_REQUEST)


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    reset_shared_agent_runtime()
    reset_counsel_stores()
    yield
    reset_shared_agent_runtime()
    reset_counsel_stores()


def _k() -> int:
    return get_counsel_settings().counsel_inline_drain_max


def _enqueue_others(count: int, *, tenant_id: str = _TENANT) -> None:
    """큐에 **앞선 잡**을 넣는다 — FIFO라 이게 먼저 lease된다.

    ⚠ 리터럴 개수를 박지 않는다 — 호출자가 `K` 기준으로 정한다.
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
                tenant_id=tenant_id,
                class_ref=request.class_ref,
                contexts={f"stu_ahead{index}": _draft_context(request)},
            )

    asyncio.run(enqueue())


def _post(client: TestClient, *, key: str = _HEADERS["Idempotency-Key"]) -> dict[str, Any]:
    response: httpx.Response = client.post(
        "/v1/counsel/drafts",
        json=_request_body(),
        headers={**_HEADERS, "Idempotency-Key": key},
    )
    assert response.status_code == 202, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def _get(client: TestClient, job_id: str) -> dict[str, Any]:
    response = client.get(f"/v1/counsel/drafts/{job_id}", headers=_HEADERS)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


# ── ① 재현 그대로 — 앞선 잡이 있어도 내 결과가 온다 ────────────────


def test_my_job_runs_even_when_others_are_queued_ahead() -> None:
    """🔴 **0-9 재현을 그대로 검사로.** 앞선 잡이 있어도 내 POST 가 자기 결과를 받는다.

    ⚠ 종전에는 `queued`로 나갔고 **GET 폴링으로도 안 풀렸다**(배경 드레인이 없다).
    """
    _enqueue_others(_k() - 1)
    with TestClient(create_app()) as client:
        posted = _post(client)
        assert posted["status"] == "succeeded", (
            f"앞선 잡이 있다고 내 잡이 안 돌았다({posted['status']}) — "
            "이걸 풀 주체가 없어서 폴링해도 영영 안 풀린다 (99 #21)"
        )
        got = _get(client, posted["job_id"])

    assert got["status"] == "succeeded"
    assert got["result"] is not None, "종단인데 결과가 비었다"
    assert got["result"]["draft_status"] == "generated", got["result"]


def test_a_plain_post_still_answers_in_place() -> None:
    """🔴 회귀 — 큐가 빈 정상 단발 POST 가 여전히 그 자리에서 결과를 받는다."""
    with TestClient(create_app()) as client:
        posted = _post(client)
    assert posted["status"] == "succeeded", posted


# ── ② 상한 — 무한이 아니다 (불변식 6) ─────────────────────────────


def test_the_drain_stops_at_the_bound() -> None:
    """🔴 **K 회전에서 멈춘다** — 큐가 아무리 길어도 무한히 돌지 않는다.

    앞선 잡을 **K개** 넣으면 회전이 전부 그쪽에 쓰이고 **내 잡은 `queued`로 남는다.**
    ⚠ 그게 이 판정의 **대가**다 — 잔여는 99 #21에 적었다(다음 POST 를 기다린다).
    ⚠ **이 검사가 상한의 증명이다** — 상한이 없으면 큐를 다 비우고 내 잡까지 돌아
    `succeeded`가 된다(그리고 그건 응답이 큐 깊이만큼 늘어난다는 뜻이다).
    """
    _enqueue_others(_k())
    with TestClient(create_app()) as client:
        posted = _post(client)

    assert posted["status"] == "queued", (
        f"K({_k()})개를 앞에 뒀는데 내 잡이 돌았다({posted['status']}) — 상한이 안 걸린다"
    )


def test_the_bound_comes_from_settings_not_a_literal() -> None:
    """K 는 **설정**에서 온다 — 코드 리터럴이 아니다(03 §1).

    ⚠ 값 자체를 단정하지 않는다(그러면 설정을 바꿀 때 이 검사가 이유 없이 red다).
    **경로가 설정을 지나는지**만 본다.
    """
    settings = get_counsel_settings()
    assert settings.counsel_inline_drain_max >= 1
    assert "counsel_inline_drain_max" in type(settings).model_fields


# ── ③ 남의 잡 실패가 내 요청을 죽이지 않는다 ──────────────────────


class _FirstCallFails:
    """`run_next` 첫 호출만 터뜨리는 얇은 래퍼 — 남의 잡 실행 실패를 재현한다.

    ⚠ **러너를 통째로 스텁으로 갈지 않는다** — 실제 러너를 열고 **한 메서드만** 감싼다.
    통째로 갈면 *"실제로 일어날 수 있는가"* 를 안 재게 된다.
    """

    def __init__(self, managed: AbstractAsyncContextManager[CounselPackRunner]) -> None:
        self._managed = managed
        self.calls = 0

    async def __aenter__(self) -> CounselPackRunner:
        runner = await self._managed.__aenter__()
        real = runner.run_next

        async def run_next(*args: object, **kwargs: object) -> WorkerJob | None:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("남의 잡 실행 실패(대역)")
            return await real(*args, **kwargs)  # type: ignore[arg-type]

        runner.run_next = run_next  # type: ignore[method-assign]
        return runner

    async def __aexit__(self, *exc: object) -> bool | None:
        return await self._managed.__aexit__(*exc)  # type: ignore[arg-type]


def test_another_jobs_failure_does_not_break_my_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """🔴 남의 잡 실행이 터져도 **내 요청이 5xx 가 되면 안 된다.**

    ⚠ 그렇다고 조용히 삼키지도 않는다 — 라우터가 `logger.warning`으로 남긴다.
    ⚠ **예외 뒤에도 계속 돈다** — 한 번 터졌다고 멈추면 내 잡이 영영 안 돈다.
    """
    _enqueue_others(_k() - 1)
    #: ⚠ `getattr` 로 잡는다 — 라우터가 재수출하지 않는 이름이라 직접 참조는 mypy 가 막는다.
    original = getattr(counsel_router, "open_counsel_pack_runner")  # noqa: B009
    wrappers: list[_FirstCallFails] = []

    def flaky(**kwargs: object) -> _FirstCallFails:
        wrapper = _FirstCallFails(original(**kwargs))
        wrappers.append(wrapper)
        return wrapper

    monkeypatch.setattr(counsel_router, "open_counsel_pack_runner", flaky)
    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/counsel/drafts", json=_request_body(), headers=_HEADERS
        )

    assert response.status_code == 202, (
        f"남의 잡 실패가 내 요청을 {response.status_code}로 만들었다: {response.text[:300]}"
    )
    assert wrappers and wrappers[0].calls >= 2, (
        "예외 뒤에 계속 돌지 않았다 — 내 잡이 영영 안 돈다"
    )


# ── ④ 테넌트 격리 — 남의 테넌트 잡은 안 집힌다 ────────────────────


def test_another_tenants_queue_is_not_drained() -> None:
    """🔴 `lease_next` 가 **테넌트 스코프**라 반복이 그 테넌트 큐로 유계다.

    실측(2026-08-19): 구현이 `job.tenant_id == tenant_id`로 거른다. **이게 판정 ⓑ의
    유계 근거다** — 스코프가 아니면 K 회전이 남의 테넌트 큐까지 돈다.
    """
    _enqueue_others(_k(), tenant_id="t_intruder")
    with TestClient(create_app()) as client:
        posted = _post(client)

    assert posted["status"] == "succeeded", (
        f"남의 테넌트 큐가 내 회전을 먹었다({posted['status']}) — 테넌트 스코프가 아니다"
    )
