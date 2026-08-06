"""🔴 ㉦ — counsel **서비스 경로**의 재개는 죽은 코드다(인메모리 백엔드).

⑰이 재개를 지었고 `test_counsel_e2e_reliability.py`가 그걸 **초록으로** 지킨다. 그런데 그
테스트는 `InMemoryJobStore()`와 `InMemorySaver()`를 **손으로 하나씩 만들어 공유**한다 —
서비스 경로는 그렇지 않다:

    counsel.py:395   `_build_supervisor()` → `build_agent_job_store()`  ← 호출마다 새 인스턴스
    assembly.py:100  `_open_saver`         → `InMemorySaver()`          ← 컨텍스트마다 새 인스턴스

⇒ **요청 하나보다 오래 사는 것이 없다.** 잡 원장과 체크포인트가 응답과 함께 소멸하므로,
아래가 전부 **한 번도 실행된 적이 없다**:

  ⓐ `_resume_input`의 `aget_state` 재개 분기(`stored["cursor"] is not None`)
  ⓑ 그 분기 안의 **불변식 ④ `context_hash` 대조**(체크포인트 손상 검출)
  ⓒ `paused` → `resume()` → 재lease
  ⓓ `run_next`의 lease 만료 recovery
  ⓔ ㉩ `_views` 갱신 — 잡이 나중에 끝나도 GET이 볼 원본이 없다

🔴 **선례가 이미 있다.** `build_inquiry_class_store`(㉫)가 같은 결함을 닫으면서
*"인메모리도 프로세스 공용 1개다"* 라고 적어 뒀다. 같은 형태로 간다.

⚠ **PG 백엔드는 원래 산다** — 저장소가 외부라 인스턴스 수명과 무관하다. 이 파일이 고정하는
것은 **memory 분기**의 수명이고, PG 분기의 동작은 건드리지 않는다(마지막 테스트가 그
비대칭을 못 박는다 — 커넥션을 싱글턴으로 만들면 안 된다).
"""

from __future__ import annotations

import asyncio
from collections.abc import (
    AsyncIterator,
    Callable,
    Coroutine,
    Iterator,
    Mapping,
    Sequence,
)
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from langchain_core.runnables import RunnableConfig

import ai.composition.counsel.assembly as assembly
from ai.agents.supervisor import Supervisor
from ai.api.routers import counsel as counsel_router
from ai.api.routers.counsel import reset_counsel_stores
from ai.composition.counsel.assembly import (
    DEFAULT_REGEN_MAX,
    _open_saver,
    open_counsel_pack_runner,
)
from ai.composition.counsel.enqueue import CounselPackEnqueuer
from ai.composition.counsel.provider import FakeCounselProvider
from ai.composition.counsel.state import CounselPackState
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
from ai.contracts.llm import LlmUnavailable
from ai.db.settings import DbSettings
from ai.db.store_factory import build_agent_job_store

_NOW = datetime(2026, 8, 7, 3, 0, tzinfo=UTC)
_DRAFT_TEXT = "정답률은 62%였습니다."
_CIRCUIT = 3


def _run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


def _counter() -> Callable[[], UUID]:
    box = {"n": 0}

    def _next() -> UUID:
        box["n"] += 1
        return UUID(int=box["n"])

    return _next


def _context(ref: str) -> DraftContext:
    return DraftContext(
        student_ref=ref,
        guardian_ref=f"gd_{ref}",
        label_snapshot=LabelSnapshot(
            comm=CommStyle.DATA,
            sensitivity=Sensitivity.ANXIOUS,
            interest=Interest.GRADE,
            frequency=Frequency.FREQUENT,
        ),
        facts=(EvidenceFact(label="이번 주 정답률", value="62%"),),
        evidence_summaries=(),
        period_label="2026년 8월",
        fallback_text="이번 주 학습 상황을 정리해 보내드립니다.",
    )


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    reset_counsel_stores()
    yield
    reset_counsel_stores()


# ── 수명: 팩토리가 요청 사이에 같은 것을 돌려주는가 ────────────────


