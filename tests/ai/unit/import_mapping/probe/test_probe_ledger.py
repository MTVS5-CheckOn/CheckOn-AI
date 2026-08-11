"""**조사 잡 한 건이 실행 원장을 남기는가** (99 ㉾).

🔴 **`record_run()` 호출이 0건이었다.** 불변식 8은 *"모든 실행"* 을 기록하라고 하는데
`mapping_probe` 워커만 그 문장 밖에 있었다 — 원장이 0건이면 그 실행은 **재현도 원가 집계도
불가능**하고, 생애주기 점검이 그 축만 `separate_gap`으로 비켜 가야 했다.

**원장 하나의 단위는 「조사 루프 한 회전」이 아니라 「`WorkerJob` 한 건」이다.**
도구 호출은 같은 실행 아래 `AGENT_STEP`으로 남는다 — 루프마다 AI_RUN을 만들면 한 잡이
원장 N행이 되고 `execution_id`가 무엇을 가리키는지 갈린다.

⚠ **현재 planner는 `FakeProbePlanner`라 LLM 호출이 0건**이다. 그래서 사용 축 셋
(`model_provider`·`model_name`·`generation_params`)은 **`None`이 정답**이다 —
조립부 설정값을 적어 두면 *"그 값으로 돌렸다"* 가 **거짓**이 된다(99 ㊧ 계열).
🔴 **호출이 없다는 이유로 AI_RUN까지 생략하면 안 된다** — 실행은 있었다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from ai.agents.job_store import InMemoryJobStore
from ai.agents.supervisor import Supervisor
from ai.contracts.agents import (
    OperationKind,
    PriorityClass,
    WorkerJob,
    WorkerKind,
)
from ai.contracts.execution import Capability
from ai.db.repositories.run_store import InMemoryRunStore, LlmCallCollector
from ai.import_mapping.probe.stores import (
    InMemoryAgentStepSink,
    InMemoryProfileStore,
    InMemorySpecResultStore,
    ProfileRecord,
    serialize_profile,
)
from ai.import_mapping.probe.worker import MappingProbeRunner
from ai.import_mapping.profiling import ColumnProfile, SheetProfile, SourceProfile
from ai.import_mapping.versions import import_versions

_NOW: Final = datetime(2026, 8, 12, tzinfo=UTC)
_HASH: Final = "sha256:" + "a" * 64
_JOB: Final = UUID(int=51)
_EXEC: Final = UUID(int=52)
_TENANT: Final = "t_probe_ledger"
_ROSTER: Final = ["원생명", "반", "등원일", "상태", "동의"]


def _run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


def _counter() -> Callable[[], UUID]:
    state = {"n": 100}

    def _next() -> UUID:
        state["n"] += 1
        return UUID(int=state["n"])

    return _next


def _profile(headers: list[str]) -> SourceProfile:
    cols = tuple(
        ColumnProfile(
            name=h,
            n_total=1,
            n_null=0,
            n_unique=1,
            dtype_guess="string",
            suspect_pii=(h == "원생명"),
        )
        for h in headers
    )
    return SourceProfile(filename="r.xlsx", sheets=(SheetProfile("s", 1, cols, ()),))


class _Harness:
    """러너 + 관측 지점 — 🔴 **원장 저장소를 명시로 주입한다.**

    ⚠ 테스트 전용 기본값(`run_store=None` → 내부에서 만들기)을 두면 **배선 누락이
    검사에서 안 보인다** — 그게 `#37`(counsel 스텝 싱크)이 넉 달 살아남은 형태다.
    """

    def __init__(self, *, loop_max: int = 6) -> None:
        self.supervisor = Supervisor(
            store=InMemoryJobStore(),
            lease_duration=timedelta(minutes=5),
            priority_aging_interval=timedelta(minutes=1),
            clock=lambda: _NOW,
        )
        self.profiles = InMemoryProfileStore()
        self.specs = InMemorySpecResultStore()
        self.sink = InMemoryAgentStepSink()
        self.runs = InMemoryRunStore()
        self.calls = LlmCallCollector()
        self.runner = MappingProbeRunner(
            supervisor=self.supervisor,
            profile_store=self.profiles,
            spec_store=self.specs,
            step_sink=self.sink,
            checkpointer=InMemorySaver(),
            loop_max=loop_max,
            lease_owner="worker-ledger",
            new_id=_counter(),
            run_store=self.runs,
            call_log=self.calls,
            now=lambda: _NOW,
        )

    def put_profile(self) -> str:
        return _run(
            self.profiles.put(
                ProfileRecord(
                    id=UUID(int=1001),
                    tenant_id=_TENANT,
                    file_hash="h",
                    filename="r.xlsx",
                    sheets=serialize_profile(_profile(_ROSTER)),
                    created_at=_NOW,
                )
            )
        )

    async def enqueue(self, payload_ref: str) -> WorkerJob:
        return await self.supervisor.enqueue(
            WorkerJob(
                job_id=_JOB,
                execution_id=_EXEC,
                tenant_id=_TENANT,
                worker_kind=WorkerKind.MAPPING_PROBE,
                operation=OperationKind.MAPPING_PROBE_RESOLVE,
                payload_ref=payload_ref,
                payload_hash=_HASH,
                priority_class=PriorityClass.STANDARD,
                queued_at=_NOW,
            )
        )

    def drain(self, payload_ref: str) -> None:
        async def scenario() -> None:
            await self.enqueue(payload_ref)
            await self.runner.run_next(tenant_id=_TENANT)

        _run(scenario())


def test_a_finished_probe_leaves_exactly_one_ai_run() -> None:
    """🔴 **잡 한 건 = AI_RUN 한 행.** 루프 회전 수와 무관하다."""
    harness = _Harness()
    harness.drain(harness.put_profile())
    assert list(harness.runs.runs) == [_EXEC], (
        f"조사 완주 뒤 AI_RUN이 {list(harness.runs.runs)}다 — "
        f"1건이 아니거나 다른 키다(단위는 잡 하나 · 99 ㉾)"
    )


def test_the_ledger_row_carries_the_job_identity() -> None:
    """🔴 값 대조 — `execution_id`·tenant·capability·입력 해시."""
    harness = _Harness()
    harness.drain(harness.put_profile())
    row = harness.runs.runs[_EXEC]
    assert row.execution_id == _EXEC, "잡의 실행 신원이 아니다(job_id를 쓴 것일 수 있다)"
    assert row.tenant_id == _TENANT
    assert row.capability is Capability.IMPORT_MAPPING
    assert row.input_snapshot_hash == _HASH, (
        "입력 스냅숏이 잡의 payload_hash가 아니다 — file_hash나 상수를 쓴 것일 수 있다"
    )


def test_the_versions_come_from_the_shared_canon() -> None:
    """🔴 응답과 원장이 **같은 함수**를 읽는다 — 10키 **전량** 대조(99 #20).

    ⚠ `RunMetadata`는 버전을 **평탄화**해서 든다(`versions` 필드가 없다) —
    키 목록을 손으로 적지 않고 `VersionSet`에서 파생한다(키가 늘면 자동으로 함께 본다).
    """
    harness = _Harness()
    harness.drain(harness.put_profile())
    row = harness.runs.runs[_EXEC]
    expected = import_versions().model_dump()
    assert len(expected) == 10, f"버전 키 수가 바뀌었다: {len(expected)}"
    mismatched = {
        key: (getattr(row, key), value)
        for key, value in expected.items()
        if getattr(row, key) != value
    }
    assert not mismatched, f"원장 버전이 응답 정본과 다르다: {mismatched}"


def test_a_fake_planner_records_no_model_usage() -> None:
    """🔴 **0콜인데 사용값이 있으면 거짓이다** — 사용 축 셋은 `None`이 정답.

    ⚠ 조립부 설정을 여기에 적어 두면 *"그 파라미터로 돌렸다"* 가 된다(99 ㊧ 계열).
    """
    harness = _Harness()
    harness.drain(harness.put_profile())
    row = harness.runs.runs[_EXEC]
    assert row.model_provider is None, "LLM을 안 불렀는데 provider가 적혔다"
    assert row.model_name is None
    assert row.generation_params is None
    assert harness.runs.calls == [], "Fake planner인데 LLM_CALL이 남았다"


def test_the_agent_steps_still_land() -> None:
    """원장이 생겨도 기존 `AGENT_STEP`은 그대로다 — 회귀."""
    harness = _Harness()
    harness.drain(harness.put_profile())
    steps = _run(harness.sink.steps(_JOB))
    assert steps, "AGENT_STEP이 사라졌다"
    assert all(step.agent_run_id == _JOB for step in steps)


def test_the_ledger_is_written_before_the_agent_steps() -> None:
    """🔴 **원장이 `AGENT_STEP`보다 먼저다.**

    ⚠ 지금은 `llm_call_id`가 항상 `None`이라 눈에 안 보이지만, 그 값이 채워지는 순간
    **참조 대상 `LLM_CALL` 행이 먼저 존재해야 한다**(counsel이 같은 이유로 같은 순서다).
    순서를 뒤집어도 오늘은 아무 검사가 안 깨지므로 **여기서 못 박는다.**
    """
    order: list[str] = []
    harness = _Harness()

    original_record_run = harness.runs.record_run
    original_step = harness.sink.record

    async def spy_run(*args: object, **kwargs: object) -> None:
        order.append("ai_run")
        await original_record_run(*args, **kwargs)  # type: ignore[arg-type]

    async def spy_step(*args: object, **kwargs: object) -> None:
        order.append("agent_step")
        await original_step(*args, **kwargs)  # type: ignore[arg-type]

    harness.runs.record_run = spy_run  # type: ignore[method-assign]
    harness.sink.record = spy_step  # type: ignore[method-assign]
    harness.drain(harness.put_profile())

    assert order, "아무것도 안 불렸다 — 이 검사가 아무것도 안 보고 있다"
    assert order[0] == "ai_run", f"원장이 AGENT_STEP보다 늦다: {order[:3]}"


def test_a_failure_after_the_execution_boundary_still_records() -> None:
    """🔴 **실행 경계를 지난 실패도 원장을 남긴다** — 성공 경로에만 두면 안 된다.

    ⚠ 실행 경계는 **그래프 실행 직전**이다. 그 뒤의 실패는 *"실행이 있었고 실패했다"* 이고
    원장에 남아야 재현 추적이 선다.
    """
    harness = _Harness()
    ref = harness.put_profile()

    async def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("그래프 실행 실패(대역)")

    harness.runner._steps.record = explode  # type: ignore[method-assign,assignment]

    async def scenario() -> None:
        await harness.enqueue(ref)
        with pytest.raises(RuntimeError):
            await harness.runner.run_next(tenant_id=_TENANT)

    _run(scenario())
    assert list(harness.runs.runs) == [_EXEC], (
        "실행 경계 뒤에 실패했는데 원장이 없다 — 성공 경로에만 적재하고 있다"
    )


def test_no_ledger_before_the_execution_starts() -> None:
    """🔴 **실행할 입력 자체가 없으면 원장을 만들지 않는다.**

    ⚠ 없는 실행의 실행 기록을 지어내지 않는다 — counsel의
    `test_no_ledger_before_the_execution_starts`와 같은 판단이다.
    """
    harness = _Harness()

    async def scenario() -> None:
        await harness.enqueue("profile://00000000-0000-0000-0000-0000000000ff")
        with pytest.raises(ValueError, match="참조 해소 실패"):
            await harness.runner.run_next(tenant_id=_TENANT)

    _run(scenario())
    assert not harness.runs.runs, "실행 전 실패인데 원장이 생겼다"


def test_the_collector_bucket_is_drained() -> None:
    """🔴 `take()`를 안 부르면 버킷이 남아 **LRU로 밀려날 때까지 방치**된다.

    ⚠ 그 축출은 경고 로그로만 나가고 **그 경고를 읽는 사람이 0명이었다**(counsel 실측).
    ⇒ 실행 뒤 그 `execution_id`의 버킷이 **비어 있어야** 한다.
    """
    harness = _Harness()
    harness.drain(harness.put_profile())
    assert harness.calls.take(_EXEC) == (), (
        "실행 뒤에도 수집기 버킷이 남아 있다 — take()를 안 불렀다"
    )
