"""**기본 설정만으로 상담 경로가 PG 정본 위에서 도는가** (99 #39 · 기본값 플립).

🔴 **이 파일은 저장소를 하나도 주입하지 않는다.** 형제 파일
`test_counsel_read_model_router_pg`는 읽기 모델 **한 축만** PG로 꽂고 나머지는 memory로
뒀다 — 그 파일 docstring이 이유를 적는다: *"전면 플립을 시도했더니 이 축과 무관한 FK 순서
(`agent_run.run_id → ai_run`)에서 죽었다."* **그 FK는 G1(0009)에서 사라졌다.** 그래서
이번에는 **환경 변수를 지운 진짜 기본 설정**으로 앱을 세우고, 무엇이 실제로 서는지 잰다.

**「인스턴스」의 정의** — 새 프로세스가 갖는 것과 갖지 않는 것을 그대로 흉내 낸다:
프로세스 캐시(`_view_cache`·`_drafts`)는 **비어 있고**, 인메모리 저장소는 **새 객체**이며,
엔진도 **새로 만든다**(`get_engine`은 `lru_cache`라 안 비우면 앞 인스턴스의 루프에 묶인
커넥션을 물고 와 `attached to a different loop`로 죽는다 — 실측). **PG만 공유한다.**
⚠ 프로덕션 코드에 시험용 갈고리를 넣지 않았다 — 전부 기존 리셋 함수와 캐시 무효화다.

⚠ **한 인스턴스로 두 번 재지 않는다** — 캐시만 비우면 인메모리 저장소가 그대로 남아
**같은 프로세스를 두 번 재는 것**이 된다(형제 파일의 `_forget_caches`와 다른 축이다).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Final

import httpx
import pytest
from anyio.from_thread import BlockingPortal
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from ai.agents.checkpointer import main as checkpointer_main
from ai.agents.checkpointer import run_with_checkpoint_loop, setup_checkpointer_schema
from ai.api.app import create_app
from ai.api.routers import classify as classify_router
from ai.api.routers import confirmations as confirmations_router
from ai.api.routers import counsel as counsel_router
from ai.api.routers.counsel import reset_counsel_stores
from ai.composition.counsel.stores import (
    InMemoryContextStore,
    InMemoryDraftResultStore,
)
from ai.db.counsel_read_model import PgCounselDraftViewStore
from ai.db.models import Base
from ai.db.repositories.counsel_context_store import PgContextStore
from ai.db.repositories.counsel_draft_store import PgDraftResultStore
from ai.db.repositories.counsel_step_store import PgCounselAgentStepSink
from ai.db.repositories.idempotency import PgIdempotencyStore
from ai.db.repositories.inquiry_class_store import PgInquiryClassStore
from ai.db.session import get_engine
from ai.db.settings import get_db_settings
from ai.db.store_factory import (
    build_idempotency_store,
    reset_default_inquiry_class_store,
    reset_shared_agent_runtime,
)

pytestmark = pytest.mark.integration

_TENANTS: Final = (
    "t_flip_idem",
    "t_flip_multi",
    "t_flip_late",
    "t_flip_class",
    "t_flip_guard",
)
_INTRUDER: Final = "t_flip_intruder"


#: 🔴 **손으로 옮긴 계약 기대값**(지시서 73-R §1·§2) — `_REQUEST`의 근거 두 줄에서 나온다.
#:
#: ⚠ **`citations_of_context()`를 불러 기대값을 만들지 않는다.** 그러면 생산 함수가
#: 틀려도 기대값이 같이 틀려 **동어반복**이 된다 — 순서·매핑이 뒤바뀌어도 초록이다.
#: 종전 검사는 *"비어 있지 않다"* 만 봤고, 그건 아래 넷을 전부 통과시켰다:
#: cite_id 순서 뒤바뀜 · record_id가 남의 근거 · summary가 다른 문면 · 두 인용이 서로 바뀜.
_EXPECTED_CITATIONS: Final = (
    ("L1", "le_2041", "6월 지문 42개·312문항"),
    ("L2", "le_2077", "제출률 100% (4주)"),
)

#: 🔴 **요청이 보낸 순서가 아니라 «적용 축» 순서다**(`labels.py::_AXIS_ENUMS` —
#: comm·sensitivity·interest·frequency). `_REQUEST["labels"]`는
#: `["narrative", "attitude", "anxious", "frequent"]`인데 응답은 `attitude`(interest)와
#: `anxious`(sensitivity)가 **자리를 바꾼다.**
#: ⚠ `snapshot_from_labels` docstring이 그 이유를 적는다 — *"요청이 보낸 것을 되돌려주는 게
#: 아니라 실제 적용분을 싣는다"*(05 §7-2: 기본값이 쓰였는지 화면이 알아야 한다).
#: 🔴 **집합으로 비교하지 않는다** — 배열 순서가 계약이자 재현성 축이다.
_EXPECTED_LABELS: Final = ["narrative", "anxious", "attitude", "frequent"]


def _cited(result: Mapping[str, Any]) -> tuple[tuple[str, str, str], ...]:
    """응답 `citations` → 대조용 튜플. **순서를 보존한다.**"""
    return tuple(
        (item["cite_id"], item["record_id"], item["summary"])
        for item in result["citations"]
    )


def _headers(tenant: str, *, suffix: str = "1") -> dict[str, str]:
    return {
        "X-Tenant-Id": tenant,
        "X-Request-Id": f"rq-flip-{suffix}",
        "Idempotency-Key": f"{tenant}:counsel:{suffix}",
    }


#: 🔴 **회차 사이에 살아남는 것을 전부 지운다** — 플립 뒤에는 프로세스가 죽어도 PG가 남는다.
#:
#: ⚠ **`counsel_draft_view`만 지웠더니 실측에서 이 파일이 두 번째 실행부터 404였다.**
#: 멱등 레코드는 살아 있어 POST가 **이전 회차의 `job_id`로 202**를 주는데, 그 잡의 뷰 행은
#: 방금 지워졌기 때문이다 — **㉿ ⓓ가 「멱등은 남고 본문은 사라진 비대칭」으로 다시 나타난 것**이고,
#: 여기서는 검사 위생 문제지만 **운영에서는 보존 기간이 갈리면 그대로 재현된다**(99 #39에 등재).
#: ⚠ **순서가 계약이다 — 자식부터 지운다**(FK). 실측으로 두 번 배웠다:
#:   ⓐ `agent_step`·`llm_call`·`llm_payload`에는 `tenant_id`가 **없다** → 부모를 타고 지운다
#:      (`column "tenant_id" does not exist`)
#:   ⓑ `llm_payload`가 `llm_call`을 참조하고 `inquiry_class`도 `llm_call`을 참조한다
#:      (`violates foreign key constraint "fk_llm_payload_call_id_llm_call"`)
#: ⚠ **손으로 짐작하지 않았다** — 두 오류가 목록을 하나씩 늘렸다.
_OF_TENANT: Final = "tenant_id = ANY(:t)"
_OF_RUN: Final = "run_id IN (SELECT execution_id FROM ai_run WHERE tenant_id = ANY(:t))"
_CLEANUP: Final[tuple[tuple[str, str], ...]] = (
    ("llm_payload", f"call_id IN (SELECT id FROM llm_call WHERE {_OF_RUN})"),
    ("agent_step", "agent_run_id IN (SELECT id FROM agent_run WHERE tenant_id = ANY(:t))"),
    ("inquiry_class", _OF_TENANT),
    ("llm_call", _OF_RUN),
    ("agent_run", _OF_TENANT),
    #: ⚠ **(㉻) `draft`가 `ai_run`을 참조한다** — 본문이 PG에 앉기 시작하면서 목록이
    #:   하나 더 늘었다(실측: `update or delete on table "ai_run" violates
    #:   fk_draft_run_id_ai_run`). 이 파일의 규율대로 **오류가 목록을 늘렸다.**
    ("draft", _OF_TENANT),
    #: ⚠ 입력 묶음은 참조받지 않지만 **회차마다 쌓인다** — 안 지우면 다음 회차의
    #:   행 수 단정이 흐려진다.
    ("counsel_context_bundle", _OF_TENANT),
    ("counsel_pack_result", _OF_TENANT),
    ("counsel_draft_view", _OF_TENANT),
    ("idempotency_record", _OF_TENANT),
    ("ai_run", _OF_TENANT),
)


async def _prepare_schema() -> None:
    #: 🔴 **LangGraph 체크포인트 테이블도 만든다** — 플립하면 counsel의 체크포인터가
    #: `AsyncPostgresSaver`가 되고, 그 테이블이 없으면 워커가
    #: `relation "checkpoints" does not exist` → `worker_internal_error`로 **잡을 실패**시킨다
    #: (실측: 앞선 통합 검사가 스키마를 내리면 이 파일이 그 상태를 밟았다).
    #: 🔴 **이건 검사 편의가 아니라 배포 선행 단계다** — `setup_checkpointer_schema()`는
    #: 저장소 전체에서 **호출처가 0건**이었다(99 #22 형태). 기본값이 `pg`가 된 지금은
    #: **그 함수를 안 부르면 새 DB에서 상담 잡이 전부 실패**한다 ⇒ #39·점검표에 적었다.
    await setup_checkpointer_schema()
    engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with engine.begin() as conn:
            for table, predicate in _CLEANUP:
                await conn.execute(
                    text(f"DELETE FROM {table} WHERE {predicate}"),  # noqa: S608
                    {"t": [*_TENANTS, _INTRUDER]},
                )
    finally:
        await engine.dispose()


def _looks_like_no_database(exc: BaseException) -> bool:
    """접속 자체가 안 되는가 — **그 외의 실패는 결함이지 부재가 아니다.**"""
    text_of = f"{type(exc).__name__}: {exc}".lower()
    return any(
        mark in text_of
        for mark in ("connect", "refused", "could not translate", "timeout")
    )


@pytest.fixture(autouse=True)
def pg_default(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """🔴 **핀을 지우고 진짜 기본값으로 돌린다** — 안 지우면 memory를 재고 통과한다.

    ⚠ `tests/conftest.py`가 오프라인 회귀용으로 `STORE_BACKEND=memory`를 걸어 둔다.
    """
    monkeypatch.delenv("STORE_BACKEND", raising=False)
    get_db_settings.cache_clear()
    get_engine.cache_clear()
    try:
        #: 🔴 **`asyncio.run`이 아니라 체크포인터의 루프로 돈다** — `_prepare_schema()`가
        #: `setup_checkpointer_schema()`를 부르고 그건 psycopg다. Windows 기본
        #: `ProactorEventLoop`에서는 이 파일 18건이 전부 `InterfaceError`로 죽었다.
        run_with_checkpoint_loop(_prepare_schema())
    except Exception as exc:
        #: 🔴 **접속 실패만 skip이다.** 종전에는 `except Exception → skip`이라
        #: 정리 SQL의 오류(없는 컬럼)까지 삼켜 **13건 전부 skip**이 됐다 — 그건
        #: 「PG가 없다」가 아니라 **「이 파일이 아무것도 안 봤다」**인데 초록으로 보였다.
        if not _looks_like_no_database(exc):
            raise
        pytest.skip(f"실 PG 미가용 — docker compose up -d ({type(exc).__name__})")
    try:
        yield
    finally:
        _forget_instance()
        get_db_settings.cache_clear()


def _forget_instance() -> None:
    """프로세스가 죽은 것과 같은 상태로 만든다 — **PG만 남긴다.**"""
    get_engine.cache_clear()
    reset_default_inquiry_class_store()
    classify_router.reset_inquiry_class_store()
    confirmations_router.reset_inquiry_class_store()
    reset_shared_agent_runtime()
    reset_counsel_stores()


@contextmanager
def instance() -> Iterator[TestClient]:
    """독립 앱 인스턴스 하나 — 들어갈 때 프로세스 상태를 버리고 나올 때 엔진을 닫는다.

    ⚠ **엔진을 닫는 자리가 여기다** — 인스턴스를 여럿 만드는 파일이라 안 닫으면
    커넥션이 계속 쌓인다(닫으려면 그 엔진을 만든 **루프 안**이어야 한다).
    """
    _forget_instance()
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        try:
            yield client
        finally:
            _portal(client).call(get_engine().dispose)


def _request_body() -> dict[str, Any]:
    """계약 §4-① 예시 — 기존 통합 테스트가 든 정본을 재사용한다(평면 import)."""
    from test_counsel_router import _REQUEST  # noqa: PLC0415

    return dict(_REQUEST)


def _portal(client: TestClient) -> BlockingPortal:
    """🔴 **`TestClient.portal`은 컨텍스트 안에서만 산다** — 타입이 `| None`이라 여기서 못 박는다.

    ⚠ `cast`로 넘기지 않는다 — 밖에서 부르면 **조용히 죽는 대신** 이 단정이 말해 준다.
    """
    portal = client.portal
    assert portal is not None, "TestClient 컨텍스트 밖에서 portal을 썼다"
    return portal


def _post(client: TestClient, tenant: str, *, suffix: str = "1") -> httpx.Response:
    response: httpx.Response = client.post(
        "/v1/counsel/drafts",
        json=_request_body(),
        headers=_headers(tenant, suffix=suffix),
    )
    return response


# ───────────────────────── 절단 가드 ─────────────────────────


def test_the_default_instance_is_really_on_pg() -> None:
    """🔴 **이 파일 전체의 절단 가드** — memory로 서면 아래 검사는 아무것도 안 본다."""
    with instance():
        assert get_db_settings().store_backend == "pg"
        assert isinstance(counsel_router._draft_view_store, PgCounselDraftViewStore)
        assert isinstance(counsel_router._idempotency_store, PgIdempotencyStore)
        assert isinstance(counsel_router._step_sink, PgCounselAgentStepSink)
        assert isinstance(
            confirmations_router.inquiry_class_store(), PgInquiryClassStore
        )


def test_a_new_instance_starts_with_empty_caches() -> None:
    """🔴 **두 번째 인스턴스가 첫 번째의 메모리를 물려받으면** 아래 검사는 공짜로 통과한다."""
    tenant = "t_flip_guard"
    with instance() as first:
        assert _post(first, tenant).status_code == 202
        assert counsel_router._view_cache.get((tenant, "x")) is None
        seen_context = counsel_router._context_store
    with instance():
        assert counsel_router._view_cache.evicted == 0
        assert len(counsel_router._view_cache._rows) == 0, "캐시가 안 비었다"
        assert counsel_router._context_store is not seen_context, (
            "인메모리 저장소가 같은 객체다 — 같은 프로세스를 두 번 재고 있다"
        )


# ───────────────────────── ㉿ ⓓ 멱등 재요청 ─────────────────────────


def test_an_idempotent_repost_is_202_and_the_get_is_200() -> None:
    """🔴 **㉿ ⓓ** — *"멱등 재전송은 202를 주는데 그 `job_id`의 GET이 404"* 를 잰다.

    ⚠ **네 증상 중 ⓓ만 한 번도 실측된 적이 없었다**(ⓐⓑⓒ는 8/10 PR에서 쟀다).
    ⚠ **`!= 404`로 재지 않는다** — 200이면서 **최초 결과와 값이 같아야** 통과다.
    """
    tenant = "t_flip_idem"
    with instance() as app_a:
        first = _post(app_a, tenant)
        assert first.status_code == 202, first.text
        job_id = first.json()["data"]["job_id"]
        execution_id = first.json()["meta"]["execution_id"]

        again = _post(app_a, tenant)
        assert again.status_code == 202, again.text
        assert again.json()["data"]["job_id"] == job_id, "재요청이 다른 잡을 만들었다"
        assert again.json()["meta"]["execution_id"] == execution_id, (
            "같은 멱등키인데 실행 원장 키가 갈렸다"
        )
        expected = app_a.get(
            f"/v1/counsel/drafts/{job_id}", headers=_headers(tenant)
        ).json()["data"]

    #: 🔴 **다른 인스턴스에서** 회수한다 — 멱등 202를 준 프로세스가 아니어도 200이어야 한다.
    with instance() as app_b:
        got = app_b.get(f"/v1/counsel/drafts/{job_id}", headers=_headers(tenant))
        assert got.status_code == 200, f"멱등 202 뒤 GET이 {got.status_code}다 — ㉿ ⓓ"
        assert got.json()["data"] == expected, "다른 인스턴스가 다른 결과를 돌려줬다"


# ───────────────────────── ㉬ 다중 인스턴스 정본 ─────────────────────────


def _assert_same_result(left: dict[str, Any], right: dict[str, Any]) -> None:
    """🔴 **개수가 아니라 값으로** 대조한다 — 인용 세 필드 전부(불변식 2의 축)."""
    assert left["status"] == right["status"]
    assert left["result"]["text"] == right["result"]["text"], "본문이 다르다"
    assert left["result"]["citations"] == right["result"]["citations"], (
        f"인용이 다르다: {left['result']['citations']} != {right['result']['citations']}"
    )
    assert left["result"]["draft_status"] == right["result"]["draft_status"]


def test_a_second_instance_reads_the_same_result() -> None:
    """㉬ — POST를 안 받은 인스턴스가 **PG에서** 같은 결과를 낸다."""
    tenant = "t_flip_multi"
    with instance() as app_a:
        job_id = _post(app_a, tenant).json()["data"]["job_id"]
        produced = app_a.get(
            f"/v1/counsel/drafts/{job_id}", headers=_headers(tenant)
        ).json()["data"]

    with instance() as app_b:
        got = app_b.get(f"/v1/counsel/drafts/{job_id}", headers=_headers(tenant))
        assert got.status_code == 200, got.text
        _assert_same_result(got.json()["data"], produced)


def test_a_second_instance_can_refine_and_a_third_sees_it() -> None:
    """㉬ — B가 다듬고 C가 읽는다. **차단도 200이다**(불변식 4)."""
    tenant = "t_flip_multi"
    with instance() as app_a:
        job_id = _post(app_a, tenant, suffix="2").json()["data"]["job_id"]

    with instance() as app_b:
        refined = app_b.post(
            f"/v1/counsel/drafts/{job_id}/refine",
            json={"instruction": "조금 더 부드럽게 써줘", "turn_no": 1},
            headers={**_headers(tenant), "Idempotency-Key": f"{tenant}:refine:1"},
        )
        assert refined.status_code == 200, refined.text
        data = refined.json()["data"]
        assert set(data) <= {"applied", "text", "citations", "blocked_reason"}
        if data["applied"]:
            assert (data.get("text") or "").strip(), "반영인데 본문이 없다"
            assert data.get("citations"), "반영 턴에도 근거가 1건 이상이어야 한다"
        else:
            assert data.get("blocked_reason"), "차단인데 사유가 없다"

    with instance() as app_c:
        got = app_c.get(f"/v1/counsel/drafts/{job_id}", headers=_headers(tenant))
        assert got.status_code == 200, "다듬기 뒤 세 번째 인스턴스가 못 읽는다"


def test_another_tenant_gets_404_from_every_instance() -> None:
    """🔴 다중 인스턴스가 되어도 **존재 은닉**은 그대로다."""
    tenant = "t_flip_multi"
    with instance() as app_a:
        job_id = _post(app_a, tenant, suffix="3").json()["data"]["job_id"]
    with instance() as app_b:
        intruding = app_b.get(
            f"/v1/counsel/drafts/{job_id}", headers=_headers(_INTRUDER)
        )
        assert intruding.status_code == 404, "다른 테넌트에 결과가 샌다"


def test_the_correction_loop_crosses_instances() -> None:
    """🔴 **㉬가 실제로 말하는 축** — `/v1/classify`와 `/v1/confirmations`다.

    ⚠ counsel 왕복만 재고 ㉬를 닫으면 **안건이 말하지 않은 것을 닫는 것**이다.
    ㉬ 본문: *"POST를 받은 프로세스와 정정을 받은 프로세스가 달라 다시 404"* —
    그래서 **분류를 A에서, 정정을 B에서** 낸다.
    """
    tenant = "t_flip_class"
    headers = {
        "X-Tenant-Id": tenant,
        "X-Request-Id": "rq-flip-class",
        "Idempotency-Key": f"{tenant}:classify:1",
    }
    with instance() as app_a:
        classified = app_a.post(
            "/v1/classify",
            json={"inquiry_ref": "iq_flip_1", "body_text": "이번 달 성적이 궁금합니다."},
            headers=headers,
        )
        assert classified.status_code == 200, classified.text
        assert classified.json()["data"]["classified"] is True, (
            "예측이 적재되지 않으면 이 검사가 헛돈다"
        )

    with instance() as app_b:
        confirmed = app_b.post(
            "/v1/confirmations",
            json={
                "kind": "classification",
                "suggestion_id": "iq_flip_1",
                "action": "corrected",
                "corrected_value": {"topic": "schedule"},
            },
            headers={**headers, "Idempotency-Key": f"{tenant}:confirm:1"},
        )
        assert confirmed.status_code == 200, (
            f"정정이 다른 인스턴스에서 {confirmed.status_code}다 — ㉬ 그대로: "
            f"{confirmed.text[:300]}"
        )
        assert confirmed.json()["data"]["accepted"] is True


# ───────────────────────── ㉻ 늦은 성공 ─────────────────────────


def _enqueue_decoy(client: TestClient, tenant: str) -> None:
    """큐에 **다른 잡**을 먼저 넣는다 — POST의 `run_next`가 그걸 집게 만든다.

    ⚠ 이게 ㉻의 발생 조건이다: `run_next`는 `worker_kind + tenant_id`로만 lease하므로
    **내 잡을 지정해 집을 수 없다**(`_generate` 주석). 앞선 잡이 있으면 내 POST는
    **미종단으로 끝난다** — 뒤늦게 끝나는 잡이 실제로 만들어진다.
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
            tenant_id=tenant,
            class_ref=request.class_ref,
            contexts={"stu_decoy": _draft_context(request)},
        )

    _portal(client).call(enqueue)


