"""🔴 **실패 종류에 따라 원장이 갈리지 않는다** — 부수효과를 `except` 절에 두지 않는다.

PR-4(#117)가 세운 규율이 refine 한 경로에만 적용됐고, 그 PR이 스스로 만든 형태가 두 곳에
남았다. **근본 원인은 하나다: 부수효과(원장 적재)를 `except` 절 안에 복제했다.**

    try:
        outcome = await refine_draft(...)
    except LlmError as exc:
        await _record_refine_run(..., swallow_errors=True)   # ← 부수효과가 절 안에 산다
        raise domain_error_for(exc) from exc
    await _record_refine_run(...)                            # ← 같은 부수효과가 또

예외 종류가 하나 늘어나는 순간 **그 절을 안 지나는 경로**가 생기고, 그 경로만 원장이
빈다. `RedactionUncertain`은 `DomainException`이라 `except LlmError`가 못 잡는다 —
두 `_record_refine_run` 어느 쪽도 안 지나고 `take(execution_id)`도 안 불린다.

실측(8/7 · 고치기 전):

| | HTTP | AI_RUN 증가 | 수집기 잔존 |
| --- | --- | --- | --- |
| refine × `RedactionUncertain` | 500 | **0** | **1** |
| classify × `LlmTimeout` | **500**(504여야) | **0** | **1** |
| classify × `LlmUnavailable` | **500**(503여야) | **0** | **1** |
| classify × `LlmError` | 500 | **0** | **1** |
| **워커 × 서킷 개방**(임계 3) | paused | **0** | **3** ← 가장 나쁘다 |
| 워커 × `ContextHashMismatch` | failed | **0** | 0 |
| 워커 × 미분류 예외 | failed | **0** | 0 |

⚠ **PR-4의 `test_the_failed_turn_still_lands_in_the_ledger`가 `LlmTimeout` 하나만 봐서
이 결함이 초록으로 통과했다.** 그래서 여기는 **파라미터화**한다 — 종류를 늘려도 안 갈리는
것이 성질이고, 성질은 한 종류로 증명되지 않는다.
"""

from __future__ import annotations

import asyncio
import importlib.util
import itertools
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from ai.agents.job_store import InMemoryJobStore
from ai.agents.supervisor import Supervisor
from ai.api.app import create_app
from ai.api.routers import classify as classify_router
from ai.api.routers import confirmations as confirmations_router
from ai.api.routers import counsel as counsel_router
from ai.api.routers.counsel import reset_counsel_stores, set_counsel_provider
from ai.composition.counsel.assembly import DEFAULT_REGEN_MAX, build_counsel_gateway
from ai.composition.counsel.enqueue import CounselPackEnqueuer
from ai.composition.counsel.provider import (
    CompositeCounselProvider,
    FakeCounselLlmProvider,
    FakeCounselProvider,
    GatewayDraftWriter,
    GatewayPlanner,
    RedactionBlockedError,
)
from ai.composition.counsel.stores import (
    ContextBundleRecord,
    InMemoryAgentStepSink,
    InMemoryContextStore,
    InMemoryDraftResultStore,
    InMemoryPackResultStore,
)
from ai.composition.counsel.worker import CounselPackRunner
from ai.contracts.agents import JobPhase, WorkerJob
from ai.contracts.composition import (
    CommStyle,
    DraftContext,
    EvidenceFact,
    Frequency,
    Interest,
    LabelSnapshot,
    Sensitivity,
)
from ai.contracts.execution import ExecutionContext
from ai.contracts.llm import LlmError, LLMRequest, LLMResult, LlmTimeout, LlmUnavailable
from ai.db.repositories.run_store import InMemoryRunStore, default_llm_call_collector

