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
import logging
import re
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
from ai.composition.counsel.assembly import build_counsel_llm_provider
from ai.composition.counsel.provider import (
    CompositeCounselProvider,
    FakeCounselProvider,
)
from ai.composition.counsel.settings import CounselSettings
from ai.composition.counsel.stores import (
    DRAFT_SCHEME,
    DraftRecord,
    InMemoryDraftResultStore,
    make_ref,
)
from ai.db.repositories.run_store import InMemoryRunStore
from ai.db.store_factory import reset_shared_agent_runtime
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
    reset_shared_agent_runtime()  # A·B 공용 잡 원장(99 ㊒)
    reset_counsel_stores()
    yield
    reset_shared_agent_runtime()  # A·B 공용 잡 원장(99 ㊒)
    reset_counsel_stores()


@pytest.fixture
def unwired() -> Iterator[None]:
    """배선 이전 상태를 재현한다 — 끝나면 반드시 되돌린다(세션 전역이다)."""
    original = counsel_router._provider
    counsel_router._provider = None
    yield
    counsel_router._provider = original


# ── A-1 미배선이면 기동이 실패한다 ────────────────────────────────


def test_app_does_not_start_when_provider_is_not_wired(
    unwired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 배선 실수가 **첫 요청**이 아니라 **기동**에서 드러난다.

    종전에는 기본값이 Fake라 그냥 떴고, 그 Fake가 `fallback_text`를 돌려주면
    숫자·금칙어가 없어 **게이트를 통과**한다 — 정상 초안으로 학부모에게 나간다.

    ⚠ **재현 조건이 바뀌었다(조립 루트 신설).** 전에는 `_provider = None`만으로 이 상태가
    됐지만 이제는 루트가 기동 때 채우므로 **루트까지 비워야** 한다. 막는 성질은 그대로다 —
    "아무도 배선하지 않으면 앱이 뜨지 않는다."
    """
    monkeypatch.setattr(counsel_router, "bootstrap_counsel_provider", lambda: None)
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


# ── 조립 루트 (PR-1의 꼬리) ───────────────────────────────────────


def test_app_boots_through_the_composition_root(unwired: None) -> None:
    """🔴 수용 기준 1 — 조립 루트를 거쳐 앱이 뜬다.

    PR-1은 **막기만 하고 배선을 안 만들어** 서비스가 뜨지 않았다(99 ㉥). 그 반쪽을 채운다.
    """
    with TestClient(create_app()) as client:
        assert isinstance(counsel_router._provider, CompositeCounselProvider)
        assert client.post(
            "/v1/counsel/drafts", json=_BODY, headers=_HEADERS
        ).status_code == 202


def test_composition_root_does_not_overwrite_an_explicit_injection() -> None:
    """🔴 이미 배선돼 있으면 덮지 않는다 — 안 그러면 주입 seam이 무의미해진다.

    테스트·평가 러너는 자기 시나리오 provider를 꽂고 앱을 띄운다. 조립 루트가 기동 때마다
    갈아치우면 `set_counsel_provider`가 있으나 마나다.
    """
    mine = FakeCounselProvider()
    set_counsel_provider(mine)
    with TestClient(create_app()):
        assert counsel_router._provider is mine


def test_startup_order_is_assemble_then_verify(unwired: None) -> None:
    """확인이 조립보다 **뒤**여야 한다 — 앞이면 정상 기동도 막힌다."""
    source = Path(inspect.getfile(counsel_router)).read_text(encoding="utf-8")
    body = source[source.index("def _startup()") :]
    assert body.index("bootstrap_counsel_provider()") < body.index(
        "require_counsel_provider()"
    )


# ── B "fake를 고르는 것"과 "잊는 것"의 구분 ───────────────────────


def test_choosing_fake_logs_which_env_value_caused_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """🔴 fake는 **명시적 선택**이라 흔적을 남긴다 — 잊은 것과 구분되어야 한다."""
    with caplog.at_level(logging.WARNING, logger="ai.composition.counsel.assembly"):
        provider = build_counsel_llm_provider(CounselSettings(llm_provider="fake"))
    assert provider.name == "fake-counsel"
    assert "fake" in caplog.text
    assert "LLM_PROVIDER" in caplog.text  # 어떤 env가 그렇게 만들었는지


def test_choosing_the_real_provider_does_not_warn(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실 경로에서는 경고가 없다 — 늘 울리는 경고는 아무도 안 본다."""
    sentinel = object()
    monkeypatch.setattr(
        "ai.llm.providers.openai_compat.OpenAICompatProvider", lambda: sentinel
    )
    with caplog.at_level(logging.WARNING, logger="ai.composition.counsel.assembly"):
        chosen = build_counsel_llm_provider(CounselSettings(llm_provider="openai_compat"))
    assert chosen is sentinel
    assert caplog.text == ""


def test_fake_output_is_distinguishable_after_the_fact(unwired: None) -> None:
    """🔴 B-2 판정 — 사후에 "이 초안이 진짜였나"를 가릴 수 있다.

    조립 루트의 fake는 **게이트웨이를 거치므로** `LLM_CALL`·`AI_RUN`에 `fake-counsel`이
    남는다. ⚠ 종전 기본값(`FakeCounselProvider`)은 게이트웨이를 안 거쳐 **호출 0건 ·
    `model_provider=None`** 이었다 — 원장만 보고는 구분이 불가능했다.
    """
    store = InMemoryRunStore()
    counsel_router.set_counsel_run_store(store)
    with TestClient(create_app()) as client:
        client.post("/v1/counsel/drafts", json=_BODY, headers=_HEADERS)

    assert store.calls, "fake가 게이트웨이를 거치지 않아 원장에 아무것도 안 남았다"
    assert {call.provider for call in store.calls} == {"fake-counsel"}
    assert {run.model_provider for run in store.runs.values()} == {"fake-counsel"}


def test_ci_default_makes_no_real_llm_call() -> None:
    """⚠ B-3 — CI 기본은 fake다. 이 PR이 그걸 바꾸지 않는다."""
    assert CounselSettings().llm_provider == "fake"
    assert build_counsel_llm_provider(CounselSettings()).name == "fake-counsel"


# ── A-1 승격본이 하나뿐인지 ───────────────────────────────────────


def test_composite_provider_is_defined_exactly_once() -> None:
    """🔴 두 벌이면 따로 늙는다(99 ⑰·㉚ 패턴) — 평가 러너는 승격본을 import한다."""
    root = Path(inspect.getfile(counsel_router)).parents[3]
    pattern = re.compile(r"^class \w*Composite\w*Provider\b", re.MULTILINE)
    definitions = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    )
    assert definitions == ["ai/composition/counsel/provider.py"], definitions