def _drain(tenant: str) -> object:
    """워커 인스턴스 — 남은 잡을 끝까지 돌린다(라우터를 안 지난다)."""

    async def run() -> None:
        from ai.api.routers.counsel import _REGEN_MAX, _build_supervisor  # noqa: PLC0415
        from ai.composition.counsel.assembly import (  # noqa: PLC0415
            open_counsel_pack_runner,
        )

        #: 🔴 **이 인스턴스에 배선된 provider를 쓴다**(지시서 73-R §3). 종전엔 여기서
        #:   `FakeCounselProvider()`를 **따로** 만들었는데, 그건 라우터가 쓰는 것과
        #:   **다른 객체일 뿐 아니라 다른 문면**이 될 수 있었다 ⇒ «최초 응답 ↔ 복원 응답»
        #:   대조가 **축과 무관한 이유로** 깨진다.
        #: ⚠ 프로세스 상태를 물려받는 것이 아니다 — `_forget_instance()`가 방금
        #:   `reset_counsel_stores()`로 **새로 꽂은** 것이고, provider는 상태가 아니라
        #:   **설정**이다(실제 워커 프로세스도 `bootstrap_counsel_provider()`로 같은 것을 만든다).
        provider = counsel_router.require_counsel_provider()
        try:
            async with open_counsel_pack_runner(
                supervisor=_build_supervisor(),
                context_store=counsel_router._context_store,
                step_sink=counsel_router._step_sink,
                draft_store=counsel_router._draft_store,
                pack_store=counsel_router._pack_store,
                planner=provider,
                writer=provider,
                regen_max=_REGEN_MAX,
                lease_owner="worker-flip",
                run_store=counsel_router._run_store,
            ) as runner:
                for _ in range(4):
                    if await runner.run_next(tenant_id=tenant) is None:
                        break
        finally:
            await get_engine().dispose()

    asyncio.run(run())
    return None


