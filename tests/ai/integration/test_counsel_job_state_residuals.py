"""잡 상태 기계의 잔여 둘 — 수렴 실패 · 복원 실패 (99 #88·#89).

🔴 **paused 축은 이 PR 이 아니다** — 판정(ⓓ)에서 걷어 냈다. `resume` 기계는
(`assert_paused_resume` · `Supervisor.resume` · `JobStore.resume`) **이미 서 있고**
없는 것은 **HTTP 표면 하나**다. 그건 N>1(월별 벌크)이 올 때 연다.

🔴 **빈 응답 축(99 #87)도 이 PR 이 아니다** — 결손이 `provider.py` 한 줄이 아니라
`graph.py` 의 **흐름**이라(재생성 루프를 빠져나온다) 재생성 상한·서킷과 얽힌다.

🔴 **검사는 전부 라우터를 통해서 잰다** — 내부 함수를 직접 부르면 배선을 지워도 green 이다
(PR-α에서 고의 파괴가 두 번 헛돌았다).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from typing import Any, Final

import httpx
import pytest
from counsel_text import DEFAULT_DRAFT
from fastapi.testclient import TestClient

from ai.agents.supervisor import Supervisor
from ai.api.app import create_app
from ai.api.routers import counsel as counsel_router
from ai.api.routers.counsel import reset_counsel_stores, set_counsel_provider
from ai.composition.counsel.provider import (
    FakeCounselProvider,
)
from ai.composition.counsel.settings import get_counsel_settings
from ai.composition.counsel.stores import InMemoryContextStore
from ai.contracts.composition import DraftContext
from ai.db.store_factory import reset_shared_agent_runtime

_TENANT: Final = "t_job_state"
_HEADERS: Final = {
    "X-Tenant-Id": _TENANT,
    "X-Request-Id": "rq-job-state",
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


def _draft_context() -> DraftContext:
    """게이트 검사용 컨텍스트 — 라우터가 쓰는 **같은 함수**로 만든다."""
    from ai.api.routers.counsel import _draft_context as build  # noqa: PLC0415
    from ai.contracts.counsel import CounselDraftRequest  # noqa: PLC0415

    return build(CounselDraftRequest.model_validate(_request_body()))


def _post(client: TestClient) -> dict[str, Any]:
    response: httpx.Response = client.post(
        "/v1/counsel/drafts", json=_request_body(), headers=_HEADERS
    )
    assert response.status_code == 202, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def _get(client: TestClient, job_id: str) -> dict[str, Any]:
    response = client.get(f"/v1/counsel/drafts/{job_id}", headers=_HEADERS)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


# ══ 작업 3 — 수렴 호출이 실패해도 원인을 덮지 않는다 (99 #89) ══


class _Exploding(FakeCounselProvider):
    """워커 실행을 터뜨리는 대역 — **수렴 경로**를 태우기 위한 원인 예외를 만든다."""

    async def write(self, **kwargs: object) -> str:
        del kwargs
        raise RuntimeError("원인-표식")


def test_a_convergence_failure_preserves_the_cause(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """🔴 **수렴 호출이 실패해도 원인 예외가 살아 나간다** (99 #89).

    워커는 `except Exception: return await self._fail(...)` 로 잡을 종단시키는데,
    **그 `_fail` 자체가 raise 하면**(fencing 거부·DB 장애) 새 예외가 **원인을 교체**하고
    밖으로 나간다 — *"LLM 이 죽었다"* 가 *"수렴이 죽었다"* 로 읽혀 **진단이 뒤집힌다.**

    ⚠ **잡 상태를 억지로 바꾸지 않는다** — 수렴이 실패한 것이지 잡이 끝난 게 아니다.
    안전망은 recovery 다(`Supervisor.run_next` 가 만료분을 먼저 회수한다).
    """
    set_counsel_provider(_Exploding(drafts=[DEFAULT_DRAFT]))

    async def exploding_fail(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("수렴-실패")

    monkeypatch.setattr(Supervisor, "fail", exploding_fail)

    with caplog.at_level(logging.WARNING), TestClient(create_app()) as client:
        response = client.post(
            "/v1/counsel/drafts", json=_request_body(), headers=_HEADERS
        )

    #: ⚠ 요청 자체는 산다 — 인라인 드레인이 잡 실행 실패를 삼키고 계속한다(99 #21).
    #:   이 검사의 축은 **원인이 보존되는가**이지 HTTP 코드가 아니다.
    assert response.status_code == 202, response.text

    #: 🔴 **수렴 실패가 남는다** — 조용히 삼키면 「잡이 종단으로 못 갔다」가 어디에도 없다.
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "잡 수렴 실패" in messages, messages[:300]

    #: 🔴 **원인이 살아 있다** — 수렴 실패가 원인을 덮으면 트레이스백에 "수렴-실패"만 남는다.
    traces = "\n".join(
        record.exc_text or "" for record in caplog.records if record.exc_info
    )
    assert "원인-표식" in traces, (
        f"수렴 실패가 원인 예외를 교체했다 — 진단이 뒤집힌다: {traces[-400:]!r}"
    )


# ══ 작업 4 — 복원 실패가 캐시에 굳지 않는다 (99 #88) ══


def _enqueue_others(count: int) -> None:
    """큐에 **앞선 잡**을 넣어 POST 를 미종단으로 끝낸다 — 「늦은 성공」의 재료."""

    async def enqueue() -> None:
        from ai.api.routers.counsel import _build_supervisor, _clock  # noqa: PLC0415
        from ai.api.routers.counsel import _draft_context as build  # noqa: PLC0415
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
                contexts={f"stu_ahead{index}": build(request)},
            )

    asyncio.run(enqueue())


def _drain() -> None:
    """워커 대역 — 남은 잡을 끝까지 돌린다(라우터를 안 지난다)."""

    async def run() -> None:
        from ai.api.routers.counsel import _REGEN_MAX, _build_supervisor  # noqa: PLC0415
        from ai.composition.counsel.assembly import (  # noqa: PLC0415
            open_counsel_pack_runner,
        )

        provider = counsel_router.require_counsel_provider()
        async with open_counsel_pack_runner(
            supervisor=_build_supervisor(),
            context_store=counsel_router._context_store,
            step_sink=counsel_router._step_sink,
            draft_store=counsel_router._draft_store,
            pack_store=counsel_router._pack_store,
            planner=provider,
            writer=provider,
            regen_max=_REGEN_MAX,
            lease_owner="worker-job-state",
            run_store=counsel_router._run_store,
        ) as runner:
            for _ in range(8):
                if await runner.run_next(tenant_id=_TENANT) is None:
                    break

    asyncio.run(run())


def test_a_failed_restore_is_retried_on_the_next_get() -> None:
    """🔴 **복원 실패가 캐시에 굳지 않는다** — 저장소가 살아나면 다음 GET 이 결과를 준다.

    종전에는 `status` 만 갱신한 뷰가 캐시되고, 다음 GET 이 `phase == cached.status` 로
    **조기 반환**해서 `succeeded` + `result=null` 이 **영구 고정**됐다(04 가 금지한 조합) —
    **저장소가 복구돼도 다시 시도하지 않았다.**

    ⚠ **재현은 「늦은 성공」 경로다** — 앞선 잡을 K개 넣어 POST 를 미종단으로 만들고
    (뷰가 `result=None` 으로 캐시된다) 워커로 끝낸 뒤 **컨텍스트 묶음을 비워 복원을
    실패**시킨다(보존 기간 경과의 대역). 그 다음 되살린다.
    """
    set_counsel_provider(FakeCounselProvider(drafts=[DEFAULT_DRAFT] * 8))
    _enqueue_others(get_counsel_settings().counsel_inline_drain_max)
    with TestClient(create_app()) as client:
        posted = _post(client)
        assert posted["status"] != "succeeded", (
            f"POST 가 그 자리에서 끝났다({posted['status']}) — 늦은 성공 시나리오가 아니다"
        )
        job_id = posted["job_id"]
        assert _get(client, job_id)["result"] is None, "미종단인데 결과가 실렸다"

        _drain()

        store = counsel_router._context_store
        #: 🔴 구현을 못박는다 — Protocol 에는 행을 지우는 문이 없다(정상이다).
        #: 다른 구현이 꽂혀 있으면 조용히 통과하는 대신 **여기서 멈춰야 한다.**
        assert isinstance(store, InMemoryContextStore), type(store).__name__
        saved = dict(store._rows)  # noqa: SLF001 — 보존 기간 경과의 대역
        store._rows.clear()  # noqa: SLF001
        first = _get(client, job_id)
        assert first["result"] is None, (
            "복원 소스를 비웠는데 결과가 나왔다 — 이 검사가 눈이 멀었다"
        )

        store._rows.update(saved)  # noqa: SLF001
        second = _get(client, job_id)

    assert second["result"] is not None, (
        "복원 실패가 캐시에 굳었다 — 저장소가 살아나도 `succeeded + result=null` 이 "
        "영구 고정된다 (99 #88)"
    )


def test_a_healthy_view_is_still_served_from_cache() -> None:
    """⚠ 뒤집기 — 결과가 있는 뷰는 **여전히 캐시에서** 나온다(조기 반환을 없앤 게 아니다)."""
    set_counsel_provider(FakeCounselProvider(drafts=[DEFAULT_DRAFT]))
    with TestClient(create_app()) as client:
        job_id = _post(client)["job_id"]
        first = _get(client, job_id)
        second = _get(client, job_id)

    assert first["result"] == second["result"]
    assert second["result"] is not None


# ══ 회귀 — 정상 경로가 그대로다 ══


def test_the_happy_path_is_unchanged() -> None:
    """🔴 회귀 — POST → GET → refine 이 그대로다."""
    set_counsel_provider(FakeCounselProvider(drafts=[DEFAULT_DRAFT, DEFAULT_DRAFT]))
    with TestClient(create_app()) as client:
        job_id = _post(client)["job_id"]
        assert _get(client, job_id)["result"]["draft_status"] == "generated"
        refined = client.post(
            f"/v1/counsel/drafts/{job_id}/refine",
            json={"instruction": "조금 더 부드럽게", "turn_no": 1},
            headers={**_HEADERS, "Idempotency-Key": f"{_TENANT}:refine:1"},
        )
    assert refined.status_code == 200, refined.text
