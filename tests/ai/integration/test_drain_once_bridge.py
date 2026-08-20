"""🔴 `drain_once` — 이관을 「나눌 수 있게」 만드는 다리 (№23 §A).

№22 가 못 한 이유는 「크다」가 아니라 **「한 덩어리라 나눌 수 없다」**였다: K 를 먼저 0 으로
내리면 54 자리가 **한꺼번에** red 라 한 PR 에 다 넣어야 했다.

⇒ 순서를 뒤집는다. **POST 뒤에 한 회전을 돌려 두면** 인라인이 있든 없든 결과가 같다:

    K=1  POST 가 이미 끝냈다   → `drain_once` 는 **0** (no-op)
    K=0  POST 는 `queued`      → `drain_once` 가 **돌린다**

⇒ 이관을 **K=1 인 채로** 먼저 머지하고, `K=1→0` 은 마지막에 한 줄이 된다.
🔴 **중간 상태가 항상 green 이다** — 그것이 이 설계의 산출물이다.
"""

from __future__ import annotations

import ast
import inspect
import json
import pathlib
from collections.abc import Iterator
from typing import Final

import pytest
from counsel_drain import drain_once
from fastapi.testclient import TestClient

from ai.api.app import app
from ai.api.routers.counsel import reset_counsel_stores
from ai.composition.counsel.settings import get_counsel_settings
from ai.db.store_factory import reset_shared_agent_runtime

_REQUEST: Final = json.loads(
    pathlib.Path("tests/ai/contract/fixtures/http/post_counsel_drafts.request.json").read_text(
        encoding="utf-8"
    )
)
_TENANT: Final = "t_bridge"
_HEADERS: Final = {
    "X-Tenant-Id": _TENANT,
    "X-Request-Id": "bridge-1",
    "Idempotency-Key": "bridge-1",
}


@pytest.fixture(autouse=True)
def _isolated() -> Iterator[None]:
    #: 🔴 공용 잡 원장도 같이 비운다 — 안 비우면 앞 테스트가 남긴 queued 잡을
    #: 이 파일의 `run_next` 가 집어가고, 그 실패가 «내 잡이 안 돌았다» 로 보인다(99 ㊒).
    reset_shared_agent_runtime()
    reset_counsel_stores()
    get_counsel_settings.cache_clear()
    yield
    reset_counsel_stores()
    reset_shared_agent_runtime()
    get_counsel_settings.cache_clear()


def _calls_in(func: object) -> set[str]:
    """이 함수가 부르는 **속성 호출 이름** 전부 — 소스에서 읽는다."""
    tree = ast.parse(inspect.getsource(func))  # type: ignore[arg-type]
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


def test_the_bridge_calls_the_same_function_production_calls() -> None:
    """🔴 **테스트 전용 경로가 아니다** — 프로덕션이 부르는 그 함수를 부른다.

    ⚠ 다른 코드를 돌면 그 검사는 프로덕션을 안 잰다(결정 로그 127 · PR-07 이 배운 형태).
    라우터의 인라인 루프와 배경 워커가 **둘 다** `run_next` 를 부르므로 그것이 정본이다.
    """
    from counsel_drain import adrain_once

    from ai.api.routers import counsel as router
    from ai.composition.counsel import drain

    assert "run_next" in _calls_in(adrain_once), "다리가 run_next 를 안 부른다"
    #: 🔴 인라인 루프는 `post_counsel_draft` 가 아니라 그 아래 `_generate` 에 있다.
    assert "run_next" in inspect.getsource(router._generate), (  # noqa: SLF001
        "라우터 인라인 경로가 run_next 를 안 부른다 — 정본이 바뀌었다"
    )
    assert "run_next" in inspect.getsource(drain._run_forever_with_real_stores), (  # noqa: SLF001
        "배경 워커가 run_next 를 안 부른다 — 정본이 바뀌었다"
    )


def _post_and_drain(client: TestClient) -> tuple[str | None, int, dict[str, object]]:
    response = client.post("/v1/counsel/drafts", json=_REQUEST, headers=_HEADERS)
    assert response.status_code == 202, response.text
    posted = response.json()["data"]
    ran = drain_once(_TENANT, rotations=3)
    got = client.get(f"/v1/counsel/drafts/{posted['job_id']}", headers={"X-Tenant-Id": _TENANT})
    assert got.status_code == 200, got.text
    return posted["status"], ran, got.json()["data"]