def test_agent_job_store_is_shared_across_requests() -> None:
    """🔴 잡 원장이 **프로세스 공용 1개**여야 한다 — `build_inquiry_class_store`(㉫)와 같다.

    호출마다 새로 만들면 POST가 적재한 잡을 다음 요청이 **못 본다**. paused 잡은 재개할
    주체가 없고, GET은 갱신할 원본이 없다(㉩). 종전엔 두 요청이 서로 다른 큐를 봤다.
    """
    first, second = build_agent_job_store(), build_agent_job_store()
    assert first is second, (
        "build_agent_job_store()가 호출마다 새 인스턴스를 준다 — 잡 원장이 요청과 함께 "
        "소멸해 paused→재개·GET 갱신이 전부 불가능하다(㉦)"
    )


def test_memory_checkpointer_is_shared_across_requests() -> None:
    """🔴 인메모리 체크포인터도 프로세스 공용이어야 한다 — 아니면 재개할 state가 없다."""

    async def scenario() -> tuple[Any, Any]:
        settings = DbSettings(store_backend="memory")
        async with _open_saver(settings) as first, _open_saver(settings) as second:
            return first, second

    first, second = _run(scenario())
    assert first is second, (
        "_open_saver가 memory 분기에서 매번 새 InMemorySaver를 만든다 — 체크포인트가 "
        "요청과 함께 소멸해 _resume_input의 aget_state 분기가 영원히 안 돈다(㉦)"
    )


def test_the_memory_checkpointer_is_not_closed_on_exit() -> None:
    """⚠ 싱글턴을 `finally`에서 닫아 버리면 **두 번째 요청이 죽는다** — 가장 틀리기 쉬운 곳.

    PG 분기는 커넥션이라 컨텍스트 종료 시 닫혀야 하고, memory 분기는 프로세스 공용이라
    닫으면 안 된다. **이 비대칭이 이 수정의 핵심**이라 컨텍스트를 빠져나온 뒤 실제로
    써 본다(존재 확인이 아니라 동작 확인).
    """

    async def scenario() -> object | None:
        settings = DbSettings(store_backend="memory")
        async with _open_saver(settings) as saver:
            pass
        config: RunnableConfig = {"configurable": {"thread_id": "t-after-exit"}}
        return await saver.aget_tuple(config)  # 닫혔으면 여기서 터진다

    assert _run(scenario()) is None  # 저장된 게 없으니 None이 정상 — 예외가 안 나는 게 요점


# ── 서비스 경로: 두 요청이 같은 큐를 보는가 ────────────────────────


def test_two_requests_see_the_same_job_queue() -> None:
    """🔴 `_build_supervisor()`를 두 번 부른 두 요청이 **같은 잡**을 본다.

    라우터는 요청마다 supervisor를 새로 만든다(`counsel.py:392`). 그 자체는 문제가 아니지만
    **뒤에 있는 저장소가 같아야** 한다 — 아니면 1번 요청이 넣은 잡을 2번 요청이 못 찾는다.
    """
    contexts = InMemoryContextStore()

    async def scenario() -> tuple[UUID, WorkerJob | None]:
        first_sv = counsel_router._build_supervisor()  # noqa: SLF001 — 서비스 경로 그대로
        job = await CounselPackEnqueuer(
            supervisor=first_sv, context_store=contexts, now=lambda: _NOW
        ).enqueue(
            tenant_id="t1", class_ref="cl_a1", contexts={"st_1": _context("st_1")}
        )
        second_sv = counsel_router._build_supervisor()  # noqa: SLF001 — 다음 요청
        return job.job_id, await second_sv.get(tenant_id="t1", job_id=job.job_id)

    job_id, seen = _run(scenario())
    assert seen is not None and seen.job_id == job_id, (
        "다음 요청의 supervisor가 앞 요청이 넣은 잡을 못 본다 — 요청마다 다른 큐다(㉦)"
    )


