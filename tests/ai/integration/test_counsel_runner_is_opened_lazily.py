"""POST 는 **필요할 때만** 러너를 연다 (99 #127 셋째 항).

🔴 **여는 것 자체가 비용이다.** `assembly.open_counsel_pack_runner` → `_open_saver` 는
`store_backend=pg` 에서 **체크포인터 커넥션**을 열고 닫는다. 인라인 드레인을 걷어
`counsel_inline_drain_max`(K)가 0이 된 뒤로, 이 요청은 러너를 **한 번도 쓰지 않으면서**
매번 그 비용을 냈다.

⚠ **「K=0 이니 안 연다」로 고칠 수 없다** — 멱등 재시도로 **이미 끝난 잡**을 만나면
`runner.result_of` 가 필요하다. 그래서 조건이 아니라 **게으르게** 연다.

🔴 **그리고 여는 것에 딸려 있던 것이 하나 더 있었다** — `require_tracing_disabled`
(㉒-a · 불변식 3). 러너를 늦게 열면 그 검사가 POST 에서 통째로 빠진다. §2 가 그 자리다.

⚠ **검사는 라우터를 통해서 잰다** — 내부 함수를 직접 부르면 배선을 지워도 green 이다.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Final

import httpx
import pytest
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers import counsel as counsel_router
from ai.api.routers.counsel import reset_counsel_stores
from ai.composition.counsel.settings import get_counsel_settings
from ai.db.store_factory import reset_shared_agent_runtime

_TENANT: Final = "t_lazy_runner"
_HEADERS: Final = {
    "X-Tenant-Id": _TENANT,
    "X-Request-Id": "rq-lazy-runner",
    "Idempotency-Key": f"{_TENANT}:counsel:1",
}


def _request_body() -> dict[str, Any]:
    """🔴 계약 예시를 복제하지 않는다 — 기존 통합 검사의 정본을 재사용한다."""
    from test_counsel_router import _REQUEST  # noqa: PLC0415

    return dict(_REQUEST)


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    #: 🔴 `get_counsel_settings` 는 `@lru_cache` 다 — K 를 만지는 검사가 있으니 양쪽에서 비운다.
    get_counsel_settings.cache_clear()
    reset_shared_agent_runtime()
    reset_counsel_stores()
    yield
    get_counsel_settings.cache_clear()
    reset_shared_agent_runtime()
    reset_counsel_stores()


class _CountingOpen:
    """`open_counsel_pack_runner` 를 **세는** 얇은 래퍼.

    ⚠ 🔴 **동작을 바꾸지 않는다** — 원본을 그대로 부르고 호출 수만 센다. 대역으로 갈아치우면
    「안 열렸다」가 아니라 「대역이 안 터졌다」를 재게 된다.
    """

    def __init__(self, original: Any) -> None:  # noqa: ANN401 — asynccontextmanager 팩토리
        self._original = original
        self.calls = 0

    def __call__(self, **kwargs: Any) -> Any:  # noqa: ANN401
        self.calls += 1
        return self._original(**kwargs)


def _count_opens(monkeypatch: pytest.MonkeyPatch) -> _CountingOpen:
    #: 🔴 **라우터 모듈에 패치한다** — 조립부에 패치하면 라우터가 이미 바인딩한
    #: 이름을 안 지나 대역이 안 먹는다. ⚠ 라우터가 재수출은 안 해서(`__all__`)
    #: 이름 조회만 `getattr` 로 우회한다 — 패치 대상은 그대로다.
    counter = _CountingOpen(getattr(counsel_router, "open_counsel_pack_runner"))  # noqa: B009
    monkeypatch.setattr(counsel_router, "open_counsel_pack_runner", counter)
    return counter


def _post(client: TestClient) -> dict[str, Any]:
    response: httpx.Response = client.post(
        "/v1/counsel/drafts", json=_request_body(), headers=_HEADERS
    )
    assert response.status_code == 202, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


# ── ① K=0 — 러너를 아예 안 연다 ───────────────────────────────────


@pytest.mark.no_counsel_drain
def test_a_plain_post_does_not_open_the_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """🔴 **적재만 하는 POST 는 러너를 0회 연다.**

    ⚠ K 를 이 검사가 정하지 않는다 — **운영값 그대로** 잰다. K 가 0이 아닌 환경에서는
    아래 §③ 이 대신 성질을 지킨다(그래서 이 검사는 K==0 에서만 단언한다).
    """
    if get_counsel_settings().counsel_inline_drain_max != 0:
        pytest.skip("K>0 환경 — 이 성질은 §③ 이 잰다")

    counter = _count_opens(monkeypatch)
    with TestClient(create_app()) as client:
        posted = _post(client)

    assert posted["status"] == "queued", posted
    assert counter.calls == 0, (
        f"러너를 {counter.calls}회 열었다 — K=0 이면 이 요청은 러너를 쓰지 않는다. "
        "여는 것만으로 pg 백엔드에서 체크포인터 커넥션이 열렸다 닫힌다(99 #127)."
    )


# ── ② 여는 것에 딸려 있던 fail-closed 는 그대로 돈다 ──────────────


@pytest.mark.no_counsel_drain
def test_the_tracing_guard_still_runs_when_the_runner_is_not_opened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """🔴 **추적 fail-closed 가 러너 개폐와 분리됐다**(㉒-a · 불변식 3).

    종전에는 러너를 여는 것이 곧 `require_tracing_disabled` 였다. 게으르게 열도록 바꾸면서
    그 검사가 POST 에서 빠질 수 있었다 — **라우터가 직접 부르게 옮겼고, 이 검사가 그 자리다.**

    ⚠ 러너가 0회 열렸는데 가드가 1회 이상 돌았다는 **두 단언이 같이 있어야** 뜻이 선다.
    하나만 있으면 «가드를 지우고 러너를 열어도» green 이다.
    """
    if get_counsel_settings().counsel_inline_drain_max != 0:
        pytest.skip("K>0 환경 — 러너가 열리므로 분리 여부를 못 가른다")

    counter = _count_opens(monkeypatch)
    seen: list[str] = []
    original = getattr(counsel_router, "require_tracing_disabled")  # noqa: B009 — 위와 같다

    def _record(worker_kind: str) -> None:
        seen.append(worker_kind)
        original(worker_kind)

    monkeypatch.setattr(counsel_router, "require_tracing_disabled", _record)

    with TestClient(create_app()) as client:
        _post(client)

    assert counter.calls == 0, "러너가 열렸다 — 이 검사의 전제가 깨진다"
    assert seen, "러너를 안 여니 추적 fail-closed 가 통째로 빠졌다(㉒-a 회귀)"
    #: 🔴 조립부와 **같은 값**이어야 한다 — 리터럴을 각자 박으면 갈려도 아무도 모른다.
    assert set(seen) == {"counsel_pack"}, seen


# ── ③ K>0 이면 연다 — 「게으르다」가 「안 연다」는 아니다 ─────────


def test_the_runner_is_opened_once_when_the_drain_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """K 를 올리면 러너가 **열리고**, 회전이 여럿이어도 **한 번만** 열린다.

    🔴 이 검사가 없으면 §① 은 «러너를 영영 안 여는» 구현으로도 green 이다.
    ⚠ 값을 env 로 준다 — 코드에 K 를 박지 않는다(03 §1).
    """
    monkeypatch.setenv("COUNSEL_INLINE_DRAIN_MAX", "2")
    get_counsel_settings.cache_clear()
    assert get_counsel_settings().counsel_inline_drain_max == 2

    counter = _count_opens(monkeypatch)
    with TestClient(create_app()) as client:
        posted = _post(client)

    assert counter.calls == 1, (
        f"러너를 {counter.calls}회 열었다 — 회전이 2회여도 러너는 하나여야 한다"
    )
    assert posted["status"] == "succeeded", (
        f"K=2 인데 POST 가 자기 잡을 못 돌렸다({posted['status']})"
    )