def test_a_late_success_is_produced_at_all() -> None:
    """🔴 **절단 가드** — 미종단 POST가 안 만들어지면 아래 ㉻ 검사는 아무것도 안 본다."""
    tenant = "t_flip_late"
    with instance() as app_a:
        _enqueue_decoy(app_a, tenant)
        posted = _post(app_a, tenant).json()["data"]
        assert posted["status"] != "succeeded", (
            f"POST가 그 자리에서 끝났다({posted['status']}) — 늦은 성공 시나리오가 아니다"
        )
        #: ⚠ **POST 응답에는 `result` 자리가 없다**(계약 §4-② — `job_id`·`status`뿐).
        #:   본문 유무는 **GET**이 말한다 — 두 계약을 섞어 재지 않는다.
        assert "result" not in posted, "POST 계약에 없던 필드가 생겼다"
        got = app_a.get(
            f"/v1/counsel/drafts/{posted['job_id']}", headers=_headers(tenant)
        ).json()["data"]
        assert got["result"] is None, "미종단인데 결과가 실렸다"


def test_a_worker_in_another_process_restores_the_input_and_completes() -> None:
    """🔴 **㉻ ⓐ 해소** — 다른 프로세스의 워커가 **입력 묶음을 PG에서 복원**해 완주한다.

    종전 실측은 `error_code=context_bundle_missing`이었다. 원인은 구현이 아니라 **배선**
    이었다 — `_context_store`가 초기값도 reset도 `InMemoryContextStore()` 리터럴이라
    `STORE_BACKEND=pg`가 아무 영향을 못 줬다(#37의 스텝 싱크와 같은 형태).

    ⚠ **`!= failed`로 재지 않는다** — 잡 종단과 **오류 코드 부재**를 함께 본다.
    """
    tenant = "t_flip_late"
    with instance() as app_a:
        _enqueue_decoy(app_a, tenant)
        job_id = _post(app_a, tenant, suffix="w").json()["data"]["job_id"]

    _forget_instance()
    _drain(tenant)

    with instance() as app_c:
        got = app_c.get(f"/v1/counsel/drafts/{job_id}", headers=_headers(tenant))
        assert got.status_code == 200, got.text
        data = got.json()["data"]
        assert data["status"] == "succeeded", (
            f"다른 프로세스 워커가 완주 못 했다({data['status']}) — "
            f"입력 묶음을 PG에서 못 찾은 것이다"
        )
        #: 🔴 **오류 코드 자체가 없어야 한다** — 종전 결손의 이름을 못 박는다.
        assert data.get("error_code") in (None, ""), data.get("error_code")