# ── 종단: 되살아난 재개 경로를 실제로 태운다 ───────────────────────


class _ServiceHarness:
    """🔴 **서비스 경로 그대로** 조립한다 — 손으로 공유 인스턴스를 만들지 않는다.

    이게 요점이다. `test_counsel_e2e_reliability.py`의 `_Harness`는 `InMemoryJobStore()`와
    `InMemorySaver()`를 스스로 하나씩 만들어 나눠 쓰므로 **팩토리 수명 결함이 안 보인다** —
    ⑰의 재개 테스트가 6주간 초록이었던 이유가 그것이다. 여기서는 라우터가 부르는 것과
    **같은 함수**(`_build_supervisor` · `open_counsel_pack_runner`)만 부른다.
    """

    def __init__(self, provider: _FailThenRecover) -> None:
        self.provider = provider
        #: 라우터의 모듈 전역 저장소와 같은 자리 — 이쪽은 이미 요청 사이에 산다.
        self.contexts = InMemoryContextStore()
        self.drafts = InMemoryDraftResultStore()
        self.packs = InMemoryPackResultStore()
        self.sink = InMemoryAgentStepSink()

    def supervisor(self) -> Supervisor:
        """요청마다 새로 만든다 — 라우터가 하는 그대로(`counsel.py:392`)."""
        return counsel_router._build_supervisor()  # noqa: SLF001 — 서비스 경로 그대로

    async def enqueue(self, refs: list[str]) -> WorkerJob:
        return await CounselPackEnqueuer(
            supervisor=self.supervisor(),
            context_store=self.contexts,
            new_id=_counter(),
            now=lambda: _NOW,
        ).enqueue(
            tenant_id="t1",
            class_ref="cl_a1",
            contexts={ref: _context(ref) for ref in refs},
        )

    async def run_once(self) -> WorkerJob | None:
        """요청 1회 = 러너 1회. 컨텍스트를 빠져나오는 것까지 포함한다."""
        async with open_counsel_pack_runner(
            supervisor=self.supervisor(),
            context_store=self.contexts,
            step_sink=self.sink,
            draft_store=self.drafts,
            pack_store=self.packs,
            planner=self.provider,
            writer=self.provider,
            regen_max=DEFAULT_REGEN_MAX,
            lease_owner="counsel-router",
        ) as runner:
            return await runner.run_next(tenant_id="t1")


class _FailThenRecover:
    """앞 `fail_for`명은 LLM 장애, 그 뒤로는 정상 — 서킷 개방 후 회복을 재현한다.

    `CounselPlanner`·`DraftWriter` 양쪽 Protocol을 만족한다(라우터가 요구하는 그대로).
    """

    def __init__(self, fail_for: int) -> None:
        self._inner = FakeCounselProvider(drafts=[_DRAFT_TEXT] * 60)
        self._fail_for = fail_for
        self.write_calls: list[str] = []

    async def plan(
        self,
        *,
        contexts: Mapping[str, DraftContext],
        student_refs: Sequence[str],
        execution_context: ExecutionContext,
    ) -> dict[str, list[str]]:
        return await self._inner.plan(
            contexts=contexts,
            student_refs=student_refs,
            execution_context=execution_context,
        )

    async def write(
        self,
        *,
        context: DraftContext,
        execution_context: ExecutionContext,
        emphasis: Sequence[str] = (),
        gate_feedback: str = "",
        refine_instruction: str = "",
        previous_text: str = "",
    ) -> str:
        self.write_calls.append(context.student_ref)
        if len(self.write_calls) <= self._fail_for:
            raise LlmUnavailable("업스트림 장애")
        return await self._inner.write(
            context=context,
            execution_context=execution_context,
            emphasis=emphasis,
            gate_feedback=gate_feedback,
            refine_instruction=refine_instruction,
            previous_text=previous_text,
        )


