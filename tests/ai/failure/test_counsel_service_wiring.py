"""서비스 조립 경로 — 미배선·부분 배선·추적 활성에서 **기동/요청이 거부된다**.

감사에서 나온 것: 라우터가 조립부(`open_counsel_pack_runner`)를 우회해 워커를 직접 만들어
왔다. 그 우회 하나로 두 가지 방어가 **서비스 경로에서만** 빠져 있었다.

| 빠진 것 | 결과 |
| --- | --- |
| provider 명시 주입 | 🔴 미배선이면 **Fake가 답한다** — 게이트를 통과하고 원장에도 안 남는다 |
| `require_tracing_disabled` | `LANGSMITH_TRACING=true`여도 그냥 돌았다(마스킹 전 state 유출) |

`assembly.py`는 같은 결함을 이미 제거하며 사유를 적어 뒀다 — *"운영 배선 실수가 곧 날조
산출 저장이었다(조용한 Fake가 최악)."* 조립부에서만 제거됐고 라우터에는 남아 있었다.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.api.routers import counsel as counsel_router
from ai.api.routers.counsel import (
    CounselProviderNotWired,
    require_counsel_provider,
    reset_counsel_stores,
    set_counsel_provider,
)
from ai.composition.counsel.provider import FakeCounselProvider
from ai.composition.counsel.stores import (
    DRAFT_SCHEME,
    DraftRecord,
    InMemoryDraftResultStore,
    make_ref,
)
from ai.runtime.tracing import TRACING_ENV_SYNONYMS

_HEADERS = {
    "X-Tenant-Id": "t1",
    "X-Request-Id": "rq-wire-1",
    "Idempotency-Key": "t1:counsel:wire",
}

_BODY: dict[str, Any] = {
    "inquiry": {
        "inquiry_ref": "iq_910",
        "topic": "counsel_request",
        "urgency": "normal",
        "received_at": "2026-08-06T14:20:00+09:00",
        "text_masked": "이번 달 어떤지 궁금합니다",
    },
    "student_ref": "st_1",
    "parent_ref": "pa_1",
    "class_ref": "cl_a1",
    "labels": [],
    "dismissed_suggestions": [],
    "context": {
        "snapshot_hash": "sha256:" + "e" * 64,
        "period_label": "2026년 7월",
        "facts": [{"record_id": "le_2041", "summary": "6월 지문 42개"}],
    },
}


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    reset_counsel_stores()
    yield
    reset_counsel_stores()


@pytest.fixture
def unwired() -> Iterator[None]:
    """배선 이전 상태를 재현한다 — 끝나면 반드시 되돌린다(세션 전역이다)."""
    original = counsel_router._provider
    counsel_router._provider = None
    yield
    counsel_router._provider = original


# ── A-1 미배선이면 기동이 실패한다 ────────────────────────────────


def test_app_does_not_start_when_provider_is_not_wired(unwired: None) -> None:
    """🔴 배선 실수가 **첫 요청**이 아니라 **기동**에서 드러난다.

    종전에는 기본값이 Fake라 그냥 떴고, 그 Fake가 `fallback_text`를 돌려주면
    숫자·금칙어가 없어 **게이트를 통과**한다 — 정상 초안으로 학부모에게 나간다.
    """
    with pytest.raises(CounselProviderNotWired), TestClient(create_app()):
        pass  # 기동에서 터진다 — 요청까지 가지 않는다


def test_require_counsel_provider_names_the_seam(unwired: None) -> None:
    """메시지가 **무엇을 부르면 되는지** 말한다 — 배포 현장에서 읽히는 문장이어야 한다."""
    with pytest.raises(CounselProviderNotWired, match="set_counsel_provider"):
        require_counsel_provider()


def test_wired_app_starts(unwired: None) -> None:
    """가드가 정상 배선을 막지 않는다(대칭 확인)."""
    set_counsel_provider(FakeCounselProvider())
    with TestClient(create_app()) as client:
        assert client.post("/v1/counsel/drafts", json=_BODY, headers=_HEADERS).status_code == 202


# ── A-3 planner 없는 writer만 꽂으면 주입에서 거부된다 ────────────


class _WriterOnly:
    """`write`만 있는 provider — 워커는 `plan`도 호출한다."""

    async def write(self, **_kwargs: object) -> str:
        return "본문"


class _PlannerOnly:
    async def plan(self, **_kwargs: object) -> dict[str, list[str]]:
        return {}


@pytest.mark.parametrize(
    ("provider", "missing"), [(_WriterOnly(), "plan"), (_PlannerOnly(), "write")]
)
def test_partial_provider_is_rejected_at_injection(
    provider: object, missing: str
) -> None:
    """🔴 종전에는 기동이 통과하고 **첫 요청에서** `worker_internal_error`가 났다.

    배선 실수는 배선 시점에 터지는 게 맞다 — 그래야 어느 객체가 뭘 빠뜨렸는지가 남는다.
    """
    with pytest.raises(CounselProviderNotWired, match=missing):
        set_counsel_provider(provider)


def test_rejected_provider_does_not_replace_the_wired_one() -> None:
    """거부된 주입이 기존 배선을 **깨뜨리지 않는다** — 반쯤 배선된 상태가 더 나쁘다."""
    good = FakeCounselProvider()
    set_counsel_provider(good)
    with pytest.raises(CounselProviderNotWired):
        set_counsel_provider(_WriterOnly())
    assert counsel_router._provider is good


# ── A-2 라우터가 러너를 직접 만들지 않는다 ────────────────────────


def test_router_never_constructs_the_runner_directly() -> None:
    """🔴 직접 생성이 곧 **조립부 우회**다 — 추적 가드와 체크포인터 선택이 함께 빠진다.

    주석으로 막을 수 있는 종류가 아니라서 소스로 고정한다(같은 우회가 두 번 났다).
    """
    tree = ast.parse(Path(inspect.getfile(counsel_router)).read_text(encoding="utf-8"))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "CounselPackRunner" not in called, "라우터가 러너를 직접 만든다(조립부 우회)"
    assert "open_counsel_pack_runner" in called, "조립부를 경유하지 않는다"


def test_router_does_not_pick_its_own_checkpointer() -> None:
    """체크포인터 선택은 조립부(`_open_saver`)의 몫이다 — 라우터가 고르면 PG 설정이 무시된다."""
    source = Path(inspect.getfile(counsel_router)).read_text(encoding="utf-8")
    assert "InMemorySaver" not in source


# ── A-2 추적이 켜져 있으면 요청이 거부된다 ────────────────────────


def test_post_is_refused_while_external_tracing_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """🔴 마스킹 전 state가 노드 경계로 나간다 — 추적 중이면 아예 돌지 않는다(불변식 3).

    ⚠ 종전에는 서비스 경로가 조립부를 우회해서 **이 가드가 실행되지 않았다.**
    ⚠ "4xx/5xx면 통과"로 두지 않는다 — 다른 이유로 실패해도 통과해 헛돈다. 거부 사유가
    **추적 가드**임을 문면으로 확인한다(같은 바디가 `test_wired_app_starts`에서 202다).
    """
    with TestClient(create_app()) as client:
        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        with pytest.raises(ValueError, match="counsel_pack 워커를 기동할 수 없다"):
            client.post("/v1/counsel/drafts", json=_BODY, headers=_HEADERS)


def test_tracing_guard_covers_every_synonym() -> None:
    """env 이름을 라우터가 따로 들지 않는다 — 판정 정본은 `runtime/tracing`이다."""
    assert "LANGSMITH_TRACING" in TRACING_ENV_SYNONYMS


# ── B-4 저장소 층 테넌트 방어 ─────────────────────────────────────


def _draft_record(tenant_id: str) -> DraftRecord:
    from datetime import UTC, datetime

    return DraftRecord(
        id=uuid4(),
        run_id=uuid4(),
        agent_run_id=None,
        tenant_id=tenant_id,
        kind="counsel",
        student_ref="st_1",
        guardian_ref="pa_1",
        label_snapshot={},
        status="generated",
        fail_reason=None,
        created_at=datetime.now(UTC),
        content="초안 본문",
    )


@pytest.mark.parametrize("asking", ["t2", "", "T1"])
def test_draft_store_refuses_other_tenants(asking: str) -> None:
    """🔴 `ContextStore.get`과 동형 — 초안 **본문**이라 유출 피해가 더 크다."""
    store = InMemoryDraftResultStore()
    record = _draft_record("t1")

    async def scenario() -> tuple[Any, Any]:
        ref = await store.put(record)
        return await store.get(ref, tenant_id=asking), await store.get(ref, tenant_id="t1")

    import asyncio

    other, own = asyncio.run(scenario())
    assert other is None, f"다른 테넌트({asking!r})가 초안 본문을 읽었다"
    assert own is not None, "자기 테넌트는 읽어야 한다"


def test_draft_store_signature_matches_context_store() -> None:
    """비대칭이 결함의 자리였다 — 두 저장소의 조회 규약을 같게 고정한다."""
    from ai.composition.counsel.stores import ContextStore, DraftResultStore

    assert (
        inspect.signature(DraftResultStore.get).parameters.keys()
        == inspect.signature(ContextStore.get).parameters.keys()
    )


def test_ref_alone_is_not_enough_to_read_a_draft() -> None:
    """ref를 알아도 테넌트가 맞아야 한다 — ref는 불투명하지만 추측 불가는 아니다."""
    store = InMemoryDraftResultStore()
    record = _draft_record("t1")
    import asyncio

    async def scenario() -> DraftRecord | None:
        await store.put(record)
        return await store.get(make_ref(DRAFT_SCHEME, record.id), tenant_id="t2")

    assert asyncio.run(scenario()) is None