def test_a_late_success_carries_the_body_to_a_brand_new_instance() -> None:
    """🔴 **㉻ ⓑ 해소 — 늦게 끝난 잡의 본문이 새 인스턴스의 GET에 실린다.**

    종전 실측은 `phase=succeeded` · `result_ref=pack://…` 인데 GET은 `result=None`이었다:
    초안 본문이 `_draft_store`(인메모리)에만 있었기 때문이다.

    🔴 **`result is not None`으로 재지 않는다**(지시서 73 §6) — 본문·인용·라벨·상태를
    **값으로** 대조한다. 인스턴스 A·워커 B·조회 C가 **프로세스 상태를 하나도 공유하지
    않는다** — 손으로 넘기는 `set_counsel_stores`는 이제 쓰지 않는다.
    """
    tenant = "t_flip_late"
    with instance() as app_a:
        _enqueue_decoy(app_a, tenant)
        posted = _post(app_a, tenant, suffix="b").json()["data"]
        job_id = posted["job_id"]
        assert posted["status"] != "succeeded", "미종단 POST가 아니다 — 늦은 성공이 아니다"

    #: 워커 B — 인스턴스 A의 메모리를 **한 톨도 물려받지 않는다.**
    _forget_instance()
    _drain(tenant)

    #: 조회 C — 또 다른 인스턴스.
    _forget_instance()
    with instance() as app_c:
        got = app_c.get(f"/v1/counsel/drafts/{job_id}", headers=_headers(tenant))
        assert got.status_code == 200, got.text
        data = got.json()["data"]
        assert data["status"] == "succeeded", f"워커가 완주 못 했다({data['status']})"
        result = data["result"]
        assert result is not None, "늦은 성공의 본문이 없다 — ㉻ ⓑ 그대로다"

        assert result["draft_status"] == "generated", result["draft_status"]
        assert result["text"], "본문이 비었다"
        #: 🔴 **정확 값이다** — *"비어 있지 않다"* 는 순서 뒤바뀜·남의 근거·다른 문면을
        #:   전부 통과시킨다(지시서 73-R §1).
        assert _cited(result) == _EXPECTED_CITATIONS, (
            f"인용이 계약값과 다르다\n  기대: {_EXPECTED_CITATIONS}\n  실제: {_cited(result)}"
        )
        #: 🔴 **배열 전문 대조** — 집합이면 순서 변경이 통과한다(§2).
        assert result["labels_applied"] == _EXPECTED_LABELS, (
            f"labels_applied가 다르다\n  기대: {_EXPECTED_LABELS}\n"
            f"  실제: {result['labels_applied']}"
        )

        #: 🔴 **다른 테넌트는 404다** — 본문이 PG에 남아도 격리가 먼저다.
        intruder = app_c.get(
            f"/v1/counsel/drafts/{job_id}", headers=_headers(_INTRUDER)
        )
        assert intruder.status_code == 404, intruder.text