def test_a_paused_job_survives_the_request_that_paused_it() -> None:
    """🔴 서킷 개방으로 `paused`가 된 잡이 **다음 요청에서 보인다**.

    지금은 그 잡이 응답과 함께 사라진다 — 강사가 다시 눌러도 재개가 아니라 **처음부터**이고
    (완료 학생 재생성 · LLM 비용 2배 · `draft_id` 이중 발급), 그전에 애초에 **잡을 찾을 수가
    없다**. ⑰이 지은 `resume()`은 부를 대상이 없어 죽은 코드다.
    """
    refs = [f"st_{i}" for i in range(1, 9)]
    harness = _ServiceHarness(_FailThenRecover(fail_for=_CIRCUIT))

    async def scenario() -> tuple[WorkerJob | None, WorkerJob | None]:
        job = await harness.enqueue(refs)
        paused = await harness.run_once()
        # 다음 요청 — 라우터가 하는 그대로 supervisor를 새로 만들어 조회한다.
        seen = await harness.supervisor().get(tenant_id="t1", job_id=job.job_id)
        return paused, seen

    paused, seen = _run(scenario())
    assert paused is not None and paused.phase is JobPhase.PAUSED
    assert seen is not None, (
        "paused 잡이 다음 요청에서 사라졌다 — 재개할 대상이 없으므로 Supervisor.resume()과 "
        "worker의 재개 분기가 통째로 죽은 코드다(㉦)"
    )
    assert seen.phase is JobPhase.PAUSED


def test_resume_after_pause_does_not_regenerate_completed_students() -> None:
    """🔴 **되살아난 경로를 실제로 태운다** — 재개가 완료 학생을 다시 만들지 않는다.

    ⚠ **LLM 호출 수로 증명한다.** 상태 문자열만 보면 헛돈다 — `succeeded`는 처음부터
    다시 돌아도 나온다(#119·#120에서 두 번 겪었다). 서킷이 열린 3명은 실패로 확정됐고
    나머지 5명이 남으므로, 재개 후 **추가 호출은 정확히 5건**이어야 한다.

    이 단정이 통과하는 순간이 ⑰의 재개가 **인메모리에서 처음 실제로 도는** 순간이다.
    """
    refs = [f"st_{i}" for i in range(1, 9)]
    provider = _FailThenRecover(fail_for=_CIRCUIT)
    harness = _ServiceHarness(provider)

    async def scenario() -> tuple[int, int, WorkerJob | None]:
        job = await harness.enqueue(refs)
        paused = await harness.run_once()
        assert paused is not None and paused.phase is JobPhase.PAUSED
        after_pause = len(provider.write_calls)

        # 강사·운영이 재개를 지시한다 — paused → queued.
        await harness.supervisor().resume(tenant_id="t1", job_id=job.job_id)
        done = await harness.run_once()
        return after_pause, len(provider.write_calls), done

    after_pause, total, done = _run(scenario())
    assert after_pause == _CIRCUIT, f"서킷이 임계에서 안 멈췄다: {after_pause}"
    assert done is not None, "재개할 잡을 lease하지 못했다 — 큐가 요청과 함께 죽었다(㉦)"
    assert done.phase is JobPhase.SUCCEEDED
    assert total == len(refs), (
        f"재개가 완료 학생을 재생성했다: 총 {total}회 호출(기대 {len(refs)}) — "
        "체크포인트가 요청과 함께 소멸해 cursor=0부터 다시 돌았다(㉦)"
    )