def _pin_k(monkeypatch: pytest.MonkeyPatch, value: int) -> None:
    """🔴 **라우터가 부르는 이름을 바꾼다** — `setenv` 는 늦는다.

    실측(8/20): `monkeypatch.setenv` + `cache_clear` 로는 K 가 안 바뀌었다 — 기동 경로가
    설정을 먼저 읽어 캐시에 앉힌다. ⇒ 라우터 모듈의 접근자를 직접 고정한다.
    """
    from ai.api.routers import counsel as router
    from ai.composition.counsel.settings import CounselSettings

    pinned = CounselSettings(counsel_inline_drain_max=value)
    monkeypatch.setattr(router, "get_counsel_settings", lambda: pinned)


def test_with_the_inline_drain_the_bridge_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """K=1 — POST 가 이미 끝냈으므로 다리는 **0** 을 돌려준다."""
    _pin_k(monkeypatch, 1)
    with TestClient(app) as client:
        posted, ran, view = _post_and_drain(client)
    assert posted == "succeeded"
    assert ran == 0, f"인라인이 끝냈는데 다리가 {ran}건을 더 돌렸다"
    assert view["status"] == "succeeded"


_K0_SCRIPT: Final = """
import json, sys
sys.path.insert(0, "tests/ai/fakes")
from fastapi.testclient import TestClient
from counsel_drain import drain_once
from ai.api.app import app
from ai.api.routers.counsel import reset_counsel_stores
from ai.db.store_factory import reset_shared_agent_runtime

REQ = json.load(open("tests/ai/contract/fixtures/http/post_counsel_drafts.request.json"))
H = {"X-Tenant-Id": "t_k0", "X-Request-Id": "k0", "Idempotency-Key": "k0"}
reset_shared_agent_runtime()
reset_counsel_stores()
with TestClient(app) as c:
    r = c.post("/v1/counsel/drafts", json=REQ, headers=H)
    d = r.json()["data"]
    ran = drain_once("t_k0", rotations=3)
    g = c.get("/v1/counsel/drafts/" + d["job_id"], headers={"X-Tenant-Id": "t_k0"}).json()["data"]
    res = g.get("result") or {}
    print(json.dumps({"posted": d["status"], "ran": ran, "got": g["status"],
                      "draft_status": res.get("draft_status"),
                      "citations": len(res.get("citations") or [])}))
"""


def test_without_the_inline_drain_the_bridge_runs_the_job() -> None:
    """🔴 K=0 — POST 는 `queued` 이고 **다리가 돌린다.** 결과는 K=1 과 같다.

    ⚠ **하위 프로세스로 잰다** — `monkeypatch.setenv`·`setattr` 둘 다 이 자리에서 안 먹었다
    (실측 8/20: 기동 경로가 설정을 먼저 읽어 캐시에 앉히고, autouse 픽스처가 뒤늦게 덮는다).
    ⇒ **환경을 프로세스 경계에서 준다** — 그게 배포가 K 를 주는 방식과도 같다.
    """
    import os
    import subprocess
    import sys

    env = {**os.environ, "COUNSEL_INLINE_DRAIN_MAX": "0",
           "LLM_PROVIDER": "fake", "STORE_BACKEND": "memory"}
    done = subprocess.run(
        [sys.executable, "-c", _K0_SCRIPT], env=env, capture_output=True, text=True, timeout=180
    )
    assert done.returncode == 0, done.stderr[-2000:]
    measured = json.loads(done.stdout.strip().splitlines()[-1])
    assert measured["posted"] == "queued", f"K=0 인데 POST 가 잡을 돌렸다: {measured}"
    assert measured["ran"] >= 1, f"K=0 인데 다리가 잡을 못 돌렸다: {measured}"
    assert measured["got"] == "succeeded", measured
    assert measured["draft_status"] == "generated", measured
    assert measured["citations"], "근거가 비었다(불변식 2)"