#: 🔴 **대조 축과 제외 축을 이름으로 갈라 둔다**(지시서 73-R §3).
#: ⚠ `result` dict 전체를 `==`로 뭉치면 ⓐ `generated_at`이 달라 항상 red이고
#:   ⓑ 제외한 것이 무엇인지 아무도 모른다. 반대로 필드를 손으로 세면 **새 필드가
#:   생겼을 때 조용히 빠진다** ⇒ 아래 `_RESULT_FIELDS` 대조가 그 구멍을 막는다.
_COMPARED_FIELDS: Final = (
    "draft_status",
    "text",
    "citations",
    "labels_applied",
    "status_reason",
    "label_suggestions",
)
#: 🔴 **조회 시각이라 서로 다를 수 있다** — 값 대조에서 빼되 **형식은 각각 본다.**
_TIME_FIELDS: Final = ("generated_at",)


def _result_of(client: TestClient, tenant: str, job_id: str) -> Mapping[str, Any]:
    got = client.get(f"/v1/counsel/drafts/{job_id}", headers=_headers(tenant))
    assert got.status_code == 200, got.text
    data = got.json()["data"]
    result = data["result"]
    assert result is not None, f"result가 없다(status={data['status']})"
    return dict(result)


def test_the_restored_result_equals_the_immediate_one_field_by_field() -> None:
    """🔴 **최초 응답과 복원 응답이 필드별로 같다** (지시서 73-R §3).

    ⚠ 늦은 성공만 정확 값으로 재면 **최초 경로가 다른 파생을 쓰는 경우**를 놓친다 —
    둘 다 정확해도 서로 다르면 같은 잡의 두 응답이 갈린 것이다.

    | 경로 | 만드는 법 |
    | --- | --- |
    | 즉시 | POST가 자기 잡을 집어 그 자리에서 끝낸다(디코이 없음) |
    | 늦은 성공 | A POST(미종단) → 워커 B 완주 → 인스턴스 C GET |

    ⚠ `generated_at`은 **조회 시각**이라 제외한다 — 대신 둘 다 tz-aware인지 본다.
    """
    from test_counsel_router import _RESULT_FIELDS  # noqa: PLC0415

    #: ── ① 즉시 완료 — 큐가 비어 있어 POST가 자기 잡을 집는다.
    immediate_tenant = "t_flip_multi"
    with instance() as app_now:
        posted = _post(app_now, immediate_tenant, suffix="im").json()["data"]
        assert posted["status"] == "succeeded", (
            f"즉시 완료가 아니다({posted['status']}) — 이 검사의 한쪽 축이 없다"
        )
        immediate = _result_of(app_now, immediate_tenant, posted["job_id"])

    #: ── ② 늦은 성공 — 프로세스 상태를 하나도 공유하지 않는다.
    late_tenant = "t_flip_late"
    with instance() as app_a:
        _enqueue_decoy(app_a, late_tenant)
        late_job = _post(app_a, late_tenant, suffix="eq").json()["data"]
        assert late_job["status"] != "succeeded", "미종단 POST가 아니다"

    _forget_instance()
    _drain(late_tenant)
    _forget_instance()
    with instance() as app_c:
        restored = _result_of(app_c, late_tenant, late_job["job_id"])

    #: 🔴 **필드 집합이 계약과 같다** — 새 필드가 생기면 여기서 걸리고, 그때
    #:   `_COMPARED_FIELDS`/`_TIME_FIELDS` 중 어디에 넣을지 정하게 된다.
    assert set(immediate) == _RESULT_FIELDS, sorted(set(immediate) ^ _RESULT_FIELDS)
    assert set(restored) == _RESULT_FIELDS, sorted(set(restored) ^ _RESULT_FIELDS)
    assert set(_COMPARED_FIELDS) | set(_TIME_FIELDS) == _RESULT_FIELDS, (
        "대조 축 + 제외 축이 계약 필드 전수와 다르다 — 조용히 빠진 필드가 있다"
    )

    for field in _COMPARED_FIELDS:
        assert immediate[field] == restored[field], (
            f"«{field}»가 두 경로에서 다르다\n"
            f"  즉시: {immediate[field]!r}\n  복원: {restored[field]!r}"
        )

    #: 🔴 두 응답 모두 **계약 기대값**이다 — 서로 같기만 하고 둘 다 틀릴 수 있다.
    assert _cited(immediate) == _EXPECTED_CITATIONS
    assert immediate["labels_applied"] == _EXPECTED_LABELS

    #: 제외 축은 **형식만** 본다 — tz-aware가 아니면 재현성 축이 깨진다.
    for label, result in (("즉시", immediate), ("복원", restored)):
        for field in _TIME_FIELDS:
            stamp = datetime.fromisoformat(result[field])
            assert stamp.tzinfo is not None, f"{label} {field}가 tz-naive다: {result[field]}"