def test_the_resume_branch_verifies_the_context_hash() -> None:
    """🔴 불변식 ④ 대조가 **서비스 경로에서 실제로 돈다** — 손상되면 failed다.

    ⚠ 이 대조는 `_resume_input`의 **재개 분기 안에만** 있다. 재개가 죽은 코드였으므로
    불변식 ④도 서비스 경로에서는 한 번도 실행된 적이 없었다 — 체크포인트가 손상돼도
    아무도 안 봤다는 뜻이다(사실은 체크포인트가 남지도 않았다).
    """
    refs = [f"st_{i}" for i in range(1, 9)]
    harness = _ServiceHarness(_FailThenRecover(fail_for=_CIRCUIT))

    async def scenario() -> WorkerJob | None:
        job = await harness.enqueue(refs)
        await harness.run_once()
        await harness.supervisor().resume(tenant_id="t1", job_id=job.job_id)
        # 체크포인트 손상 주입 — 묶음이 재개 사이에 바뀌었다.
        harness.contexts._rows = {  # noqa: SLF001 — 테스트 전용 변조
            key: record.model_copy(update={"content_hash": "sha256:" + "0" * 64})
            for key, record in harness.contexts._rows.items()  # noqa: SLF001
        }
        return await harness.run_once()

    done = _run(scenario())
    assert done is not None
    assert done.phase is JobPhase.FAILED
    assert done.error_code == "context_hash_mismatch", (
        "재개 분기의 불변식 ④ 대조가 돌지 않았다 — 손상된 체크포인트로 계속 돌았다"
    )


# ── PG 분기는 건드리지 않는다 ──────────────────────────────────────