#: 실패 종류 축 — 🔴 `RedactionUncertain`을 **반드시** 포함한다(그게 빠져 결함이 통과했다).
#: refine 쪽은 `RedactionBlockedError`를 주입하면 `refine_draft`가 `RedactionUncertain`으로
#: 올린다(주체 분리 · #117 작업 5).
_REFINE_FAILURES = [
    (LlmTimeout("t"), 504, "TIMEOUT"),
    (LlmUnavailable("u"), 503, "LLM_UPSTREAM_DOWN"),
    (LlmError("4xx — 컨텍스트 한도"), 500, "INTERNAL"),
    (RedactionBlockedError("컨텍스트 마스킹 불확실"), 500, "INTERNAL"),
]

_CLASSIFY_FAILURES = [
    (LlmTimeout("t"), 504, "TIMEOUT"),
    (LlmUnavailable("u"), 503, "LLM_UPSTREAM_DOWN"),
    (LlmError("4xx — 컨텍스트 한도"), 500, "INTERNAL"),
]

_HEADERS = {"X-Tenant-Id": "t1", "X-Request-Id": "rq-ledger-1"}
_WORKER_NOW = datetime(2026, 8, 7, tzinfo=UTC)
_GROUNDED = "지문 42개를 함께 살펴봤습니다."


def _router_request_body() -> dict[str, Any]:
    spec = importlib.util.spec_from_file_location(
        "rt", "tests/ai/integration/test_counsel_router.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    body: dict[str, Any] = module._REQUEST
    return body


def _counsel_headers() -> dict[str, str]:
    return {**_HEADERS, "Idempotency-Key": "t1:counsel:ledger"}


class _FailOnRefine:
    """초안 1회는 성공시키고 다듬기 턴부터 터진다."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def plan(self, **_kwargs: object) -> dict[str, list[str]]:
        return {}

    async def write(
        self,
        *,
        context: DraftContext,
        execution_context: ExecutionContext,
        emphasis: object = (),
        gate_feedback: str = "",
        refine_instruction: str = "",
        previous_text: str = "",
    ) -> str:
        del context, execution_context, emphasis, gate_feedback, previous_text
        if refine_instruction:
            raise self._exc
        return _GROUNDED


class _BoomProvider:
    """`LLMProvider` 대역 — 항상 터진다(분류 경로 주입용)."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    @property
    def name(self) -> str:
        return "boom"

    async def complete(self, request: LLMRequest, context: ExecutionContext) -> LLMResult:
        del request, context
        raise self._exc


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    reset_counsel_stores()
    classify_router.reset_inquiry_class_store()
    confirmations_router.reset_inquiry_class_store()
    default_llm_call_collector().reset()
    yield
    reset_counsel_stores()
    classify_router.reset_inquiry_class_store()
    confirmations_router.reset_inquiry_class_store()
    default_llm_call_collector().reset()


def _pending_calls() -> int:
    collector = default_llm_call_collector()
    return sum(len(bucket) for bucket in collector._pending.values())


def _pending_ids() -> set[UUID]:
    return set(default_llm_call_collector()._pending)


# ── A · refine ───────────────────────────────────────────────────


def _refine_once(
    exc: Exception, store: InMemoryRunStore
) -> tuple[httpx.Response, int]:
    """POST(초안) → refine(장애). **delta는 refine 턴만** 센다.

    🔴 초안 POST도 같은 `_run_store`에 AI_RUN을 쓴다 — 스냅숏을 POST **앞**에서 잡으면
    refine 턴이 안 남아도 델타가 1이라 **테스트가 틀린 이유로 통과**한다(초판에서 실제로
    그랬다). 스냅숏은 POST **뒤**에서 잡는다.
    """
    counsel_router.set_counsel_run_store(store)
    set_counsel_provider(_FailOnRefine(exc))
    headers = _counsel_headers()
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        job_id = client.post(
            "/v1/counsel/drafts", json=_router_request_body(), headers=headers
        ).json()["data"]["job_id"]
        default_llm_call_collector().reset()  # 초안 경로 수집분을 분리한다
        before = len(store.runs)
        response: httpx.Response = client.post(
            f"/v1/counsel/drafts/{job_id}/refine",
            json={"instruction": "조금 더 부드럽게", "turn_no": 1},
            headers=headers,
        )
        return response, len(store.runs) - before


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    _REFINE_FAILURES,
    ids=[type(e).__name__ for e, _s, _c in _REFINE_FAILURES],
)
def test_refine_ledger_survives_every_failure_kind(
    exc: Exception, status: int, code: str
) -> None:
    """🔴 **어떤 실패든 AI_RUN이 남는다.** 종류에 따라 갈리면 red."""
    response, delta = _refine_once(exc, InMemoryRunStore())

    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    assert delta == 1, (
        f"{type(exc).__name__}에서 refine 턴의 AI_RUN이 안 남았다(delta={delta}) — "
        "부수효과가 except 절 안에 있다"
    )