def test_the_late_body_is_identical_across_two_more_instances() -> None:
    """🔴 **캐시가 아니라 PG에서 온다** — 인스턴스를 두 번 더 갈아도 같은 값이다.

    ⚠ 한 인스턴스에서만 재면 **프로세스 캐시가 답한 것**과 구분되지 않는다.
    """
    tenant = "t_flip_late"
    with instance() as app_a:
        _enqueue_decoy(app_a, tenant)
        job_id = _post(app_a, tenant, suffix="r").json()["data"]["job_id"]

    _forget_instance()
    _drain(tenant)

    seen: list[Any] = []
    for _ in range(2):
        _forget_instance()
        with instance() as app_n:
            got = app_n.get(f"/v1/counsel/drafts/{job_id}", headers=_headers(tenant))
            assert got.status_code == 200, got.text
            result = got.json()["data"]["result"]
            assert result is not None, "새 인스턴스에서 본문이 사라졌다"
            seen.append((result["text"], result["draft_status"], result["citations"]))

    assert seen[0] == seen[1], f"인스턴스마다 값이 다르다:\n  {seen[0]}\n  {seen[1]}"


def test_the_two_counsel_stores_are_wired_to_pg() -> None:
    """🔴 **㉻의 원인이던 두 자리가 팩토리를 탄다** — 인메모리로 되돌리면 red.

    ⚠ 「본문이 있다」만 재면 다음 사람이 `_refresh_view`를 고치려 든다. 막고 있던 것은
    그게 아니라 **이 두 저장소가 팩토리를 안 탄다**는 사실이었다.
    """
    with instance():
        assert isinstance(counsel_router._context_store, PgContextStore), (
            f"ContextStore가 {type(counsel_router._context_store).__name__}다 — "
            f"기본값 pg에서 인메모리면 ㉻ ⓐ가 그대로다"
        )
        assert isinstance(counsel_router._draft_store, PgDraftResultStore), (
            f"DraftResultStore가 {type(counsel_router._draft_store).__name__}다 — "
            f"㉻ ⓑ가 그대로다"
        )
        #: ⚠ 반대편 — 인메모리 구현이 사라진 것은 아니다(memory 백엔드는 그대로다).
        assert issubclass(InMemoryContextStore, object)
        assert issubclass(InMemoryDraftResultStore, object)


