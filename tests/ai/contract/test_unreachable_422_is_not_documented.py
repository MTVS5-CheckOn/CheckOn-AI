"""🔴 도달 불가 `422` 는 문서에 없고, **도달하는 `422` 는 남아 있다** (99 #105).

⚠ **한 방향만 재면 안 된다.** «`str` 경로에 422 가 없다» 만 잰 검사는 **전부 떼는 후처리**도
통과시킨다 — 그러면 `slot_index: int` 경로에서 **실제로 오는 응답**이 문서에서 사라지고,
BE 는 그 422 를 못 받는 것으로 짠다. ⇒ 🔴 **앵커 폭은 반대 방향이 증명한다**
(№51 §B ③ · №52 §4-2 가 세운 형태).

🔴 **왜 이 결손이 생겼나**: FastAPI 는 path 파라미터가 있으면 `422` 를 **자동 주입**한다.
`job_id: str` 은 **어떤 값도 유효**해서 검증이 실패할 수 없는데도 문서에 남아, 승우님이
codegen 하면 **못 오는 핸들러**를 만든다(배포 openapi 로 확인 · 99 #105).

⚠ 🔴 **경로를 하드코딩한 것은 여기가 유일하고, 그게 이 파일의 일이다** — 후처리는 **규칙**이고
검사는 «지금 이 경로가 이래야 한다» 를 문다. 🔴 **다만 새 경로가 생기면 이 목록은 안 는다** —
그때 이 파일을 같이 고쳐야 하고, 안 고치면 **새 경로는 아무도 안 잰다**.
"""

from __future__ import annotations

from typing import Any, Final

import pytest

from ai.api.app import create_app

#: path 파라미터가 **전부 `str`** — 검증이 실패할 수 없다 ⇒ `422` 는 도달 불가다.
_STR_ONLY_OPERATIONS: Final = (
    ("/v1/counsel/drafts/{job_id}", "get"),
    ("/v1/counsel/drafts/{job_id}/refine", "post"),
    ("/v1/imports/{job_id}", "get"),
    ("/v1/imports/{job_id}/confirm", "post"),
    ("/v1/problems/{job_id}", "get"),
    ("/v1/problems/{set_id}/items", "get"),
)

#: 🔴 `slot_index: int` — **422 가 실제로 온다.** 여기서 사라지면 BE 가 못 받는 것으로 짠다.
_REACHABLE_422_OPERATIONS: Final = (
    ("/v1/problems/{set_id}/items/{slot_index}", "get"),
    ("/v1/problems/{set_id}/items/{slot_index}/revisions", "post"),
)


@pytest.fixture(scope="module")
def document() -> dict[str, Any]:
    return create_app().openapi()


@pytest.mark.parametrize(("path", "method"), _STR_ONLY_OPERATIONS)
def test_an_unreachable_validation_error_is_not_documented(
    path: str, method: str, document: dict[str, Any]
) -> None:
    """① path 파라미터가 전부 `str` 이면 `422` 가 문서에 **없다**."""
    responses = document["paths"][path][method]["responses"]
    assert "422" not in responses, (
        f"{method.upper()} {path}: 도달 불가 `422` 가 문서에 남아 있다 — "
        f"BE codegen 이 **못 오는 핸들러**를 만든다(99 #105). "
        f"후처리(`api/app.py::_install_unreachable_422_removal`)가 이 경로를 못 봤다"
    )


@pytest.mark.parametrize(("path", "method"), _REACHABLE_422_OPERATIONS)
def test_a_reachable_validation_error_is_still_documented(
    path: str, method: str, document: dict[str, Any]
) -> None:
    """② `slot_index: int` 경로에서는 `422` 가 **남아 있다**.

    🔴 **이 검사가 앵커 폭이다.** 없으면 «전부 떼는 후처리» 도 ①을 통과하고, 그때
    **실제로 오는 응답**이 문서에서 사라진다.
    """
    responses = document["paths"][path][method]["responses"]
    assert "422" in responses, (
        f"{method.upper()} {path}: **도달하는** `422` 가 문서에서 사라졌다 — "
        f"`slot_index: int` 라 잘못된 값이 오면 FastAPI 가 실제로 422 를 낸다. "
        f"후처리 조건이 «path 파라미터가 전부 `str`» 보다 넓어졌다"
    )


def test_the_post_processing_actually_saw_some_routes() -> None:
    """③ 후처리가 라우트를 **하나라도** 찾았다 — 0건이면 조용히 통과한다.

    ⚠ 라우트 순회는 FastAPI 내부 구조(`_IncludedRouter.original_router`)에 기댄다.
    🔴 버전이 바뀌어 순회가 깨지면 `_api_routes` 가 **빈 이터레이터**가 되고, 후처리는
    아무것도 안 하면서 **예외도 안 낸다.** ①이 그때 red 가 되지만, 원인이 «후처리 버그» 인지
    «경로가 바뀐 것» 인지 갈리지 않는다 — 그 둘을 갈라 두는 자리다.
    """
    from ai.api.app import _api_routes  # noqa: PLC0415 — 내부 헬퍼를 의도적으로 연다

    routes = list(_api_routes(create_app().routes))
    assert routes, (
        "`_api_routes` 가 APIRoute 를 하나도 못 찾았다 — FastAPI 라우트 구조가 바뀌었다. "
        "후처리가 **조용히 아무것도 안 하는** 상태다"
    )
    assert any(route.dependant.path_params for route in routes), (
        "path 파라미터가 있는 라우트를 하나도 못 찾았다 — 순회가 반쪽이다"
    )