@pytest.mark.parametrize(
    "exc",
    [e for e, _s, _c in _REFINE_FAILURES],
    ids=[type(e).__name__ for e, _s, _c in _REFINE_FAILURES],
)
def test_refine_collector_is_drained_on_every_failure_kind(exc: Exception) -> None:
    """⚠ 수집기 잔존 확인이 절반이다 — 원장만 보면 `take()` 누락을 못 잡는다.

    안 부르면 레코드가 남아 **다음 실행에 섞인다**.
    """
    _refine_once(exc, InMemoryRunStore())
    assert _pending_calls() == 0, (
        f"{type(exc).__name__}에서 수집기에 호출이 남았다 — take()를 안 불렀다"
    )


# ── B · /v1/classify ─────────────────────────────────────────────


def _classify_once(
    exc: Exception, store: InMemoryRunStore, monkeypatch: pytest.MonkeyPatch, ref: str
) -> httpx.Response:
    import ai.composition.classify.provider as provider_module

    classify_router.set_classify_run_store(store)
    monkeypatch.setattr(
        provider_module, "build_classify_provider", lambda settings=None: _BoomProvider(exc)
    )
    set_counsel_provider(FakeCounselProvider())  # 앱 기동 가드(#108)
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        response: httpx.Response = client.post(
            "/v1/classify",
            json={"inquiry_ref": ref, "body_text": "이번 달 성적이 궁금합니다."},
            headers=_HEADERS,
        )
        return response


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    _CLASSIFY_FAILURES,
    ids=[type(e).__name__ for e, _s, _c in _CLASSIFY_FAILURES],
)
def test_classify_llm_failure_reaches_http_honestly(
    exc: Exception, status: int, code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 `classifier.py`가 *"LlmUnavailable·LlmTimeout은 여기서 삼키지 않는다"* 고 선언하고
    올려보내는데 **받는 쪽이 없어** 전부 500이었다 — 변환 경계 미적용.
    """
    response = _classify_once(exc, InMemoryRunStore(), monkeypatch, f"iq_{code}")
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code


@pytest.mark.parametrize(
    "exc",
    [e for e, _s, _c in _CLASSIFY_FAILURES],
    ids=[type(e).__name__ for e, _s, _c in _CLASSIFY_FAILURES],
)
def test_classify_ledger_survives_every_failure_kind(
    exc: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 `classify.py`가 *"AI_RUN은 폴백 건에도 남긴다 — 불변식 8은 판정 성공 여부와
    무관하게 '모든 실행'을 기록"* 이라고 선언해 놓고 **장애 건은 안 남겼다.**

    선언과 코드가 갈린 자리다.
    """
    store = InMemoryRunStore()
    _classify_once(exc, store, monkeypatch, f"iq_ledger_{type(exc).__name__}")
    assert len(store.runs) == 1, (
        f"{type(exc).__name__}에서 AI_RUN이 안 남았다 — 적재가 호출 뒤에만 있다"
    )


@pytest.mark.parametrize(
    "exc",
    [e for e, _s, _c in _CLASSIFY_FAILURES],
    ids=[type(e).__name__ for e, _s, _c in _CLASSIFY_FAILURES],
)
def test_classify_collector_is_drained_on_every_failure_kind(
    exc: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠ 분류가 더 나쁘다 — 파싱 재시도로 호출을 여러 번 하고 실패하므로 **실제로 쌓인다.**"""
    _classify_once(exc, InMemoryRunStore(), monkeypatch, f"iq_leak_{type(exc).__name__}")
    assert _pending_ids() == set(), f"{type(exc).__name__}에서 수집기 버킷이 남았다"


# ── 회귀 방지 — 정상 경로는 그대로 ───────────────────────────────


def test_successful_classify_still_records_and_stores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⚠ 과차단 방지 — 성공 경로의 원장·적재 순서가 안 바뀐다(AI_RUN → INQUIRY_CLASS 실 FK)."""
    store = InMemoryRunStore()
    classify_router.set_classify_run_store(store)
    set_counsel_provider(FakeCounselProvider())
    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/classify",
            json={"inquiry_ref": "iq_ok", "body_text": "성적이 궁금합니다."},
            headers=_HEADERS,
        )
    assert response.status_code == 200
    assert len(store.runs) == 1
    assert _pending_calls() == 0


# ── C · 비동기 워커 (같은 형태 4번째) ────────────────────────────
#
# 🔴 **여기가 셋 중 가장 나쁘다.** 서킷은 연속 N학생 LLM 실패 뒤 열린다 — 그 시점
# 수집기 버킷에 실제 LLM_CALL이 **최소 N건** 들어 있고 그게 통째로 방치된다
# (refine은 대개 0~1건이었다). 실측(8/7 · 임계 3 · 5학생): 버킷 내 호출 **3건**.
#
# `_run_guarded`의 5개 절이 전부 `_execute` **밖**이라, 종전에는 `ainvoke` 뒤에 있던
# 적재를 **어느 실패도 안 지났다**.


def _worker_context(ref: str) -> DraftContext:
    return DraftContext(
        student_ref=ref,
        guardian_ref="gd_1",
        label_snapshot=LabelSnapshot(
            comm=CommStyle.DATA,
            sensitivity=Sensitivity.ANXIOUS,
            interest=Interest.GRADE,
            frequency=Frequency.FREQUENT,
        ),
        facts=(EvidenceFact(label="정답률", value="62%", record_id="le_1"),),
        evidence_summaries=(),
        period_label="2026년 7월",
        fallback_text="이번 기간 학습 상황을 정리해 보내드립니다.",
    )


def _ids() -> Callable[[], UUID]:
    counter = itertools.count(1)
    return lambda: UUID(int=next(counter))


class _WorkerHarness:
    """워커 1개 + 저장소 — 원장·수집기를 주입해 관측한다."""

    def __init__(self, provider: object, *, circuit: int = 3) -> None:
        self.jobs = InMemoryJobStore()
        self.supervisor = Supervisor(
            store=self.jobs,
            lease_duration=timedelta(seconds=600),
            priority_aging_interval=timedelta(minutes=1),
            clock=lambda: _WORKER_NOW,
        )
        self.contexts = _MutatingContextStore()
        self.runs = InMemoryRunStore()
        #: ⚠ 러너와 게이트웨이가 **같은 수집기**를 써야 버킷이 안 갈린다(assembly 규약).
        self.collector = default_llm_call_collector()
        self.collector.reset()
        self.runner = CounselPackRunner(
            supervisor=self.supervisor,
            context_store=self.contexts,
            draft_store=InMemoryDraftResultStore(),
            pack_store=InMemoryPackResultStore(),
            step_sink=InMemoryAgentStepSink(),
            planner=provider,  # type: ignore[arg-type]
            writer=provider,  # type: ignore[arg-type]
            checkpointer=InMemorySaver(),
            regen_max=DEFAULT_REGEN_MAX,
            lease_owner="worker-1",
            new_id=_ids(),
            now=lambda: _WORKER_NOW,
            llm_failure_circuit=circuit,
            run_store=self.runs,
            call_log=self.collector,
        )

    async def enqueue(self, refs: list[str]) -> WorkerJob:
        return await CounselPackEnqueuer(
            supervisor=self.supervisor,
            context_store=self.contexts,
            new_id=_ids(),
            now=lambda: _WORKER_NOW,
        ).enqueue(
            tenant_id="t1",
            class_ref="cl_a1",
            contexts={ref: _worker_context(ref) for ref in refs},
        )


class _MutatingContextStore(InMemoryContextStore):
    """조회 시점에 묶음을 바꿔칠 수 있다 — 체크포인트 해시 불일치 재현용."""

    def __init__(self) -> None:
        super().__init__()
        self.mutate: Callable[[ContextBundleRecord], ContextBundleRecord] | None = None

    async def get(
        self, ref: str, *, tenant_id: str
    ) -> ContextBundleRecord | None:
        bundle = await super().get(ref, tenant_id=tenant_id)
        return self.mutate(bundle) if (bundle is not None and self.mutate) else bundle


class _GatewayThenFail:
    """게이트웨이를 거쳐 **기록을 남긴 뒤** 실패한다 — 버킷에 호출이 쌓인다."""

    def __init__(self) -> None:
        gateway = build_counsel_gateway(
            FakeCounselLlmProvider("정답률이 88%까지 올랐습니다.")
        )
        self._inner = CompositeCounselProvider(
            GatewayPlanner(gateway), GatewayDraftWriter(gateway)
        )

    async def plan(self, **_kwargs: object) -> dict[str, list[str]]:
        return {}

    async def write(self, **kwargs: object) -> str:
        await self._inner.write(**kwargs)  # type: ignore[arg-type]  # 대역 — 키워드 통과만
        raise LlmError("vendor down")


class _UnclassifiedBoom:
    """미분류 예외 — `_run_guarded`의 `except Exception` 절로 간다."""

    async def plan(self, **_kwargs: object) -> dict[str, list[str]]:
        raise RuntimeError("코드 버그")

    async def write(self, **_kwargs: object) -> str:
        return "정답률은 62%였습니다."


def _ok_provider() -> FakeCounselProvider:
    return FakeCounselProvider(drafts=["정답률은 62%였습니다."] * 40)


def _run_circuit_open() -> tuple[_WorkerHarness, WorkerJob]:
    harness = _WorkerHarness(_GatewayThenFail(), circuit=3)

    async def scenario() -> WorkerJob:
        await harness.enqueue(["st_1", "st_2", "st_3", "st_4", "st_5"])
        job = await harness.runner.run_next(tenant_id="t1")
        assert job is not None
        return job

    return harness, asyncio.run(scenario())


def _run_hash_mismatch() -> tuple[_WorkerHarness, WorkerJob]:
    harness = _WorkerHarness(_GatewayThenFail(), circuit=1)

    async def scenario() -> WorkerJob:
        await harness.enqueue(["st_1", "st_2"])
        paused = await harness.runner.run_next(tenant_id="t1")
        assert paused is not None and paused.phase is JobPhase.PAUSED
        # 재개 전에 묶음이 바뀐다 — 불변식 ④가 잡아야 하는 손상.
        harness.collector.reset()
        harness.runs.runs.clear()
        harness.contexts.mutate = lambda b: b.model_copy(
            update={"content_hash": "sha256:" + "9" * 64}
        )
        await harness.supervisor.resume(tenant_id="t1", job_id=paused.job_id)
        job = await harness.runner.run_next(tenant_id="t1")
        assert job is not None
        return job

    return harness, asyncio.run(scenario())


def _run_unclassified() -> tuple[_WorkerHarness, WorkerJob]:
    harness = _WorkerHarness(_UnclassifiedBoom())

    async def scenario() -> WorkerJob:
        await harness.enqueue(["st_1"])
        job = await harness.runner.run_next(tenant_id="t1")
        assert job is not None
        return job

    return harness, asyncio.run(scenario())


def _run_success() -> tuple[_WorkerHarness, WorkerJob]:
    harness = _WorkerHarness(_ok_provider())

    async def scenario() -> WorkerJob:
        await harness.enqueue(["st_1"])
        job = await harness.runner.run_next(tenant_id="t1")
        assert job is not None
        return job

    return harness, asyncio.run(scenario())


#: (시나리오, 기대 수렴 상태) — 🔴 **수렴 상태 단정을 빼지 마라.**
#: `swallow_errors`가 없으면 `LlmCircuitOpenError`가 `WORKER_INTERNAL`로 바뀌는데,
#: 원장 델타만 보면 그 뒤집힘(paused → failed)이 안 잡힌다.
_WORKER_SCENARIOS = [
    (_run_circuit_open, JobPhase.PAUSED),
    (_run_hash_mismatch, JobPhase.FAILED),
    (_run_unclassified, JobPhase.FAILED),
    (_run_success, JobPhase.SUCCEEDED),
]


@pytest.mark.parametrize(
    ("scenario", "expected_phase"),
    _WORKER_SCENARIOS,
    ids=["circuit_open", "hash_mismatch", "unclassified", "success"],
)
def test_worker_ledger_survives_every_convergence_path(
    scenario: Callable[[], tuple[_WorkerHarness, WorkerJob]], expected_phase: JobPhase
) -> None:
    """🔴 성공·서킷 개방·해시 불일치·미분류 — **어느 경로든 AI_RUN이 1건 남는다.**"""
    harness, job = scenario()

    assert job.phase is expected_phase, f"수렴 상태가 뒤집혔다: {job.phase.value}"
    assert len(harness.runs.runs) == 1, (
        f"{job.phase.value} 경로에서 AI_RUN이 안 남았다 — 적재가 ainvoke 뒤에만 있다"
    )


@pytest.mark.parametrize(
    ("scenario", "expected_phase"),
    _WORKER_SCENARIOS,
    ids=["circuit_open", "hash_mismatch", "unclassified", "success"],
)
def test_worker_collector_is_drained_on_every_convergence_path(
    scenario: Callable[[], tuple[_WorkerHarness, WorkerJob]], expected_phase: JobPhase
) -> None:
    """⚠ 서킷 개방 시 **최소 임계만큼** 호출이 방치됐다(실측 3건) — 여기가 가장 나쁘다."""
    del expected_phase
    harness, _job = scenario()
    residue = sum(len(bucket) for bucket in harness.collector._pending.values())
    assert residue == 0, f"수집기에 호출 {residue}건이 남았다 — take()를 안 불렀다"


@pytest.mark.parametrize(
    "removal",
    ["bundle_missing", "tenant_mismatch"],
)
def test_no_ledger_before_the_execution_starts(removal: str) -> None:
    """🔴 **여기는 원장이 안 남는 게 정상이다** — 실행 자체가 없다.

    `_execution_context(job)` 앞에서 죽는 두 경로는 `execution_id`도 LLM 호출도 없다.
    ⚠ 이 단정이 없으면 다음 사람이 *"여기도 빠졌네"* 하고 넣는다 — **실행이 없는데 실행
    기록을 만드는 것**이 된다.
    """
    harness = _WorkerHarness(_ok_provider())

    async def scenario() -> WorkerJob:
        await harness.enqueue(["st_1"])
        if removal == "bundle_missing":
            harness.contexts._rows.clear()
        else:
            harness.contexts.mutate = lambda b: b.model_copy(
                update={"tenant_id": "other"}
            )
        job = await harness.runner.run_next(tenant_id="t1")
        assert job is not None
        return job

    job = asyncio.run(scenario())
    assert job.phase is JobPhase.FAILED
    assert len(harness.runs.runs) == 0, "실행이 없었는데 실행 기록이 생겼다"
