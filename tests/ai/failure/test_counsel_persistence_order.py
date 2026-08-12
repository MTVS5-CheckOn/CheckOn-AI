"""상담 영속의 **순서와 멱등** — 저장이 실패하면 무엇이 앞서가면 안 되는가 (㉻ · 지시서 73 §7).

본문이 PG에 앉기 시작하면서 **순서가 계약**이 됐다:

    ContextBundle 저장 → enqueue → start → AI_RUN begin → 게이트 통과
      → DRAFT 저장 → 학생 경계 checkpoint → AI_RUN finalize → PackResult → succeed

🔴 **각 화살표가 뒤집히면 서로 다른 거짓이 생긴다:**

| 뒤집힘 | 생기는 거짓 |
| --- | --- |
| 묶음 저장 뒤에 enqueue → 앞으로 | 워커가 못 찾는 잡이 큐에 선다 |
| DRAFT 저장 뒤에 checkpoint → 앞으로 | 재개가 그 학생을 **끝난 것으로 건너뛴다**(본문 없음) |
| `begin_run` 뒤에 DRAFT → 앞으로 | FK 부모가 없어 23503 |

⚠ **인메모리 저장소로 잰다** — 축은 «순서»이지 «PG»가 아니다. 두 구현의 멱등·충돌 의미는
이제 같다(`InMemoryContextStore`·`InMemoryDraftResultStore`가 PG와 같은 예외를 낸다).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import UUID, uuid4

import pytest

# ⚠ 같은 디렉터리의 하네스를 재사용한다 — 워커 조립을 또 복제하면 한쪽만 낡는다(99 #02).
from test_ledger_survives_every_failure import (  # type: ignore[import-not-found]
    _WorkerHarness,
    _ok_provider,
)

from ai.composition.counsel.stores import (
    DRAFT_SCHEME,
    ContextBundleRecord,
    DraftRecord,
    InMemoryContextStore,
    InMemoryDraftResultStore,
    make_ref,
)
from ai.contracts.agents import JobPhase, WorkerJob, WorkerKind
from ai.contracts.llm import LlmError
from ai.db.repositories.counsel_context_store import ContextBundleConflict
from ai.db.repositories.counsel_draft_store import DraftRecordConflict

_NOW: Final = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)


class _CountingDrafts(InMemoryDraftResultStore):
    """저장 호출 수를 센다 — **행 수로는 재저장을 못 본다**(멱등이라 안 는다)."""

    def __init__(self) -> None:
        super().__init__()
        self.puts = 0
        self.per_student: list[str] = []

    async def put(self, record: DraftRecord) -> str:
        self.puts += 1
        self.per_student.append(record.student_ref)
        return await super().put(record)


class _FailingDrafts(InMemoryDraftResultStore):
    """지정 학생의 저장만 터진다 — 체크포인트가 그 학생을 넘어가는지 본다."""

    def __init__(self, *, fails_for: str) -> None:
        super().__init__()
        self._fails_for = fails_for

    async def put(self, record: DraftRecord) -> str:
        if record.student_ref == self._fails_for:
            raise RuntimeError(f"초안 저장 실패(대역) student={record.student_ref}")
        return await super().put(record)


class _FailsOncePerStudent:
    """학생마다 **첫 write만** 벤더 오류 — 재개하면 그 학생이 성공한다.

    ⚠ `_GatewayThenFail`은 **모든** write가 실패해서 완료 학생이 아예 안 생긴다 —
    재개 검사에는 «앞 구간에서 끝난 학생»이 필요하다.
    """

    def __init__(self, *, fails_for: str) -> None:
        self._inner: Any = _ok_provider()
        self._fails_for = fails_for
        self._already_failed = False

    async def plan(self, **kwargs: Any) -> Any:  # noqa: ANN401
        return await self._inner.plan(**kwargs)

    async def write(self, **kwargs: Any) -> Any:  # noqa: ANN401
        context = kwargs.get("context")
        if (
            context is not None
            and context.student_ref == self._fails_for
            and not self._already_failed
        ):
            self._already_failed = True
            raise LlmError("vendor down(대역 · 1회)")
        return await self._inner.write(**kwargs)


class _FailingContexts(InMemoryContextStore):
    """입력 묶음 저장이 터진다 — enqueue까지 가면 안 된다."""

    async def put(self, record: ContextBundleRecord) -> str:
        del record
        raise RuntimeError("입력 묶음 저장 실패(대역)")


def _state_of(harness: _WorkerHarness, job: WorkerJob) -> dict[str, Any]:
    """체크포인트에 실제로 저장된 state — **워커가 아니라 저장된 것**을 본다."""

    async def read() -> dict[str, Any]:
        config = {"configurable": {"thread_id": str(job.job_id)}}
        snapshot = await harness.runner._checkpointer.aget(config)  # noqa: SLF001
        assert snapshot is not None, "체크포인트가 아예 없다 — 이 검사의 전제가 깨졌다"
        values: dict[str, Any] = snapshot["channel_values"]
        return values

    return asyncio.run(read())


# ───────────────────── ① 묶음 저장 실패 → enqueue 없음 ─────────────────────


def test_a_failed_bundle_write_does_not_enqueue_the_job() -> None:
    """🔴 **못 저장한 입력의 잡을 큐에 세우지 않는다.**

    ⚠ 세우면 워커가 `context_bundle_missing`으로 죽는다 — ㉻ ⓐ가 **저장 실패 형태로**
    되살아난다. 실패는 POST 자리에서 정직하게 나야 한다.
    """
    harness = _WorkerHarness(_ok_provider())
    harness.contexts = _FailingContexts()  # 하네스의 enqueue가 이걸 쓴다

    async def scenario() -> WorkerJob | None:
        with pytest.raises(RuntimeError, match="입력 묶음 저장 실패"):
            await harness.enqueue(["st_1"])
        #: 🔴 **큐가 비어 있어야 한다** — 잡이 섰는지는 lease로 묻는다.
        return await harness.supervisor.lease_next(
            tenant_id="t1",
            worker_kind=WorkerKind.COUNSEL_PACK,
            lease_owner="probe",
        )

    assert asyncio.run(scenario()) is None, "저장이 실패했는데 잡이 큐에 섰다"


# ───────────────── ② DRAFT 저장 실패 → 체크포인트가 안 앞선다 ─────────────────


def test_a_failed_draft_write_does_not_advance_the_checkpoint() -> None:
    """🔴 **본문을 못 저장한 학생이 「완료」로 체크포인트에 들어가면 안 된다.**

    ⚠ 들어가면 재개가 그 학생을 **건너뛰고**, 잡은 succeeded인데 그 학생의 본문만 영영
    없다 — GET이 `result=None`인 ㉻의 증상이 **한 학생 단위로** 재현된다.
    """
    drafts = _FailingDrafts(fails_for="st_2")
    harness = _WorkerHarness(_ok_provider(), draft_store=drafts)

    async def scenario() -> WorkerJob:
        job = await harness.enqueue(["st_1", "st_2", "st_3"])
        done = await harness.runner.run_next(tenant_id="t1")
        assert done is not None
        return job

    job = asyncio.run(scenario())
    values = _state_of(harness, job)

    #: 🔴 `st_1`까지만 전진했다 — 실패한 `st_2`는 결과에 없다.
    assert values["cursor"] == 1, f"cursor가 {values['cursor']}다 — 실패 학생을 넘어갔다"
    recorded = [result.student_ref for result in values["results"]]
    assert recorded == ["st_1"], f"결과에 {recorded}가 들어 있다"
    assert drafts.ids() and len(drafts.ids()) == 1, "저장된 초안 수가 안 맞는다"


# ───────────────── ③ 재개 — 완료 학생은 다시 안 돈다 ─────────────────


def test_a_resume_does_not_rewrite_or_regenerate_the_finished_student() -> None:
    """🔴 **재개는 완료 학생의 LLM도 초안 저장도 다시 하지 않는다.**

    ⚠ 다시 돌면 ⓐ 원가가 두 배고 ⓑ `new_draft_id()`가 **새 UUID**를 내므로 같은 학생의
    초안 행이 둘 생긴다(앞 행이 고아). 재개의 정의가 «cursor부터»인 이유다.

    ⚠ **재개마다 시계를 옮긴다** — `created_at`이 달라져도 멱등 충돌이 나면 안 된다.
    """
    drafts = _CountingDrafts()
    #: 서킷 1 — `st_1` 성공 뒤 `st_2`의 첫 시도에서 pause된다(결정론).
    harness = _WorkerHarness(
        _FailsOncePerStudent(fails_for="st_2"), circuit=1, draft_store=drafts
    )

    async def scenario() -> tuple[WorkerJob, WorkerJob]:
        job = await harness.enqueue(["st_1", "st_2"])
        paused = await harness.runner.run_next(tenant_id="t1")
        assert paused is not None
        assert paused.phase is JobPhase.PAUSED, f"1차 종단이 {paused.phase}다"
        #: 🔴 재개 — 시계를 옮겨도 충돌이 없어야 한다.
        harness.runner._now = lambda: _NOW + timedelta(minutes=5)  # noqa: SLF001
        await harness.supervisor.resume(tenant_id="t1", job_id=job.job_id)
        second = await harness.runner.run_next(tenant_id="t1")
        assert second is not None
        return paused, second

    first, second = asyncio.run(scenario())
    assert second.job_id == first.job_id, "재개가 다른 잡을 집었다"

    #: 🔴 `st_1`의 초안은 **한 번만** 저장됐다.
    assert drafts.per_student.count("st_1") == 1, (
        f"완료 학생의 초안이 {drafts.per_student}만큼 다시 저장됐다"
    )
    assert len(drafts.ids()) == len(set(drafts.ids())), "같은 학생의 초안 행이 둘이다"


# ───────────────── ④ 같은 id, 다른 전문 — 두 구현이 같은 판정 ─────────────────


#: ⚠ **고정 run_id** — 매번 새로 만들면 «같은 전문»이 성립하지 않아 멱등 검사가 거짓 red다.
_RUN_ID: Final = UUID(int=0x5EED)


def _draft(draft_id: UUID, *, content: str) -> DraftRecord:
    return DraftRecord(
        id=draft_id,
        run_id=_RUN_ID,
        agent_run_id=None,
        tenant_id="t1",
        kind="counsel_pack",
        student_ref="st_1",
        guardian_ref="gd_st_1",
        label_snapshot={},
        status="generated",
        fail_reason=None,
        created_at=_NOW,
        content=content,
    )


def _bundle(bundle_id: UUID, *, content_hash: str) -> ContextBundleRecord:
    return ContextBundleRecord(
        id=bundle_id,
        tenant_id="t1",
        class_ref="cl_a1",
        contexts={},
        content_hash=content_hash,
        created_at=_NOW,
    )


def test_the_in_memory_draft_store_refuses_a_different_body() -> None:
    """🔴 **인메모리도 PG와 같은 판정이다** — 갈리면 백엔드에 따라 본문이 달라진다.

    ⚠ 종전 인메모리는 `self._rows[id] = record`로 **조용히 덮었다.** 그러면
    `STORE_BACKEND=memory`에서만 통과하는 코드가 배포에서 예외가 된다(72-R에서 배운 자리).
    """
    store = InMemoryDraftResultStore()
    draft_id = uuid4()

    async def scenario() -> DraftRecord | None:
        await store.put(_draft(draft_id, content="첫 본문"))
        await store.put(_draft(draft_id, content="첫 본문"))  # 멱등
        with pytest.raises(DraftRecordConflict, match=str(draft_id)):
            await store.put(_draft(draft_id, content="다른 본문"))
        return await store.get(make_ref(DRAFT_SCHEME, draft_id), tenant_id="t1")

    stored = asyncio.run(scenario())
    assert stored is not None and stored.content == "첫 본문", "충돌인데 본문이 덮였다"


def test_the_in_memory_context_store_refuses_a_different_bundle() -> None:
    """🔴 입력 묶음도 같다 — 재개한 워커가 **다른 입력**을 읽으면 안 된다."""
    store = InMemoryContextStore()
    bundle_id = uuid4()

    async def scenario() -> None:
        await store.put(_bundle(bundle_id, content_hash="sha256:a"))
        await store.put(_bundle(bundle_id, content_hash="sha256:a"))  # 멱등
        with pytest.raises(ContextBundleConflict, match=str(bundle_id)):
            await store.put(_bundle(bundle_id, content_hash="sha256:b"))

    asyncio.run(scenario())
