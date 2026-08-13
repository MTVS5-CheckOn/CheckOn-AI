"""🔴 실패 응답이 **자기 엔드포인트의 버전**을 단다 (99 ㊓).

`api/app.py`의 두 예외 핸들러가 `detection_versions()`를 **하드코딩**했다. 그래서 counsel·
classify·imports·confirmations의 **모든 실패 응답이 detect 버전을 달고 나간다.**

실측(8/8 · 이 파일을 쓰기 전):

    엔드포인트      성공 응답                          실패 응답
    detect        detection-rules-0.2  thr=default-v3   detection-rules-0.2  thr=default-v3
    classify      classify-0.1         thr=None         detection-rules-0.2  thr=default-v3  ✗
    counsel       counsel-pack-0.1     thr=None         detection-rules-0.2  thr=default-v3  ✗
    imports       —                                     detection-rules-0.2  thr=default-v3  ✗
    confirmations —                                     detection-rules-0.2  thr=default-v3  ✗

⚠ **detect만 맞는 이유는 하드코딩된 것이 detect의 것이기 때문**이다 — 우연이다.

🔴 **04 §2.2 A판정(7/22)이 이미 정본을 정해 놨다:** *"실행 전 오류(헤더 누락 등 config 확정
전)의 조립 규칙은 엔드포인트가 아는 **정적 앱 버전 + 기본 config의 threshold**로 채운다
(실행 여부와 무관하게 **그 엔드포인트의** 버전 정보)."* 구현이 그 판정을 안 따랐다.

⚠ **영향:** BE가 실패 로그로 버전을 되짚으면 counsel 장애가 **detect 버전으로 기록된다** —
불변식 8의 재현이 반쪽이 된다. `threshold=default-v3`는 더 나쁘다: counsel은 임계 시트를
쓰지도 않는데 값이 실려 *"그 실행이 임계 v3를 썼다"* 는 거짓 정보가 남는다.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient

from ai.api.app import ROUTER_VERSION_SCOPES, create_app
from ai.api.routers.classify import reset_inquiry_class_store
from ai.api.routers.counsel import reset_counsel_stores
from ai.api.routers.detect import reset_detection_store, reset_idempotency_store
from ai.api.routers.imports import reset_import_stores
from ai.db.store_factory import reset_shared_agent_runtime

_HEADERS = {"X-Tenant-Id": "t1", "X-Request-Id": "rq-scope", "Idempotency-Key": "k"}

#: 존재하지 않는 job_id — counsel GET의 404를 낸다(존재 은닉 경로).
_ABSENT_JOB = "00000000-0000-4000-8000-000000000000"


def _golden(path: str) -> Any:  # noqa: ANN401 — 통합 테스트 픽스처를 재사용한다
    """요청 픽스처를 **파일 경로로** 불러 쓴다 — 바디를 새로 짓지 않는다."""
    spec = importlib.util.spec_from_file_location(path.replace("/", "."), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    _reset_all()
    yield
    _reset_all()


def _reset_all() -> None:
    reset_idempotency_store()
    reset_detection_store()
    reset_inquiry_class_store()
    reset_shared_agent_runtime()  # A·B 공용 잡 원장(99 ㊒)
    reset_counsel_stores()
    reset_import_stores()


def registered_v1_paths(app: Any) -> set[str]:  # noqa: ANN401 — FastAPI
    """앱에 **실제로 등록된** `/v1/*` 경로 전수.

    ⚠ FastAPI 0.139는 `include_router`를 `_IncludedRouter`로 감싸 **라우트를 평탄화하지
    않는다** — `app.routes`를 그냥 훑으면 `/openapi.json` 같은 기본 경로만 나온다.
    실제 경로는 `original_router.routes`에 있다. 이 사실을 모르면 *"등록된 라우터가
    없다"* 로 조용히 통과하는 검사를 짜게 된다(8/8에 실제로 그랬다).
    """
    paths: set[str] = set()
    for route in app.routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            paths |= {getattr(sub, "path", "") for sub in included.routes}
        elif hasattr(route, "path"):
            paths.add(route.path)
    return {path for path in paths if path.startswith("/v1")}


def _versions(response: Any) -> dict[str, Any]:  # noqa: ANN401 — httpx.Response
    body = response.json()
    versions: dict[str, Any] = body["meta"]["versions"]
    return versions


# ── 실패 응답이 자기 것을 단다 ─────────────────────────────────────


#: 접두 → 그 라우터의 **실패를 내는 대표 요청** `(메서드, 경로, 바디)`.
#:
#: 🔴 **이 매핑은 목록이지만 「빠뜨림」은 검출된다** — 아래 테스트가 **등록된 접두 전수**를
#: 돌면서 매핑에 없는 접두를 **red**로 만든다. 그게 목록형과 전칭의 실제 차이다: 목록은
#: 남지만 **조용히 빠지지는 않는다**(로그 67 — 전칭이 불가능하면 빠뜨림을 검출한다).
#:
#: ⚠ **`RouterScope`에 메서드·바디를 넣지 않는다** — 계약을 테스트 편의로 오염시킨다.
#: 경로마다 실패를 내는 법이 다른 것은 **테스트의 문제**이지 계약의 문제가 아니다.
_FAILURE_REQUESTS: Final[dict[str, tuple[str, str, dict[str, Any] | None]]] = {
    "/v1/detect": ("POST", "/v1/detect", {"bad": 1}),
    "/v1/classify": ("POST", "/v1/classify", {"bad": 1}),
    "/v1/counsel": ("GET", f"/v1/counsel/drafts/{_ABSENT_JOB}", None),
    "/v1/imports": ("POST", "/v1/imports", {"bad": 1}),
    "/v1/confirmations": ("POST", "/v1/confirmations", {"bad": 1}),
    "/v1/problems": ("POST", "/v1/problems", {"bad": 1}),
    "/v1/diagnosis": ("POST", "/v1/diagnosis", {"bad": 1}),
}


def test_every_registered_prefix_has_a_failure_request() -> None:
    """🔴 **등록된 접두 전수**가 위 매핑에 있다 — 빠뜨리면 red다.

    새 라우터가 붙으면 *"실패 응답이 자기 engine을 단다"* 검사가 **그 경로엔 안 돌던**
    것이 종전 형태였다(5개 하드코딩). 이 단정이 그 침묵을 없앤다.
    """
    prefixes = {scope.prefix for scope in ROUTER_VERSION_SCOPES}
    assert prefixes, "ROUTER_VERSION_SCOPES가 비었다 — 검사가 끊긴 것이다"
    missing = sorted(prefixes - set(_FAILURE_REQUESTS))
    assert not missing, (
        f"실패 요청 매핑이 없는 접두: {missing}\n"
        "그 경로는 '실패 응답이 자기 engine을 단다' 검사를 **안 받는다**(99 ㊓). "
        "_FAILURE_REQUESTS에 (메서드, 경로, 실패를 내는 바디)를 추가하라 — "
        "대개 `{\"bad\": 1}` 같은 스키마 위반 바디면 400이 난다."
    )


@pytest.mark.parametrize(
    "scope", ROUTER_VERSION_SCOPES, ids=lambda s: s.prefix.replace("/", "_")
)
def test_failure_response_carries_its_own_engine_version(scope: Any) -> None:  # noqa: ANN401
    """🔴 각 엔드포인트의 실패 응답이 **자기 스코프가 말하는 engine**을 단다.

    ⚠ **기대값을 문자열로 박지 않는다** — `scope.versions().engine_version`에서 가져온다.
    박으면 또 목록형이고, `confirmations`처럼 **접두와 engine이 다른** 경우
    (`/v1/confirmations` → `classify-0.1` · 의도다)를 손으로 관리하게 된다.
    """
    method, path, body = _FAILURE_REQUESTS[scope.prefix]
    expected = scope.versions().engine_version

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        response = client.request(
            method,
            path,
            json=body,
            headers={**_HEADERS, "Idempotency-Key": f"k{scope.prefix}"},
        )

    assert response.status_code >= 400, (
        f"{scope.prefix}가 실패 응답을 안 냈다: {response.status_code} — "
        "_FAILURE_REQUESTS의 바디가 더 이상 스키마를 위반하지 않는 것일 수 있다"
    )
    engine = _versions(response)["engine"]
    assert engine == expected, (
        f"{scope.prefix}의 실패 응답이 engine={engine!r}인데 그 스코프는 {expected!r}를 "
        "말한다 — app.py의 예외 핸들러가 남의 엔드포인트 버전을 단다(99 ㊓)"
    )


def test_success_and_failure_agree_on_versions() -> None:
    """🔴 **㊓가 말하는 것** — 성공은 자기 것을 다는데 실패만 남의 것을 단다.

    같은 엔드포인트의 두 응답이 다른 버전을 말하면, BE가 어느 쪽을 믿어야 하는지 알 수
    없다. counsel로 대조한다(성공 202 · 실패 404가 둘 다 쉽게 난다).
    """
    router_test = _golden("tests/ai/integration/test_counsel_router.py")

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        ok = client.post(
            "/v1/counsel/drafts",
            json=router_test._REQUEST,
            headers={**_HEADERS, "Idempotency-Key": "k-ok"},
        )
        bad = client.get(f"/v1/counsel/drafts/{_ABSENT_JOB}", headers=_HEADERS)

    assert ok.status_code == 202 and bad.status_code == 404
    assert _versions(ok) == _versions(bad), (
        f"성공 {_versions(ok)}\n실패 {_versions(bad)}\n"
        "같은 엔드포인트인데 성공과 실패가 다른 버전을 말한다(99 ㊓)"
    )


def test_counsel_failure_does_not_claim_a_threshold_sheet() -> None:
    """⚠ **더 나쁜 쪽** — counsel은 임계 시트를 쓰지도 않는데 `threshold`가 실렸다.

    `engine`이 틀린 것은 *"어느 엔진인지 헷갈린다"* 이지만, `threshold=default-v3`는
    *"그 실행이 임계 v3를 썼다"* 는 **없는 사실**을 만든다. 04 §2.2가 `threshold`를
    *"감지 임계값 시트 버전이라 detection 외에는 null"* 로 규정했다.
    """
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        bad = client.get(f"/v1/counsel/drafts/{_ABSENT_JOB}", headers=_HEADERS)

    assert _versions(bad)["threshold"] is None, (
        "counsel 실패 응답이 threshold를 달았다 — 그 엔드포인트는 임계 시트를 쓰지 않는다"
    )


def test_unhandled_500_also_uses_the_request_path() -> None:
    """🔴 미분류 500 경로(`_unhandled`)도 같은 규율을 탄다 — **더 빠지기 쉬운 쪽**이다.

    `_domain_handler`만 고치고 `_unhandled`를 두면 *"장애일 때만 남의 버전"* 이 된다 —
    하필 가장 급할 때 원장이 엉뚱한 엔진을 가리킨다.
    """
    from ai.api.routers import counsel as counsel_router

    app = create_app()

    def _boom() -> None:
        raise RuntimeError("의도적 미분류 예외")

    original = counsel_router._build_supervisor  # noqa: SLF001
    with TestClient(app, raise_server_exceptions=False) as client:
        counsel_router._build_supervisor = _boom  # type: ignore[assignment]
        try:
            response = client.post(
                "/v1/counsel/drafts",
                json=_golden("tests/ai/integration/test_counsel_router.py")._REQUEST,
                headers={**_HEADERS, "Idempotency-Key": "k-boom"},
            )
        finally:
            counsel_router._build_supervisor = original  # noqa: SLF001

    assert response.status_code == 500
    assert _versions(response)["engine"].startswith("counsel-pack-"), (
        f"미분류 500이 engine={_versions(response)['engine']!r}을 단다 — "
        "_unhandled도 요청 경로로 versions를 골라야 한다(99 ㊓)"
    )


# ── 검사 경로 절단 ────────────────────────────────────────────────


def test_the_app_registers_the_routers_this_file_checks() -> None:
    """⚠ 위 표의 엔드포인트가 **실제로 등록돼 있는지** 먼저 본다.

    라우터 등록이 빠지면 위 테스트들이 404를 받고 *"실패 응답이 나왔다"* 로 통과할 수
    있다 — 조용한 통과를 막는다.
    """
    paths = registered_v1_paths(create_app())
    for expected in (
        "/v1/detect",
        "/v1/classify",
        "/v1/imports",
        "/v1/confirmations",
        "/v1/counsel/drafts",
    ):
        assert any(p.startswith(expected) for p in paths), f"{expected}가 등록돼 있지 않다"