def test_pg_checkpointer_is_still_request_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⚠ PG 분기는 **요청 스코프 그대로**다 — 커넥션을 싱글턴으로 만들면 안 된다.

    memory 싱글턴과 정반대라 헷갈리기 쉽다. PG는 저장소가 외부라 **인스턴스를 공유할 이유가
    없고**, 공유하면 커넥션 수명·풀·트랜잭션 경계가 요청과 어긋난다. 실제 접속 없이
    **분기 자체**를 본다(`open_checkpointer`가 불리는지).
    """
    calls: list[str] = []

    class _FakeSaver:
        """실제 접속 없이 **분기만** 본다 — PG 커넥션을 테스트가 열지 않는다."""

    @asynccontextmanager
    async def _fake_open(settings: DbSettings) -> AsyncIterator[_FakeSaver]:
        calls.append("open")
        yield _FakeSaver()

    monkeypatch.setattr(assembly, "open_checkpointer", _fake_open)

    async def scenario() -> tuple[object, object]:
        settings = DbSettings(store_backend="pg")
        async with _open_saver(settings) as first:
            pass
        async with _open_saver(settings) as second:
            pass
        return first, second

    first, second = _run(scenario())
    assert calls == ["open", "open"], "PG 분기가 open_checkpointer를 안 탔다"
    assert first is not second, (
        "PG 체크포인터가 싱글턴이 됐다 — 커넥션 수명이 요청 경계와 어긋난다. "
        "memory만 프로세스 공용이다(비대칭이 의도다)"
    )


def test_pg_job_store_is_still_built_per_call() -> None:
    """⚠ PG 잡 원장도 호출마다 만든다 — 세션메이커가 원본이라 이미 공유 상태다.

    `build_inquiry_class_store`(㉫)가 *"PG 백엔드에는 영향이 없다"* 라고 적어 둔 것과 같다.
    """
    settings = DbSettings(store_backend="pg")
    first = build_agent_job_store(settings)
    second = build_agent_job_store(settings)
    assert first is not second
    assert type(first).__name__ == "PgJobStore"


# ── 죽어 있던 경로 전수 (리포트의 목록) ────────────────────────────


def test_the_runner_resume_input_has_a_reachable_resume_branch() -> None:
    """⚠ `_resume_input`의 재개 분기가 **도달 가능**해졌음을 소스가 아니라 호출로 본다.

    위 테스트들이 이미 태우지만, 여기서는 **분기 자체**를 직접 확인한다 — 저장된 state가
    있는 config로 부르면 `None`(=재개 신호)이 나와야 한다. 종전에는 체크포인터가 매번
    새것이라 이 함수가 **항상 init state를 반환**했다.
    """
    refs = ["st_1", "st_2", "st_3", "st_4"]
    provider = _FailThenRecover(fail_for=_CIRCUIT)
    harness = _ServiceHarness(provider)

    async def scenario() -> CounselPackState | None:
        job = await harness.enqueue(refs)
        await harness.run_once()
        await harness.supervisor().resume(tenant_id="t1", job_id=job.job_id)
        bundle = await harness.contexts.get(job.payload_ref, tenant_id="t1")
        assert bundle is not None
        async with open_counsel_pack_runner(
            supervisor=harness.supervisor(),
            context_store=harness.contexts,
            step_sink=harness.sink,
            draft_store=harness.drafts,
            pack_store=harness.packs,
            planner=provider,
            writer=provider,
            regen_max=DEFAULT_REGEN_MAX,
            lease_owner="counsel-router",
        ) as runner:
            graph = _graph_for(runner, job, bundle)
            config: dict[str, Any] = {
                "configurable": {"thread_id": str(job.job_id)}
            }
            return await runner._resume_input(graph, config, job, bundle)  # noqa: SLF001

    assert _run(scenario()) is None, (
        "저장된 체크포인트가 있는데 _resume_input이 init state를 만들었다 — 재개 분기가 "
        "여전히 도달 불가다(cursor부터가 아니라 처음부터 재실행)"
    )


def _graph_for(
    runner: CounselPackRunner, job: WorkerJob, bundle: ContextBundleRecord
) -> Any:  # noqa: ANN401 — `build_counsel_graph`와 같은 이유(버전별 제네릭)
    """`_resume_input`이 받는 그래프 — `aget_state`만 쓰므로 최소 조립으로 충분하다."""
    from ai.composition.counsel.graph import build_counsel_graph
    from ai.composition.counsel.worker import _execution_context

    return build_counsel_graph(
        planner=runner._planner,  # noqa: SLF001
        writer=runner._writer,  # noqa: SLF001
        contexts=bundle.contexts,
        execution_context=_execution_context(job),
        checkpointer=runner._checkpointer,  # noqa: SLF001
        call_log=runner._call_log,  # noqa: SLF001
        student_call_ids={},
        regen_max=DEFAULT_REGEN_MAX,
        llm_failure_circuit=_CIRCUIT,
        draft_store=runner._drafts,  # noqa: SLF001
        tenant_id=job.tenant_id,
        agent_run_id=job.job_id,
        new_draft_id=_counter(),
        now=lambda: _NOW,
    )


def test_lease_recovery_still_works_across_requests() -> None:
    """⚠ lease 만료 recovery도 **요청 사이**에 걸쳐야 한다 — 큐가 살아야 성립한다.

    워커가 급사해 running으로 방치된 잡을, **다음 요청**의 `lease_next`가 회수한다. 큐가
    요청과 함께 죽으면 회수할 대상 자체가 없어 이 경로도 죽은 코드였다.
    """
    settings_lease = timedelta(seconds=1)
    harness = _ServiceHarness(_FailThenRecover(fail_for=0))

    async def scenario() -> tuple[WorkerJob | None, JobPhase]:
        job = await harness.enqueue(["st_1", "st_2"])
        store = build_agent_job_store()
        # 요청 ①: lease만 하고 죽는다(running 방치).
        leased = await Supervisor(
            store=store,
            lease_duration=settings_lease,
            priority_aging_interval=timedelta(minutes=1),
            clock=lambda: _NOW,
        ).lease_next(tenant_id="t1", worker_kind=job.worker_kind, lease_owner="dead")
        assert leased is not None
        # 요청 ②: lease가 만료된 뒤 다음 워커가 회수해 간다.
        later = _NOW + timedelta(seconds=120)
        recovered = await Supervisor(
            store=build_agent_job_store(),
            lease_duration=settings_lease,
            priority_aging_interval=timedelta(minutes=1),
            clock=lambda: later,
        ).lease_next(tenant_id="t1", worker_kind=job.worker_kind, lease_owner="alive")
        return recovered, leased.phase

    recovered, leased_phase = _run(scenario())
    assert leased_phase is JobPhase.LEASED
    assert recovered is not None, (
        "다음 요청이 만료 lease를 회수하지 못했다 — 큐가 요청과 함께 죽어 recovery 경로가 "
        "돌 대상을 잃는다(㉦)"
    )
