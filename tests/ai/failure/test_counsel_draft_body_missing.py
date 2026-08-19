"""잡은 **정상 성공**인데 초안 본문이 없다 — 500이 아니라 도메인 상태다 (99 #75).

🔴 **종전에는 GET이 500이었다.** `_wire_result`가 이렇게 적혀 있었다::

    return CounselDraftResult(
        draft_status=WireDraftStatus.GENERATED,      # ← 고정
        text=record.content if record else None,     # ← "없을 수 있다"

**옆줄 둘이 서로를 몰랐다.** 아래 줄은 부재를 안다고 말하는데 윗줄이 `GENERATED`를 고정해서
`_generated_must_be_grounded`가 **반드시** `ValueError`를 던진다 ⇒ 잡힌 정상 성공이 5xx가 된다.
**불변식 4의 반대 방향 위반**이다(에러가 아닌 것이 5xx로 나간다).

⚠ **바로 위 `_restore_result`는 bundle·context 부재를 이미 `None`으로 막아 뒀다** —
방어의 비대칭이지 새 설계가 아니었다.

━━ 🔴 이 입력이 **실제로 일어나는가** ━━

`_draft_store.get`이 `None`을 주는 경로는 **넷**이다(2026-08-19 실측):

    ⓐ `student.draft_id`가 비었다        라우터가 저장소를 아예 안 탄다(`else None`)
    ⓑ 행이 없다 (`miss_absent`)          보존 기간 경과·수동 삭제·워커가 잡만 성공 표시
    ⓒ 남의 테넌트 행 (`miss_foreign_tenant`)  저장소 수준 격리(99 #23)
    ⓓ 초안 저장소만 휘발                 memory 백엔드 재시작 — 잡 원장은 남고 초안만 빈다

이 파일은 **ⓑ·ⓓ**를 잰다 — 실제 인메모리 저장소의 행을 지워 「행이 없다」를 만든다.
⚠ **저장소를 스텁으로 갈아 끼우지 않는다** — 그러면 *"실제로 일어날 수 있는가"* 를 안 재게 된다.
"""

from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

import httpx
import pytest
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers import counsel as counsel_router
from ai.api.routers.counsel import reset_counsel_stores
from ai.composition.counsel.stores import InMemoryDraftResultStore
from ai.db.store_factory import reset_shared_agent_runtime

_TENANT: Final = "t_draft_missing"
_HEADERS: Final = {
    "X-Tenant-Id": _TENANT,
    "X-Request-Id": "rq-draft-missing-1",
    "Idempotency-Key": f"{_TENANT}:counsel:1",
}


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    reset_shared_agent_runtime()
    reset_counsel_stores()
    yield
    reset_shared_agent_runtime()
    reset_counsel_stores()


def _request_body() -> dict[str, Any]:
    """🔴 계약 §4-① 예시를 복제하지 않는다 — 기존 통합 검사의 정본을 **파일로** 읽는다.

    ⚠ 평면 `import test_counsel_router`는 여기서 **안 된다** — `tests/ai/integration`은
    같은 디렉터리가 아니라서 그 폴더가 `sys.path`에 없다(실측: `ModuleNotFoundError`).
    선례는 같은 폴더의 `test_ledger_survives_every_failure._router_request_body`다.
    ⚠ 그 선례는 상대경로라 **CWD에 의존한다** — 여기서는 `__file__` 기준으로 고정한다.
    """
    target = (
        Path(__file__).resolve().parents[1] / "integration" / "test_counsel_router.py"
    )
    spec = importlib.util.spec_from_file_location("_counsel_router_contract", target)
    assert spec is not None and spec.loader is not None, target
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    body: dict[str, Any] = module._REQUEST
    return dict(body)


def _post(client: TestClient) -> str:
    response: httpx.Response = client.post(
        "/v1/counsel/drafts", json=_request_body(), headers=_HEADERS
    )
    assert response.status_code == 202, response.text
    job_id: str = response.json()["data"]["job_id"]
    return job_id


def _drop_every_draft_row() -> int:
    """🔴 **실제 저장소의 행을 지운다** — 보존 만료(ⓑ)·초안 저장소 휘발(ⓓ)의 대역.

    ⚠ 지운 건수를 돌려준다 — 0건이면 이 검사가 **아무 상태도 안 만든 것**이라
    그 뒤의 통과는 의미가 없다.
    ⚠ **저장소를 스텁으로 갈아 끼우지 않는다** — 실제 구현의 행을 비운다.
    """
    store = counsel_router._draft_store
    #: 🔴 **구현을 못박는다** — Protocol에는 행을 지우는 문이 없다(정상이다: 프로덕션이
    #: 지울 일이 아니다). 이 대역은 인메모리 구현에서만 성립하고, 다른 구현이 꽂혀 있으면
    #: 조용히 통과하는 대신 **여기서 멈춰야 한다**.
    assert isinstance(store, InMemoryDraftResultStore), (
        f"인메모리 초안 저장소가 아니다({type(store).__name__}) — 이 대역이 성립하지 않는다"
    )
    rows: dict[Any, Any] = store._rows  # noqa: SLF001
    dropped = len(rows)
    rows.clear()
    return dropped


