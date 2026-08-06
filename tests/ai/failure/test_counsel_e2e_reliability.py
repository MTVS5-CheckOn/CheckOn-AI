"""3-1·3-2·3-5 재현 — counsel_pack의 종단이 끊겨 있다.

부품(게이트·state 계약·redaction)은 좋은데 파이프라인이 이어지지 않는다.

**3-1 산출물 미도착** — `graph.py:136-144`가 게이트 통과 시 `StudentResult(draft_id=…)`만
만들고 **본문 text를 버린다.** `DraftResultStore.put()` 호출은 0건이고 `DraftRecord`엔
content 필드조차 없다. 결정타: `worker.py:142`가 `succeed(result_ref=job.payload_ref)` —
**결과 참조 자리에 입력 컨텍스트 참조**를 넣어, 백엔드가 역참조하면 초안이 아니라 입력이 나온다.

**3-2 재개 불가** — `worker.py:95-118`이 항상 init(cursor=0)을 `ainvoke`에 투입한다.
같은 thread_id에 init을 다시 넣으면 처음부터 재실행(plan 재호출·완료 학생 전원 재생성·
draft_id 이중 발급)이다. `langgraph_state.md` §1.3("cursor부터 · 재생성 없음(멱등)")과 정면
충돌하고, `worker.py:100-103`의 불변식 ④ 대조는 같은 값끼리 비교하는 **자기 대조**다.

**3-5 실패 미수렴** — `Supervisor.fail()`이 있는데 워커가 한 번도 부르지 않는다. bundle
부재·해시 불일치·미분류 예외가 전부 running 방치 → lease 만료 recovery 3회(그때마다 3-2
리셋 재실행 = LLM 비용 3배) 후에야 failed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from ai.agents.job_store import InMemoryJobStore
from ai.agents.supervisor import Supervisor
from ai.composition.counsel.assembly import DEFAULT_REGEN_MAX
from ai.composition.counsel.enqueue import CounselPackEnqueuer
from ai.composition.counsel.provider import FakeCounselProvider
from ai.composition.counsel.stores import (
    ContextBundleRecord,
    CounselPackResultRecord,
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
    DraftStatus,
    EvidenceFact,
    Frequency,
    Interest,
    LabelSnapshot,
    Sensitivity,
)

_NOW = datetime(2026, 7, 30, 3, 0, tzinfo=UTC)
_DRAFT_TEXT = "정답률은 62%였습니다."


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
        period_label="2026년 7월",
        fallback_text="이번 주 학습 상황을 정리해 보내드립니다.",
    )


class _MutableContextStore:
    """테스트 전용 ContextStore — 손상·삭제·테넌트 변조를 주입한다.

    프로덕션 `InMemoryContextStore`에 테스트용 mutator를 추가하지 않기 위해 감싼다.
    """

    def __init__(self) -> None:
        self._inner = InMemoryContextStore()
        self._dropped: set[str] = set()
        self._boom = False
        self._bypass_isolation = False

    async def put(self, record: ContextBundleRecord) -> str:
        return await self._inner.put(record)

    async def get(self, ref: str, *, tenant_id: str) -> ContextBundleRecord | None:
        if self._boom:
            raise RuntimeError("저장소 예상 밖 오류")
        if ref in self._dropped:
            return None
        if self._bypass_isolation:  # 1차 방어를 뚫은 저장소를 흉내낸다
            from ai.composition.counsel.stores import CONTEXT_SCHEME, parse_ref

            return self._inner._rows.get(parse_ref(ref, CONTEXT_SCHEME))  # noqa: SLF001
        return await self._inner.get(ref, tenant_id=tenant_id)

    # ── 테스트 조작 ──
    def drop(self, ref: str) -> None:
        self._dropped.add(ref)

    def retag_tenant(self, tenant_id: str) -> None:
        """다른 테넌트의 묶음을 **격리 없이 돌려주는** 저장소를 흉내낸다.

        저장소 수준 격리(`InMemoryContextStore.get`의 tenant 필터)가 1차 방어이고, 워커의
        `bundle.tenant_id != job.tenant_id` 검사가 **이중 방어**다. 이 테스트는 1차가
        뚫린(또는 PG 구현이 필터를 빠뜨린) 상황에서 2차가 잡는지를 본다.
        """
        self._bypass_isolation = True
        self._inner._rows = {  # noqa: SLF001 — 테스트 전용 변조
            k: v.model_copy(update={"tenant_id": tenant_id})
            for k, v in self._inner._rows.items()  # noqa: SLF001
        }

    def explode(self) -> None:
        self._boom = True

    def corrupt_hash(self) -> None:
        self._inner._rows = {  # noqa: SLF001 — 체크포인트 손상 주입(불변식 ④)
            k: v.model_copy(update={"content_hash": "sha256:" + "0" * 64})
            for k, v in self._inner._rows.items()  # noqa: SLF001
        }


class _Harness:
    """공용 조립 — supervisor·저장소 3종·러너를 한 자리에서 만든다."""

    def __init__(
        self,
        *,
        provider: FakeCounselProvider | None = None,
        lease_seconds: int = 600,
    ) -> None:
        self.store = InMemoryJobStore()
        self._clock: Callable[[], datetime] = lambda: _NOW
        self.supervisor = Supervisor(
            store=self.store,
            lease_duration=timedelta(seconds=lease_seconds),
            priority_aging_interval=timedelta(minutes=1),
            clock=lambda: self._clock(),
        )
        self.contexts = _MutableContextStore()
        self.drafts = InMemoryDraftResultStore()
        self.packs = InMemoryPackResultStore()
        self.sink = InMemoryAgentStepSink()
        self.provider = provider or FakeCounselProvider(drafts=[_DRAFT_TEXT] * 40)
        #: ⚠ **인스턴스마다 새 체크포인터** — 클래스 속성으로 두면 테스트 간 체크포인트가
        #: 새서 재개 테스트가 서로를 오염시킨다(실제로 그랬다).
        self.saver: Any = InMemorySaver()
        self.runner = self._runner()

    def _runner(self) -> CounselPackRunner:
        return CounselPackRunner(
            supervisor=self.supervisor,
            context_store=self.contexts,
            draft_store=self.drafts,
            pack_store=self.packs,
            step_sink=self.sink,
            planner=self.provider,
            writer=self.provider,
            checkpointer=self.saver,
            regen_max=DEFAULT_REGEN_MAX,
            lease_owner="worker-1",
            new_id=_counter(),
        )

    async def enqueue(self, refs: list[str], *, tenant_id: str = "t1") -> WorkerJob:
        enqueuer = CounselPackEnqueuer(
            supervisor=self.supervisor,
            context_store=self.contexts,
            new_id=_counter(),
            now=lambda: _NOW,
        )
        return await enqueuer.enqueue(
            tenant_id=tenant_id,
            class_ref="cl_a1",
            contexts={r: _context(r) for r in refs},
        )


# ── 3-1: 산출물이 실제로 저장·조회된다 ────────────────────────────


def test_result_ref_is_not_the_input_ref() -> None:
    """`result_ref`가 입력 `payload_ref`를 가리키면 백엔드가 초안 대신 입력을 받는다."""
    h = _Harness()

    async def scenario() -> tuple[WorkerJob, WorkerJob | None]:
        job = await h.enqueue(["st_1", "st_2"])
        return job, await h.runner.run_next(tenant_id="t1")

    job, done = _run(scenario())
    assert done is not None and done.phase is JobPhase.SUCCEEDED
    assert done.result_ref is not None
    assert done.result_ref != job.payload_ref, "결과 참조가 입력 참조와 같다"
    assert done.result_ref.startswith("pack://")


def test_result_ref_dereferences_to_summary_and_results() -> None:
    """역참조하면 요약 + 학생별 결과가 나온다 — 04 §3.9 결과 계약."""
    h = _Harness()

    async def scenario() -> tuple[WorkerJob | None, CounselPackResultRecord | None]:
        await h.enqueue(["st_1", "st_2"])
        done = await h.runner.run_next(tenant_id="t1")
        assert done is not None and done.result_ref is not None
        return done, await h.runner.result_of(done.result_ref)

    _done, pack = _run(scenario())
    assert pack is not None
    assert pack.summary == "2명 중 2명 생성·0명 데이터 부족·0명 실패"
    assert [r.student_ref for r in pack.results] == ["st_1", "st_2"]
    assert all(r.status is DraftStatus.GENERATED for r in pack.results)


def test_draft_body_is_actually_stored_and_matches_gate_output() -> None:
    """각 `draft_id`로 **본문이 실제로 조회**되고 게이트 통과분과 일치한다."""
    h = _Harness()

    async def scenario() -> list[str]:
        await h.enqueue(["st_1", "st_2"])
        done = await h.runner.run_next(tenant_id="t1")
        assert done is not None and done.result_ref is not None
        pack = await h.runner.result_of(done.result_ref)
        assert pack is not None
        bodies: list[str] = []
        for result in pack.results:
            assert result.draft_id is not None, f"{result.student_ref} draft_id 없음"
            record = await h.drafts.get(f"draft://{result.draft_id}", tenant_id="t1")
            assert record is not None, f"{result.student_ref} 초안 본문이 저장되지 않았다"
            bodies.append(record.content)
        return bodies

    assert _run(scenario()) == [_DRAFT_TEXT, _DRAFT_TEXT]


def test_failed_student_has_no_draft_record() -> None:
    """게이트 소진 학생은 본문을 저장하지 않는다 — 저장은 **게이트 통과분만**(불변식 1)."""
    h = _Harness(provider=FakeCounselProvider(drafts=["정답률이 88%까지 올랐습니다."] * 40))

    async def scenario() -> CounselPackResultRecord | None:
        await h.enqueue(["st_1"])
        done = await h.runner.run_next(tenant_id="t1")
        assert done is not None and done.result_ref is not None
        return await h.runner.result_of(done.result_ref)

    pack = _run(scenario())
    assert pack is not None
    assert pack.results[0].status is DraftStatus.FAILED
    assert pack.results[0].draft_id is None


# ── 3-2: 재개가 진짜로 동작한다 (워커 수준) ───────────────────────


def test_resume_only_processes_remaining_students() -> None:
    """k명 처리 후 죽고 재lease → **잔여 학생 수만큼만** LLM 호출이 늘어난다."""
    refs = ["st_1", "st_2", "st_3", "st_4"]
    killer = _KillAfter(FakeCounselProvider(drafts=[_DRAFT_TEXT] * 40), after=2)
    h = _Harness(provider=killer, lease_seconds=1)  # type: ignore[arg-type]

    async def scenario() -> tuple[int, int, CounselPackResultRecord | None]:
        await h.enqueue(refs)
        # ① 2명까지 처리하고 워커가 급사한다.
        with pytest.raises(_WorkerKilled):
            await h.runner.run_next(tenant_id="t1")
        after_first = len(killer.write_calls)

        # ② lease 만료 → recovery → 재lease. 같은 thread_id로 재개된다.
        killer.armed = False
        h._clock = lambda: _NOW + timedelta(seconds=120)  # lease 만료 유도
        done = await h.runner.run_next(tenant_id="t1")
        assert done is not None and done.result_ref is not None
        return after_first, len(killer.write_calls), await h.runner.result_of(
            done.result_ref
        )

    first, total, pack = _run(scenario())
    assert first == 2, f"중단 시점 호출 수가 2가 아니다: {first}"
    assert total == len(refs), f"재개가 완료 학생을 재생성했다: {total} (기대 {len(refs)})"
    assert pack is not None
    assert len(pack.results) == len(refs)


def test_resume_does_not_reissue_draft_ids() -> None:
    """완료 학생의 draft_id가 재발급되지 않는다 — 커밋 1의 store로 검증 가능해졌다."""
    refs = ["st_1", "st_2", "st_3"]
    killer = _KillAfter(FakeCounselProvider(drafts=[_DRAFT_TEXT] * 40), after=2)
    h = _Harness(provider=killer, lease_seconds=1)  # type: ignore[arg-type]

    async def scenario() -> tuple[list[UUID], list[UUID]]:
        await h.enqueue(refs)
        with pytest.raises(_WorkerKilled):
            await h.runner.run_next(tenant_id="t1")
        before = sorted(h.drafts.ids())

        killer.armed = False
        h._clock = lambda: _NOW + timedelta(seconds=120)  # lease 만료 유도
        await h.runner.run_next(tenant_id="t1")
        return before, sorted(h.drafts.ids())

    before, after = _run(scenario())
    assert len(before) == 2
    assert before == after[: len(before)], "완료 학생의 draft_id가 바뀌었다(이중 발급)"
    assert len(after) == len(refs)


def test_resume_does_not_recall_plan() -> None:
    """plan은 멱등 — 재개 시 다시 호출되지 않는다(§1.3)."""
    refs = ["st_1", "st_2", "st_3"]
    killer = _KillAfter(FakeCounselProvider(drafts=[_DRAFT_TEXT] * 40), after=2)
    h = _Harness(provider=killer, lease_seconds=1)  # type: ignore[arg-type]

    async def scenario() -> int:
        await h.enqueue(refs)
        with pytest.raises(_WorkerKilled):
            await h.runner.run_next(tenant_id="t1")
        killer.armed = False
        h._clock = lambda: _NOW + timedelta(seconds=120)  # lease 만료 유도
        await h.runner.run_next(tenant_id="t1")
        return len(killer.plan_calls)

    assert _run(scenario()) == 1, "재개가 plan을 다시 호출했다"


class _WorkerKilled(BaseException):
    """워커 프로세스 급사 — **`BaseException`이다.**

    진짜 프로세스 kill은 `Exception`이 아니므로(SystemExit·KeyboardInterrupt와 같은 결)
    커밋 3의 `except Exception` 수렴 핸들러에 잡히지 않는다. 잡은 running 상태로 남고
    lease 만료 → recovery → 재lease로 이어진다 — 재개 경로를 정확히 재현한다.
    """


class _KillAfter:
    """k명 성공 후 급사하는 provider 래퍼 — 프로덕션에 테스트 훅을 넣지 않기 위해.

    `write_calls`를 그대로 위임해 호출 수 검증이 유지된다.
    """

    def __init__(self, inner: FakeCounselProvider, *, after: int) -> None:
        self._inner = inner
        self._after = after
        self.armed = True

    @property
    def write_calls(self) -> list[str]:
        return self._inner.write_calls

    @property
    def plan_calls(self) -> list[tuple[str, ...]]:
        return self._inner.plan_calls

    async def plan(self, **kwargs: object) -> dict[str, list[str]]:
        return await self._inner.plan(**kwargs)  # type: ignore[arg-type]

    async def write(self, **kwargs: object) -> str:
        if self.armed and len(self._inner.write_calls) >= self._after:
            raise _WorkerKilled("kill")
        return await self._inner.write(**kwargs)  # type: ignore[arg-type]


# ── 3-5: 실패가 즉시 수렴한다 ─────────────────────────────────────


def test_missing_bundle_converges_to_failed() -> None:
    """bundle 역참조 실패 → 즉시 failed(recovery 3회를 기다리지 않는다)."""
    h = _Harness()

    async def scenario() -> WorkerJob | None:
        job = await h.enqueue(["st_1"])
        h.contexts.drop(job.payload_ref)  # 저장소에서 사라진 상황
        return await h.runner.run_next(tenant_id="t1")

    done = _run(scenario())
    assert done is not None
    assert done.phase is JobPhase.FAILED
    assert done.error_code == "context_bundle_missing"


def test_tenant_mismatch_converges_to_failed() -> None:
    """bundle의 tenant가 잡의 tenant와 다르면 즉시 failed — 워커의 **이중 방어**.

    1차는 저장소 수준 필터(`ContextStore.get(..., tenant_id=)`)라 정상 경로에서는 애초에
    해소되지 않는다(그 경우 `context_bundle_missing`). 이 테스트는 1차가 뚫린 상황을
    주입해 2차가 잡는지를 본다.
    """
    h = _Harness()

    async def scenario() -> WorkerJob | None:
        await h.enqueue(["st_1"], tenant_id="t1")
        h.contexts.retag_tenant("t_other")  # 격리 위반 상황을 주입
        return await h.runner.run_next(tenant_id="t1")

    done = _run(scenario())
    assert done is not None
    assert done.phase is JobPhase.FAILED
    assert done.error_code == "tenant_mismatch"


def test_hash_mismatch_converges_to_failed() -> None:
    """재개 시 해시 불일치 = 체크포인트 손상 → failed(불변식 ④ 실검증)."""
    refs = ["st_1", "st_2", "st_3"]
    killer = _KillAfter(FakeCounselProvider(drafts=[_DRAFT_TEXT] * 40), after=1)
    h = _Harness(provider=killer, lease_seconds=1)  # type: ignore[arg-type]

    async def scenario() -> WorkerJob | None:
        await h.enqueue(refs)
        with pytest.raises(_WorkerKilled):
            await h.runner.run_next(tenant_id="t1")
        h.contexts.corrupt_hash()  # 같은 ref인데 내용 해시가 달라졌다
        killer.armed = False
        h._clock = lambda: _NOW + timedelta(seconds=120)  # lease 만료 유도
        return await h.runner.run_next(tenant_id="t1")

    done = _run(scenario())
    assert done is not None
    assert done.phase is JobPhase.FAILED
    assert done.error_code == "context_hash_mismatch"


def test_unclassified_exception_converges_to_failed() -> None:
    """미분류 예외도 running 방치가 아니라 failed로 수렴한다."""
    h = _Harness()

    async def scenario() -> WorkerJob | None:
        await h.enqueue(["st_1"])
        h.contexts.explode()  # 저장소가 예상 밖 예외를 던진다
        return await h.runner.run_next(tenant_id="t1")

    done = _run(scenario())
    assert done is not None
    assert done.phase is JobPhase.FAILED
    assert done.error_code == "worker_internal_error"


def test_llm_circuit_pauses_after_consecutive_failures() -> None:
    """§1.3 서킷 — 연속 LLM 실패 3학생이면 paused. LLM 호출이 거기서 멈춘다(불변식 6)."""
    from ai.contracts.llm import LlmUnavailable

    refs = [f"st_{i}" for i in range(1, 9)]
    h = _Harness(provider=FakeCounselProvider(drafts=[LlmUnavailable("down")] * 40))

    async def scenario() -> tuple[WorkerJob | None, int]:
        await h.enqueue(refs)
        done = await h.runner.run_next(tenant_id="t1")
        return done, len(h.provider.write_calls)

    done, calls = _run(scenario())
    assert done is not None
    assert done.phase is JobPhase.PAUSED, "전면 장애인데 22명을 다 던졌다"
    assert calls == 3, f"서킷이 임계에서 멈추지 않았다: {calls}"


def test_non_ok_outcome_is_llm_failed_not_gate_exhausted() -> None:
    """3-12 — outcome≠OK·text=None이면 ""가 게이트로 가 장애가 오분류된다."""
    from ai.composition.counsel.provider import GatewayDraftWriter
    from ai.contracts.execution import Capability, ExecutionContext, VersionSet
    from ai.contracts.llm import (
        CallOutcome,
        LlmError,
        LLMRequest,
        LLMResult,
        TokenUsage,
    )

    class _BadGateway:
        async def complete(
            self, request: LLMRequest, context: ExecutionContext
        ) -> LLMResult:
            return LLMResult(
                outcome=CallOutcome.PROVIDER_ERROR,
                text=None,
                provider="fake",
                model="m",
                usage=TokenUsage(tokens_in=0, tokens_out=0, cost_usd=0.0),
                latency_ms=1,
            )

    writer = GatewayDraftWriter(_BadGateway())  # type: ignore[arg-type]
    ctx = ExecutionContext(
        execution_id=UUID("00000000-0000-4000-8000-0000000000d1"),
        tenant_id="t1",
        capability=Capability.COMPOSITION,
        input_snapshot_hash="h",
        versions=VersionSet(
            pipeline_version="0.1",
            engine_version="0.1",
            schema_version="0.1",
            contract_version="0.1",
        ),
    )
    with pytest.raises(LlmError):
        _run(writer.write(context=_context("st_1"), execution_context=ctx))
