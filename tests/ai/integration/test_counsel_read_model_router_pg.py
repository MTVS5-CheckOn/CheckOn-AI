"""캐시가 비어도 **라우터가 PG에서 살려내는가** — ㉿ 네 증상의 실측 축.

🔴 **저장소를 만든 것으로는 아무것도 안 고쳐진다.** `PgCounselDraftViewStore`가 있어도
`_view_cache`·`_drafts`가 여전히 **유일한 정본**이면 ㉿는 그대로다(99 #22가 등재한 형태 —
정의·마이그레이션까지 있는데 **프로덕션 소비가 0**). 이 파일이 그 **호출자 0**을 red로 만든다.

**캐시를 비우는 것이 재시작의 대역이다** — 프로세스 공용 LRU라 비우면 남는 것은 PG뿐이다.
축출(㉿ ⓐ)과 재시작(ⓒ)은 캐시 관점에서 **같은 상태**이므로 한 번에 본다.

🔴 **읽기 모델 저장소만 PG로 주입한다 — 앱 전체를 `STORE_BACKEND=pg`로 돌리지 않는다.**
전면 플립을 시도했더니 **이 축과 무관한 FK 순서**에서 죽었다(실측):
`agent_run.run_id → ai_run` 위반 — 잡 행이 실행 원장 행보다 먼저 들어간다.
그건 **플립 점검표의 안건**이고 이 PR의 축이 아니다. 섞으면 read-model이 살아났는지
아닌지를 **그 실패가 가린다.** ⇒ 다른 저장소는 memory로 두고 **한 축만 바꾼다.**
⚠ 팩토리가 `store_backend`로 고르는지는 별도 단위 검사가 본다.

PG가 없으면 skip이고, 그때 이 파일은 **아무것도 안 본 것**이다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any, Final

import httpx
import pytest
from counsel_text import draft
from fastapi.testclient import TestClient
from pg_hint import PG_UNAVAILABLE
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ai.api.app import create_app
from ai.api.routers import counsel as counsel_router
from ai.api.routers.counsel import (
    reset_counsel_stores,
    set_counsel_draft_view_store,
    set_counsel_provider,
)
from ai.composition.counsel.provider import FakeCounselProvider
from ai.contracts.counsel import Citation
from ai.db.counsel_read_model import PgCounselDraftViewStore
from ai.db.settings import get_db_settings
from ai.db.store_factory import reset_shared_agent_runtime

pytestmark = pytest.mark.integration

_TENANT: Final = "t_readmodel_router"
_HEADERS: Final = {
    "X-Tenant-Id": _TENANT,
    "X-Request-Id": "rq-readmodel-1",
    "Idempotency-Key": f"{_TENANT}:counsel:1",
}


async def _query(statement: str, **params: object) -> object:
    """🔴 **async 드라이버로만 붙는다** — 동기 드라이버는 이 저장소에 설치돼 있지 않고,
    그걸 쓰면 이 파일 전체가 **조용히 skip**된다(실측: 6건 skip).
    """
    engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            return (await conn.execute(text(statement), params)).scalar_one()
    finally:
        await engine.dispose()


def _pg_available() -> bool:
    try:
        asyncio.run(_query("SELECT 1"))
    except Exception:  # noqa: BLE001 — 접속 실패 종류를 가리지 않는다
        return False
    return True


def _truncate() -> None:
    asyncio.run(
        _query(
            "WITH x AS (DELETE FROM counsel_draft_view WHERE tenant_id = :t "
            "RETURNING 1) SELECT count(*) FROM x",
            t=_TENANT,
        )
    )


@pytest.fixture
def pg_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    if not _pg_available():
        pytest.skip(PG_UNAVAILABLE)
    del monkeypatch
    reset_shared_agent_runtime()
    reset_counsel_stores()
    #: 🔴 **테스트 전용 엔진이다** — 공용 `get_sessionmaker()`는 처음 쓴 이벤트 루프에 묶이고
    #: `TestClient`는 테스트마다 새 루프를 연다. 공용을 쓰면 두 번째 테스트부터
    #: `attached to a different loop`로 죽는다(실측). `NullPool`로 접속도 안 물고 있는다.
    engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
    set_counsel_draft_view_store(
        PgCounselDraftViewStore(async_sessionmaker(engine, expire_on_commit=False))
    )
    _truncate()
    try:
        with TestClient(create_app()) as client:
            yield client
    finally:
        _truncate()
        asyncio.run(engine.dispose())
        reset_shared_agent_runtime()
        reset_counsel_stores()


def _request_body() -> dict[str, Any]:
    """🔴 **계약 §4-① 예시를 복제하지 않는다** — 기존 통합 테스트가 든 정본을 재사용한다.

    ⚠ 평면 import다(`tests/ai/integration`은 패키지가 아니다 · `pythonpath` 규약).
    """
    from test_counsel_router import _REQUEST  # noqa: PLC0415

    return dict(_REQUEST)


def _post(client: TestClient) -> str:
    response: httpx.Response = client.post(
        "/v1/counsel/drafts", json=_request_body(), headers=_HEADERS
    )
    assert response.status_code == 202, response.text
    job_id: str = response.json()["data"]["job_id"]
    return job_id


def _assert_citations_match(
    payload: object, restored: tuple[Citation, ...]
) -> None:
    """응답의 인용과 **복원한 초안의 인용**을 값으로 대조한다.

    🔴 **개수만 보면 다른 초안을 되살려도 통과한다** — `cite_id`·`record_id`·`summary`가
    전부 같아야 한다(evidence는 불변식 2의 축이다).
    """
    expected = [citation.model_dump(mode="json") for citation in restored]
    assert payload == expected, (
        f"응답의 인용이 복원한 초안과 다르다 — 다른 초안을 되살렸을 수 있다: "
        f"{payload!r} != {expected!r}"
    )


def _forget_caches() -> None:
    """재시작·축출의 대역 — 프로세스 캐시만 비운다. **PG는 그대로 둔다.**"""
    counsel_router._view_cache.clear()
    counsel_router._drafts.clear()


def test_the_read_model_store_under_test_is_the_pg_one(pg_client: TestClient) -> None:
    """🔴 절단 가드 — Null 구현이 꽂혀 있으면 이 파일 전체가 아무것도 안 본다."""
    assert isinstance(
        counsel_router._draft_view_store, PgCounselDraftViewStore
    ), "읽기 모델 저장소가 PG가 아니다 — 이 파일은 캐시만 재고 있다"
    del pg_client


def test_the_row_is_written_on_post(pg_client: TestClient) -> None:
    """🔴 호출자 0 가드 — POST가 실제 `counsel_draft_view` 행을 남겨야 한다.

    ⚠ **「행이 있다」로는 부족하다** — 같은 행을 **초안 저장이 대신 만든다.** 뷰 쓰기를
    끊고 돌려 봤더니 초안 쪽이 행을 만들어 `count == 1`이 **그대로 통과**했다(실측).
    ⇒ **`view_snapshot`이 실제로 찼는지**를 센다. 검사의 이름이 보는 것보다 넓었다(로그 85).
    """
    job_id = _post(pg_client)
    count = asyncio.run(
        _query(
            "SELECT count(*) FROM counsel_draft_view "
            "WHERE tenant_id = :t AND job_id = :j AND view_snapshot IS NOT NULL",
            t=_TENANT,
            j=job_id,
        )
    )
    assert count == 1, "POST가 뷰 스냅숏을 안 남겼다 — 저장소 호출자가 0이다"


def test_get_survives_an_emptied_cache(pg_client: TestClient) -> None:
    """㉿ ⓐ·ⓒ — 축출/재시작 뒤에도 GET이 404가 아니어야 한다."""
    job_id = _post(pg_client)
    assert pg_client.get(
        f"/v1/counsel/drafts/{job_id}", headers=_HEADERS
    ).status_code == 200

    _forget_caches()
    revived = pg_client.get(f"/v1/counsel/drafts/{job_id}", headers=_HEADERS)
    assert revived.status_code == 200, (
        f"캐시를 비우니 GET이 죽는다({revived.status_code}) — 캐시가 아직 정본이다"
    )
    assert revived.json()["data"]["job_id"] == job_id


def test_another_tenant_still_gets_404(pg_client: TestClient) -> None:
    """🔴 복원 경로가 **테넌트 격리를 뚫으면 안 된다** — 존재 은닉 그대로."""
    job_id = _post(pg_client)
    _forget_caches()
    headers = {**_HEADERS, "X-Tenant-Id": "t_intruder"}
    assert pg_client.get(
        f"/v1/counsel/drafts/{job_id}", headers=headers
    ).status_code == 404


def _refine(client: TestClient, job_id: str, *, turn: int = 1) -> httpx.Response:
    response: httpx.Response = client.post(
        f"/v1/counsel/drafts/{job_id}/refine",
        json={"instruction": "조금 더 부드럽게 써줘", "turn_no": turn},
        headers={**_HEADERS, "Idempotency-Key": f"{_TENANT}:refine:{turn}"},
    )
    return response


def test_refine_survives_an_emptied_cache(pg_client: TestClient) -> None:
    """㉿ ⓑ — GET과 refine이 **같은 축**으로 살아나야 한다(두 캐시가 독립 축출이었다).

    🔴 **종전 단정은 `status_code != 404`였고 그건 거짓 green이었다.**
    실측 판정표: `200 통과 · 422 통과 · 500 통과 · 503 통과`. **404만 아니면 다 성공**이라
    *"복원 뒤 refine이 터진다"* 를 **그대로 통과**시켰다 — **검사 이름이 「살아난다」인데
    보는 것은 「404가 아니다」**였다(로그 85 계열).

    ⇒ **성공 응답까지 본다** — 200 + 계약대로의 `applied`/`blocked_reason`,
    그리고 **복원된 초안의 인용이 그대로 실렸는지**(다른 초안을 되살린 것이 아니다).
    """
    job_id = _post(pg_client)
    _forget_caches()
    response = _refine(pg_client, job_id)

    assert response.status_code == 200, (
        f"복원 뒤 refine이 {response.status_code}다 — 404가 아니라고 성공이 아니다: "
        f"{response.text[:400]}"
    )
    data = response.json()["data"]
    #: 🔴 **차단도 200이다**(불변식 4) — 두 갈래를 계약대로 가른다.
    assert set(data) <= {"applied", "text", "citations", "blocked_reason"}, data
    if data["applied"]:
        assert (data.get("text") or "").strip(), "반영인데 본문이 없다"
        assert data.get("citations"), "반영 턴에도 근거가 1건 이상이어야 한다(불변식 2)"
    else:
        assert data.get("blocked_reason"), "차단인데 사유가 없다(사유 없는 거부 금지)"

    #: 🔴 **복원한 「그」 초안인가** — 개수만 보면 **다른 초안을 되살려도 통과한다.**
    #: `cite_id`·`record_id`·`summary`까지 **값으로** 대조한다(evidence는 불변식 2의 축이다).
    restored = counsel_router._drafts.get((_TENANT, job_id))
    assert restored is not None, "refine이 캐시를 안 채웠다 — 복원 경로를 안 탔다"
    if data["applied"]:
        _assert_citations_match(data["citations"], restored.citations)


@pytest.mark.parametrize("field", ["cite_id", "record_id", "summary"])
def test_a_same_sized_but_different_citation_is_caught(
    pg_client: TestClient, field: str
) -> None:
    """🔴 **개수 대조의 미탐을 실측한다 — 같은 단정 함수를 태워서.**

    ⚠ *"같은 개수인데 값이 다르면 red다"* 를 **손으로 다시 적으면 동어반복**이다.
    본 검사가 쓰는 `_assert_citations_match`를 **그대로 불러** 잡히는지 본다.
    ⚠ 종전 `len(...) == len(...)`은 이 입력을 **통과시킨다** — 그 사실도 함께 단정한다.
    """
    job_id = _post(pg_client)
    _forget_caches()
    assert _refine(pg_client, job_id).status_code == 200

    restored = counsel_router._drafts.get((_TENANT, job_id))
    assert restored is not None and restored.citations, "대조할 인용이 없다"
    original = [c.model_dump(mode="json") for c in restored.citations]
    tampered = [{**original[0], field: f"{original[0][field]}-다른값"}, *original[1:]]

    #: 🔴 **개수는 같다** — 종전 단정은 여기서 green이다.
    assert len(tampered) == len(restored.citations)
    with pytest.raises(AssertionError):
        _assert_citations_match(tampered, restored.citations)

    #: 뒤집기의 뒤집기 — 안 건드린 값은 통과해야 한다(단정이 늘 터지는 것이 아니다).
    _assert_citations_match(original, restored.citations)


def test_a_broken_refine_is_not_reported_as_survival(pg_client: TestClient) -> None:
    """🔴 **뒤집기 — 복원은 됐는데 refine 자체가 터지면 red여야 한다.**

    ⚠ 종전 단정(`!= 404`)은 **이 상태를 green으로** 받았다. 여기서 그 사실을 실측한다.
    """
    job_id = _post(pg_client)
    _forget_caches()

    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("refine 내부 실패(대역)")

    #: ⚠ `setattr`/`getattr`로 간다 — 모듈이 재수출하지 않는 이름이라 정적으로는 안 보인다.
    original = getattr(counsel_router, "refine_draft")  # noqa: B009
    setattr(counsel_router, "refine_draft", explode)  # noqa: B010
    try:
        #: ⚠ **`raise_server_exceptions=False`** — 기본 `TestClient`는 핸들러 밖 예외를
        #: 되던져서 **실서버가 실제로 내는 응답**을 못 본다. 여기서 재려는 것은
        #: *"그 상태를 종전 단정이 통과시켰는가"* 이므로 **응답 코드**를 봐야 한다.
        with TestClient(create_app(), raise_server_exceptions=False) as raw:
            response = _refine(raw, job_id, turn=2)
    finally:
        setattr(counsel_router, "refine_draft", original)  # noqa: B010

    assert response.status_code >= 500, (
        f"refine이 터졌는데 {response.status_code}다 — 대역이 안 걸렸다"
    )
    #: 🔴 **종전 단정(`!= 404`)은 이 응답을 green으로 받는다** — 그게 이 보완의 이유다.
    assert response.status_code != 404, (
        "이 상태에서 종전 단정은 통과한다 — 그래서 「살아난다」가 거짓이 될 수 있었다"
    )


def test_an_unknown_job_is_still_404(pg_client: TestClient) -> None:
    """복원 경로가 **없는 잡을 만들어 내면 안 된다**."""
    assert pg_client.get(
        "/v1/counsel/drafts/job-does-not-exist", headers=_HEADERS
    ).status_code == 404


# ══ refine 반영분이 **두 저장소 모두에** 남는가 (99 #74) ══
#
# 🔴 **반영본이 사는 곳이 둘이다** — `_drafts`(다음 refine 턴의 입력)와 `_view_cache`(GET의
# 뷰). 종전에는 `state.text = ...` in-place 변이 한 줄이라 **어느 쪽에도 영속이 없었다.**
# ⚠ 두 축을 한 검사로 묶지 마라 — 하나만 고치고 닫았는지를 못 가른다(결정 로그 96 부류).


#: 🔴 **최초 생성과 다듬기 결과를 다른 문면으로 못박는다.**
#: ⚠ 기본 Fake는 refine 턴에도 **같은 문면**을 돌려준다(2026-08-19 실측) — 그러면
#:   *"뷰가 반영본을 들었나"* 가 **동어반복**이 되어 뷰 갱신을 지워도 green이다.
#:   두 축(초안·뷰) 모두 이 구분이 없으면 눈이 먼다.
#: ⚠ 숫자·새 사실을 넣지 않는다 — 넣으면 refine 게이트가 차단해서 축이 바뀐다.
_FIRST_DRAFT: Final = draft("이번 기간 학습 상황을 정리해 보내드립니다.")
_REFINED_DRAFT: Final = draft("이번 기간 학습 상황을 조금 더 부드럽게 정리해 보내드립니다.")


def _script_two_drafts() -> None:
    """🔴 라우터 provider를 **문면 둘짜리 Fake**로 꽂는다 — 실 LLM 0회.

    ⚠ 저장소는 안 건드린다(그건 *"실제로 일어날 수 있는가"* 를 못 재게 한다).
    바꾸는 것은 **LLM 대역**뿐이고, 그건 이 저장소의 결정론 규약 그대로다.
    """
    set_counsel_provider(
        FakeCounselProvider(drafts=[_FIRST_DRAFT, _REFINED_DRAFT, _REFINED_DRAFT])
    )


def _refined_text(client: TestClient, job_id: str) -> str:
    """반영된 refine 한 턴 — 차단이면 이 축을 못 재므로 skip한다."""
    response = _refine(client, job_id)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    if not data["applied"]:
        pytest.skip(f"이 회차 refine이 차단됐다 — 반영 축을 못 잰다: {data}")
    text: str = data["text"]
    assert text.strip()
    return text


def test_the_refined_body_survives_an_emptied_cache(pg_client: TestClient) -> None:
    """🔴 **축 ① 초안 원장** — 반영분이 PG에 남아 다음 턴의 입력이 된다 (99 #74).

    캐시를 비우는 것이 **재시작·축출의 대역**이다(이 파일 머리말). 종전에는 refine 성공
    경로가 `_remember_draft`를 안 지나 **여기서 최초 초안으로 되돌아갔다** — 강사가 쌓은
    누적이 통째로 사라진다.

    ⚠ **GET을 보지 않는다** — GET은 뷰를 읽지 초안을 읽지 않는다. 이 검사가 뷰를 보면
    축 ②와 겹쳐서 「저장소가 둘」이라는 사실을 못 재게 된다.
    """
    _script_two_drafts()
    job_id = _post(pg_client)
    refined = _refined_text(pg_client, job_id)

    _forget_caches()
    restored = asyncio.run(counsel_router._draft_state_of((_TENANT, job_id)))

    assert restored is not None, "캐시를 비우니 초안이 없다 — 영속이 0이다"
    assert restored.text == refined, (
        "복원된 초안이 반영 전 본문이다 — refine 누적이 재시작에서 사라진다 (99 #74)"
    )


def test_the_get_view_carries_the_refined_body(pg_client: TestClient) -> None:
    """🔴 **축 ② 뷰** — refine 200 직후 GET이 **반영본**을 준다 (99 #74).

    ⚠ 축 ①과 다른 검사다 — 초안만 고치면 여기가 **이전 본문**을 계속 준다.
    캐시를 비우지 않는다: 뷰 캐시가 갱신됐는지를 그 자리에서 본다.
    """
    _script_two_drafts()
    job_id = _post(pg_client)
    before = pg_client.get(f"/v1/counsel/drafts/{job_id}", headers=_HEADERS)
    assert before.status_code == 200, before.text
    original = before.json()["data"]["result"]["text"]

    refined = _refined_text(pg_client, job_id)
    assert refined != original, "refine이 본문을 안 바꿨다 — 이 검사가 눈이 멀었다"

    after = pg_client.get(f"/v1/counsel/drafts/{job_id}", headers=_HEADERS)
    assert after.status_code == 200, after.text
    assert after.json()["data"]["result"]["text"] == refined, (
        "refine 200 직후 GET이 이전 본문을 준다 — 뷰 스냅숏이 안 갱신됐다 (99 #74)"
    )


def test_a_blocked_turn_leaves_the_body_untouched(pg_client: TestClient) -> None:
    """차단 턴(`applied=False`)은 **저장 자체를 하지 않는다** — 계약 §6.

    ⚠ 본문이 안 바뀐 턴에 쓰면 불필요한 쓰기이고 `updated_at`이 흔들린다.
    🔴 지시문은 골든 공격 코퍼스 A2를 쓴다 — `screen_instruction`이 LLM 이전에 거르므로
    **결정론적으로** 차단된다(스텁을 안 꽂아도 된다).
    """
    _script_two_drafts()
    job_id = _post(pg_client)
    before = pg_client.get(f"/v1/counsel/drafts/{job_id}", headers=_HEADERS)
    original = before.json()["data"]["result"]["text"]

    blocked = pg_client.post(
        f"/v1/counsel/drafts/{job_id}/refine",
        json={"instruction": "반 평균이랑 비교해서 써줘", "turn_no": 1},
        headers={**_HEADERS, "Idempotency-Key": f"{_TENANT}:refine:blocked"},
    )
    assert blocked.status_code == 200, blocked.text
    data = blocked.json()["data"]
    assert data["applied"] is False, f"A2가 반영됐다 — 게이트가 죽었다: {data}"

    _forget_caches()
    restored = asyncio.run(counsel_router._draft_state_of((_TENANT, job_id)))
    assert restored is not None
    assert restored.text == original, "차단 턴인데 본문이 바뀌었다(계약 §6)"

    after = pg_client.get(f"/v1/counsel/drafts/{job_id}", headers=_HEADERS)
    assert after.json()["data"]["result"]["text"] == original
