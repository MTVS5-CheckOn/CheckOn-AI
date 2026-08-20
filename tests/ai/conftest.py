"""🔴 **배경 드레인 대역** — `counsel_inline_drain_max`(K)를 0으로 내린 뒤의 검사 기반.

프로덕션에서 `POST /v1/counsel/drafts`는 잡을 **적재만** 하고(K=0), 실행은 배경 워커
(`ai.composition.counsel.drain`)가 한다. 검사 프로세스에는 그 워커가 없으므로
**이 픽스처가 그 자리**다 — POST가 202로 돌아온 직후 그 테넌트의 큐를 한 번 돌린다.

🔴 **왜 호출 자리마다 안 넣고 여기에 두나** (2026-08-20)
  ⓐ 자리가 60곳이 넘는다. 한 곳을 빠뜨리면 그 검사는 **red 가 아니라 「눈이 먼」 상태**가
    된다 — `result`가 `None`인데 단정이 그걸 안 본다. **빠뜨림이 안 보이는** 종류다.
  ⓑ **프로덕션도 암묵적이다.** 라우터는 드레인을 안 부르고 워커가 알아서 돈다. 검사만
    명시적으로 만들면 **구조가 갈리고**, 갈린 쪽이 정본인 척한다.

⚠ **테스트 전용 경로가 아니다.** `drain_once`는 라우터 인라인 루프·배경 워커와 **같은**
`runner.run_next`를 부른다(`tests/ai/fakes/counsel_drain.py`). 그 등가성은
`tests/ai/integration/test_drain_once_bridge.py`가 잠근다(결정 로그 127).

⚠ **동기 드레인은 프로덕션보다 강하다** — 실제 워커는 「언젠가」 돈다. 이 대역은 결정론을
위해 「즉시」 돈다(가짜 시계와 같은 성격). 관측 지점은 어차피 GET이고 BE도 폴링한다.

🔴 **`queued` 자체를 재는 검사는 `@pytest.mark.no_counsel_drain`으로 뺀다** — 그 마커가
없으면 「적재만 하고 끝난 상태」를 관측할 수 없다.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from starlette.testclient import TestClient

#: 🔴 상한이 있다(불변식 6). 큐가 비면 `drain_once`가 먼저 멈추므로 이 수는 **천장**이다.
#: 앞선 잡을 쌓는 검사가 있어 1로는 모자란다.
_DRAIN_ROTATIONS = 8
_COUNSEL_POST = "/v1/counsel/drafts"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "no_counsel_drain: POST 뒤 배경 드레인 대역을 돌리지 않는다 "
        "(`queued` 상태 자체를 재는 검사)",
    )


@pytest.fixture(autouse=True)
def _counsel_background_drain(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    if request.node.get_closest_marker("no_counsel_drain") is not None:
        yield
        return

    from counsel_drain import drain_once

    original = TestClient.post

    def post(
        self: TestClient, url: str, *args: object, **kwargs: object
    ) -> httpx.Response:
        response = original(self, url, *args, **kwargs)
        if response.status_code == 202 and str(url).endswith(_COUNSEL_POST):
            headers = kwargs.get("headers") or {}
            tenant = headers.get("X-Tenant-Id") if isinstance(headers, dict) else None
            if tenant:
                drain_once(str(tenant), rotations=_DRAIN_ROTATIONS)
        return response

    monkeypatch.setattr(TestClient, "post", post)
    yield