# ───────────────────────── 실패·격리 ─────────────────────────


def test_an_unreachable_pg_does_not_fall_back_to_memory() -> None:
    """🔴 **접속 실패는 휘발성으로 강등되지 않는다** — 조용한 memory 폴백 금지.

    ⚠ 폴백이 있으면 **DB가 죽은 것과 정상이 같은 응답**이 되고, 그 사이 데이터는
    프로세스와 함께 사라진다. 실패는 실패로 올라와야 한다.
    """
    import os  # noqa: PLC0415

    original = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = "postgresql+asyncpg://checkon@localhost:1/checkon_ai"
    get_db_settings.cache_clear()
    get_engine.cache_clear()
    try:
        store = build_idempotency_store()
        assert isinstance(store, PgIdempotencyStore), (
            "접속 못 하는 URL에서 memory 저장소가 나왔다 — 조용한 강등이다"
        )

        async def attempt() -> None:
            try:
                await store.get(
                    tenant_id="t_flip_guard",
                    endpoint="/v1/counsel/drafts",
                    idempotency_key="k",
                )
            finally:
                await get_engine().dispose()

        with pytest.raises(Exception, match=r"(?i)connect|refused|timeout"):
            asyncio.run(attempt())
    finally:
        if original is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original
        get_db_settings.cache_clear()
        get_engine.cache_clear()


def test_an_orphan_agent_step_is_refused_by_the_database() -> None:
    """🔴 **부모 없는 `AGENT_STEP`은 저장되면 안 된다** — `agent_step.agent_run_id` FK.

    ⚠ G1이 지운 것은 `agent_run.run_id → ai_run` **하나**다. 스텝의 부모 제약은
    **살아 있어야 하고**, 그걸 같이 지웠는지 여기서 확인한다(플립은 쓰기를 실제로 늘린다).
    """
    from ai.composition.counsel.stores import AgentStepRecord  # noqa: PLC0415

    with instance():
        sink = counsel_router._step_sink
        assert isinstance(sink, PgCounselAgentStepSink)

        async def attempt() -> None:
            await sink.record(
                AgentStepRecord(
                    id=uuid.uuid4(),
                    agent_run_id=uuid.uuid4(),  # 존재하지 않는 부모
                    seq=1,
                    node_name="plan",
                    tool_called=None,
                    tool_args_masked={},
                    llm_call_id=None,
                    outcome="ok",
                )
            )

        with pytest.raises(Exception, match=r"(?i)foreign key|violates"):
            asyncio.run(attempt())