def _enqueue_decoy() -> None:
    """🔴 큐에 **다른 잡**을 먼저 넣어 POST를 미종단으로 끝낸다.

    `run_next`는 `worker_kind + tenant_id`로만 lease하므로 **내 잡을 지정해 집을 수 없다** —
    앞선 잡이 있으면 내 POST가 미종단으로 끝나고 **늦게 끝나는 잡**이 실제로 만들어진다.
    선례: `tests/ai/integration/test_pg_default_flip_counsel.py` §㉻.

    🔴 **이 우회가 필요한 이유** — POST가 그 자리에서 끝나면 뷰가 **완성된 result와 함께**
    캐시되고, 그 뒤 GET은 `_wire_result`를 **다시 지나지 않는다.** 즉 「본문 부재」는
    **늦은 성공 경로에서만** 도달한다(작업 0-9 실측).
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
        await CounselPackEnqueuer(
            supervisor=_build_supervisor(),
            context_store=counsel_router._context_store,
            now=_clock,
        ).enqueue(
            tenant_id=_TENANT,
            class_ref=request.class_ref,
            contexts={"stu_decoy": _draft_context(request)},
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
            lease_owner="worker-draft-missing",
            run_store=counsel_router._run_store,
        ) as runner:
            for _ in range(4):
                if await runner.run_next(tenant_id=_TENANT) is None:
                    break

    asyncio.run(run())


def _late_success(client: TestClient) -> str:
    """미종단 POST → 워커 완료. 🔴 절단 가드를 여기 담는다."""
    _enqueue_decoy()
    posted = client.post(
        "/v1/counsel/drafts", json=_request_body(), headers=_HEADERS
    ).json()["data"]
    assert posted["status"] != "succeeded", (
        f"POST가 그 자리에서 끝났다({posted['status']}) — 늦은 성공 시나리오가 아니다"
    )
    job_id: str = posted["job_id"]
    assert (
        client.get(f"/v1/counsel/drafts/{job_id}", headers=_HEADERS).json()["data"][
            "result"
        ]
        is None
    ), "미종단인데 결과가 실렸다 — 뷰가 이미 완성돼 복원 경로를 안 탄다"
    _drain()
    return job_id


def test_the_late_success_scenario_is_produced_at_all() -> None:
    """🔴 절단 가드 — 늦은 성공이 안 만들어지면 아래 둘은 아무것도 안 본다."""
    with TestClient(create_app()) as client:
        job_id = _late_success(client)
        assert _drop_every_draft_row() >= 1, (
            "워커가 끝났는데 초안 행이 0건이다 — 「본문 부재」를 만들지 못했다"
        )
        assert job_id


def test_a_missing_body_is_a_domain_state_not_a_500() -> None:
    """🔴 **본문이 없으면 200 + `llm_failed`/`draft_body_missing`** — 500이 아니다.

    종전에는 `draft_status=GENERATED`가 고정이라 `_generated_must_be_grounded`가
    **반드시** 터졌다 ⇒ 잡힌 정상 성공이 GET에서 500이 된다(99 #75).
    """
    with TestClient(create_app()) as client:
        job_id = _late_success(client)
        assert _drop_every_draft_row() >= 1

        response = client.get(f"/v1/counsel/drafts/{job_id}", headers=_HEADERS)

        assert response.status_code == 200, (
            f"본문 부재가 {response.status_code}로 나갔다 — 잡힌 정상 성공이 5xx다 "
            f"(불변식 4의 반대 방향 · 99 #75): {response.text[:400]}"
        )
        result = response.json()["data"]["result"]
        assert result is not None, "복원 경로를 안 탔다 — 이 검사가 눈이 멀었다"
        assert result["draft_status"] == "llm_failed", result
        assert result["status_reason"] == "draft_body_missing", result
        #: 🔴 **없는 본문을 지어내지 않는다** — 부재는 부재로 나간다.
        assert result["text"] is None, result


def test_the_healthy_late_success_is_still_generated() -> None:
    """⚠ 뒤집기 — 행이 남아 있으면 종전대로 `generated`다.

    🔴 한쪽만 보면 «항상 llm_failed»로 짜도 통과한다.
    """
    with TestClient(create_app()) as client:
        job_id = _late_success(client)

        response = client.get(f"/v1/counsel/drafts/{job_id}", headers=_HEADERS)

        assert response.status_code == 200, response.text
        result = response.json()["data"]["result"]
        assert result is not None, "늦은 성공의 본문이 복원되지 않았다"
        assert result["draft_status"] == "generated", result
        assert (result["text"] or "").strip(), result
        assert result["status_reason"] is None, result