# ───────────── 배포 명령 — 체크포인터 스키마 (99 #39) ─────────────

#: `AsyncPostgresSaver.setup()`이 만드는 집합 — 🔴 **한 이름만 보지 않는다.**
#: `checkpoints` 하나만 확인하면 *"필요한 게 다 섰다"* 를 못 말한다.
_CHECKPOINT_TABLES: Final = (
    "checkpoint_blobs",
    "checkpoint_migrations",
    "checkpoint_writes",
    "checkpoints",
)


async def _checkpoint_tables() -> set[str]:
    engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                        "AND tablename LIKE 'checkpoint%'"
                    )
                )
            ).scalars()
            return set(rows)
    finally:
        await engine.dispose()


async def _drop_checkpoint_tables() -> None:
    """🔴 **체크포인터 테이블만** 지운다 — 애플리케이션 테이블은 그대로 둔다.

    ⚠ 두 축을 같이 지우면 *"무엇이 없어서 실패했는가"* 를 못 가른다.
    """
    engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            for table in reversed(_CHECKPOINT_TABLES):
                await conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
    finally:
        await engine.dispose()


def _counsel_job_phase(tenant: str) -> str:
    """기본 pg 설정으로 상담 잡 하나를 돌리고 **잡 phase**를 돌려준다."""
    with instance() as app:
        posted = _post(app, tenant, suffix="cp")
        assert posted.status_code == 202, posted.text
        return str(posted.json()["data"]["status"])


def test_the_deployment_command_prepares_and_is_rerunnable() -> None:
    """🔴 **빈 체크포인터 스키마 → CLI → 재실행 → 상담 잡**을 한 줄기로 잰다 (99 #39).

    실측한 인과다: 체크포인터 테이블이 없으면 잡이 **`failed`**로 떨어진다
    (`relation "checkpoints" does not exist` → `worker_internal_error`).
    ⚠ **기동은 정상이고 POST도 202**다 — 그래서 조용하다.

    ⚠ **테이블 이름 하나로 판정하지 않는다** — `setup()`이 만드는 집합 전체를 보고,
    **마지막에 상담 잡을 실제로 돌려** 그 집합이 충분한지 확인한다.
    """
    asyncio.run(_drop_checkpoint_tables())
    assert asyncio.run(_checkpoint_tables()) == set(), "체크포인터 테이블이 안 지워졌다"

    #: ① CLI를 안 돌린 상태 — 잡이 실패한다(이 검사의 존재 이유).
    assert _counsel_job_phase("t_flip_guard") == "failed", (
        "체크포인터 스키마 없이 상담 잡이 성공했다 — 이 배포 단계가 필요 없다는 뜻이거나 "
        "이 검사가 그 상태를 못 만들고 있다"
    )

    #: ② 배포 명령 1회.
    assert checkpointer_main() == 0, "배포 명령이 실패했다"
    assert set(_CHECKPOINT_TABLES) <= asyncio.run(_checkpoint_tables()), (
        f"필요한 테이블이 다 안 섰다: {asyncio.run(_checkpoint_tables())}"
    )

    #: ③ 재실행 — 배포는 두 번 돌 수 있어야 한다.
    assert checkpointer_main() == 0, "재실행이 실패했다 — 멱등이 아니다"

    #: ④ 같은 잡이 이제 완주한다 — **집합이 충분했는지는 이것이 말한다.**
    assert _counsel_job_phase("t_flip_multi") == "succeeded", (
        "배포 명령을 돌렸는데도 상담 잡이 안 끝난다 — 스키마 집합이 부족하다"
    )


def test_the_command_does_not_depend_on_the_backend_flag() -> None:
    """🔴 **`STORE_BACKEND=memory`에서도 준비한다** — *"이 DB를 준비하라"* 는 명령이다.

    ⚠ 플래그를 보면 **다음 플립을 위해 미리 준비하는 실행이 조용히 아무것도 안 하고
    성공**한다 — 배포자는 준비된 줄 안다.
    """
    import os  # noqa: PLC0415

    asyncio.run(_drop_checkpoint_tables())
    os.environ["STORE_BACKEND"] = "memory"
    get_db_settings.cache_clear()
    try:
        assert checkpointer_main() == 0
        assert set(_CHECKPOINT_TABLES) <= asyncio.run(_checkpoint_tables())
    finally:
        os.environ.pop("STORE_BACKEND", None)
        get_db_settings.cache_clear()


def test_an_unreachable_database_fails_the_command_loudly() -> None:
    """🔴 **접속 실패는 종료 코드 0이 아니다** — 배포가 그걸 보고 중단한다.

    ⚠ 성공 문면이 같이 나오면 안 된다(로그만 보고 넘어간다).
    """
    import os  # noqa: PLC0415

    original = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = "postgresql+asyncpg://checkon@localhost:1/checkon_ai"
    get_db_settings.cache_clear()
    try:
        assert checkpointer_main() == 1, "접속 못 하는데 성공으로 끝났다"
    finally:
        if original is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original
        get_db_settings.cache_clear()
        #: 다음 검사가 쓸 수 있게 되돌린다 — 이 파일이 스키마를 내렸다.
        assert checkpointer_main() == 0
