"""세 capability가 **실제 enqueue 경로로** 실 PG를 지나는가 (99 #36 **G3**).

🔴 **`PgJobStore.add()`를 직접 부르는 검사로 대체하지 않는다.** 그건 #36을 두 달 못 보게 만든
바로 그 형태다 — 기존 PG 왕복 검사가 **부모 `AI_RUN`을 손으로 만들어** FK 결함을 가렸다.
여기서는 **capability별 실제 Enqueuer → 실제 `PgJobStore` → 실제 runner → 실제 원장 recorder**를
지나고, **PG에서 다시 읽어** 확인한다.

⚠ **실 LLM 호출 0** — 생성·조사 경로는 전부 Fake provider·Fake planner다.
⚠ 테스트는 **부모 `AI_RUN`을 만들지 않고**, **FK를 지우지 않고**, **최종 상태를 손으로
UPDATE하지 않고**, `result_ref`를 **주입하지 않는다.**
⚠ 각 검사는 **고유 테넌트**를 쓰고 **자기 테넌트 행만** 지운다.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ai.agents.supervisor import Supervisor
from ai.composition.counsel.assembly import DEFAULT_REGEN_MAX
from ai.composition.counsel.enqueue import CounselPackEnqueuer
from ai.composition.counsel.provider import FakeCounselProvider
from ai.composition.counsel.stores import (
    AgentStepRecord as CounselAgentStepRecord,
)
from ai.composition.counsel.stores import (
    InMemoryContextStore,
    InMemoryDraftResultStore,
)
from ai.composition.counsel.worker import CounselPackRunner
from ai.contracts.agents import (
    JobPhase,
    OperationKind,
    PriorityClass,
    WorkerJob,
    WorkerKind,
)
from ai.contracts.composition import (
    CommStyle,
    DraftContext,
    EvidenceFact,
    Frequency,
    Interest,
    LabelSnapshot,
    Sensitivity,
)
from ai.contracts.execution import Capability
from ai.db.ledger_completeness import LedgerVerdict, judge_ledger_row, summarize
from ai.db.repositories.agent_job import PgJobStore
from ai.db.repositories.counsel_step_store import PgCounselAgentStepSink
from ai.db.repositories.ledger_audit import PgLedgerAudit
from ai.db.repositories.pack_store import PgPackResultStore
from ai.db.repositories.probe_stores import (
    PgAgentStepSink,
    PgProfileStore,
    PgSpecResultStore,
)
from ai.db.repositories.run_store import LlmCallCollector, PgRunStore
from ai.db.settings import get_db_settings
from ai.evaluation.pg_ledger_preflight import preflight_blocks
from ai.import_mapping.probe.stores import ProfileRecord, serialize_profile
from ai.import_mapping.probe.worker import MappingProbeRunner
from ai.import_mapping.profiling import ColumnProfile, SheetProfile, SourceProfile

pytestmark = pytest.mark.integration

_NOW: Final = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)
_TIMEOUT: Final = 20.0


def _tenant(tag: str) -> str:
    """🔴 **검사마다 고유 테넌트** — 행 누수와 상호 오염을 구조로 막는다."""
    return f"t_g3_{tag}_{uuid.uuid4().hex[:8]}"


def _draft_context(student: str) -> DraftContext:
    return DraftContext(
        student_ref=student,
        guardian_ref=f"guardian-{student}",
        label_snapshot=LabelSnapshot(
            comm=CommStyle.NARRATIVE,
            sensitivity=Sensitivity.DIRECT,
            interest=Interest.ATTITUDE,
            frequency=Frequency.FREQUENT,
        ),
        facts=(
            EvidenceFact(label="학습 참여", value="꾸준함", record_id=f"rec-{student}"),
        ),
        evidence_summaries=("수업 참여 기록",),
        period_label="2026년 8월",
        fallback_text="확인 가능한 기록을 안내합니다.",
    )


async def _clean(sessions: async_sessionmaker[AsyncSession], tenant: str) -> None:
    """🔴 **자기 테넌트 행만** 지운다 — 테이블 전체 삭제를 본문에 두지 않는다."""
    async with sessions() as session, session.begin():
        for table in (
            "agent_step",
            "llm_call",
            "counsel_pack_result",
            "agent_run",
            "ai_run",
        ):
            column = "tenant_id"
            if table == "agent_step":
                await session.execute(
                    text(
                        "DELETE FROM agent_step WHERE agent_run_id IN"
                        " (SELECT id FROM agent_run WHERE tenant_id = :t)"
                    ),
                    {"t": tenant},
                )
                continue
            if table == "llm_call":
                await session.execute(
                    text(
                        "DELETE FROM llm_call WHERE run_id IN"
                        " (SELECT execution_id FROM ai_run WHERE tenant_id = :t)"
                    ),
                    {"t": tenant},
                )
                continue
            await session.execute(
                text(f"DELETE FROM {table} WHERE {column} = :t"), {"t": tenant}
            )


type _Scenario = Callable[[async_sessionmaker[AsyncSession]], Awaitable[None]]


def _run(scenario: _Scenario, *, tenant: str) -> None:
    async def go() -> str:
        engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
        try:
            async with engine.connect():
                pass
        except Exception:  # noqa: BLE001 — 접속 실패만 skip으로 가른다
            await engine.dispose()
            return "skip"
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with asyncio.timeout(_TIMEOUT):
                await scenario(sessions)
        finally:
            await _clean(sessions, tenant)
            await engine.dispose()
        return "ok"

    if asyncio.run(go()) == "skip":
        pytest.skip("실 PG 미가용 — docker compose up -d")


async def _agent_runs(
    sessions: async_sessionmaker[AsyncSession], tenant: str
) -> list[Any]:
    async with sessions() as session:
        return list(
            (
                await session.execute(
                    text(
                        "SELECT id, run_id, status, result_ref FROM agent_run"
                        " WHERE tenant_id = :t"
                    ),
                    {"t": tenant},
                )
            ).all()
        )


async def _llm_call_count(
    sessions: async_sessionmaker[AsyncSession], execution_id: uuid.UUID
) -> int:
    """이 실행의 `LLM_CALL` 행 수 — Fake planner에서는 **0이 정답**이다(99 ㉾)."""
    async with sessions() as session:
        return int(
            (
                await session.execute(
                    text("SELECT count(*) FROM llm_call WHERE run_id = :r"),
                    {"r": execution_id},
                )
            ).scalar_one()
        )


async def _scalars(
    sessions: async_sessionmaker[AsyncSession], sql: str, **params: uuid.UUID | str
) -> list[Any]:
    async with sessions() as session:
        return list((await session.execute(text(sql), params)).scalars().all())


async def _steps_of_tenant(
    sessions: async_sessionmaker[AsyncSession], tenant: str
) -> list[Any]:
    """이 테넌트의 `AGENT_STEP` — 🔴 **부모 `AGENT_RUN`을 조인**해서만 격리된다.

    ⚠ `agent_step`에는 `tenant_id` 컬럼이 **없다**(실측). ⑱(공통 `AgentStepRecord` 승격)을
    우회해 계약을 바꾸지 않고, 조회 쪽에서 부모를 탄다.
    """
    return await _scalars(
        sessions,
        "SELECT s.id FROM agent_step s JOIN agent_run r ON r.id = s.agent_run_id"
        " WHERE r.tenant_id = :t",
        t=tenant,
    )


async def _ai_runs(sessions: async_sessionmaker[AsyncSession], tenant: str) -> list[Any]:
    async with sessions() as session:
        return list(
            (
                await session.execute(
                    text(
                        "SELECT execution_id, tenant_id, capability,"
                        " input_snapshot_hash FROM ai_run WHERE tenant_id = :t"
                    ),
                    {"t": tenant},
                )
            ).all()
        )


async def _audit(
    sessions: async_sessionmaker[AsyncSession], tenant: str
) -> list[Any]:
    return [
        judge_ledger_row(o)
        for o in await PgLedgerAudit(sessions).observe(tenant_id=tenant)
    ]


def _supervisor(sessions: async_sessionmaker[AsyncSession]) -> Supervisor:
    """🔴 **실제 `PgJobStore`** — 인메모리로 바꿔치기하면 이 파일의 축이 사라진다."""
    return Supervisor(
        store=PgJobStore(sessionmaker=sessions),
        lease_duration=timedelta(minutes=5),
        priority_aging_interval=timedelta(minutes=1),
        clock=lambda: _NOW,
    )


class _SuccessWorkflow:
    """결정론 성공 대역 — **실 LLM을 안 부른다.**

    ⚠ 결과 종류는 이 파일의 축이 아니다(원장 결합을 본다) — 계약상 유효한 최소 결과를 낸다.
    """

    async def run(self, request: object, context: object) -> object:
        from ai.contracts.problem_generation import RejectedInsufficientOutcome

        del request, context
        return RejectedInsufficientOutcome(status_reason="g3_fixture_no_weakness_data")


async def _assert_enqueued(
    sessions: async_sessionmaker[AsyncSession], tenant: str, job: Any  # noqa: ANN401
) -> None:
    """enqueue 직후 공통 단정 — 🔴 **부모 `AI_RUN`이 아직 없는 것이 정상**이다.

    G1 전에는 이 INSERT 자체가 `23503`으로 죽었다(99 #36).
    """
    #: ⚠ **그 잡의 행만 본다** — 합산 검사에서는 앞 capability의 행이 같은 테넌트에 남는다.
    #:   테넌트 전량을 세면 개별 검사에서만 맞고 합산에서 틀린다(실측으로 잡았다).
    rows = [row for row in await _agent_runs(sessions, tenant) if row.id == job.job_id]
    assert len(rows) == 1, f"이 잡의 AGENT_RUN이 1건이 아니다: {rows}"
    assert rows[0].run_id == job.execution_id, "run_id가 execution_id와 다르다"
    assert rows[0].status in {JobPhase.QUEUED.value, JobPhase.LEASED.value}
    parents = [
        row
        for row in await _ai_runs(sessions, tenant)
        if row.execution_id == job.execution_id
    ]
    assert parents == [], "부모 AI_RUN이 미리 생겼다"
    #: **PG에서 다시 읽어** 복원되는가 — 값이 살아 있는지는 왕복으로만 안다.
    restored = await PgJobStore(sessionmaker=sessions).get(
        tenant_id=tenant, job_id=job.job_id
    )
    assert restored is not None, "PG에서 잡을 복원 못 했다"
    assert restored.execution_id == job.execution_id


async def _assert_ledger_bound(
    sessions: async_sessionmaker[AsyncSession],
    tenant: str,
    job: Any,  # noqa: ANN401
    *,
    capability: Capability,
) -> None:
    """종단 후 공통 단정 — 원장이 **정확히 하나** 생기고 논리 결합이 맞는가."""
    ledger = [
        row
        for row in await _ai_runs(sessions, tenant)
        if row.execution_id == job.execution_id
    ]
    assert len(ledger) == 1, f"AI_RUN이 1건이 아니다: {ledger}"
    assert ledger[0].tenant_id == tenant
    assert ledger[0].capability == capability.value


async def _run_counsel(
    sessions: async_sessionmaker[AsyncSession], tenant: str
) -> Any:  # noqa: ANN401 — WorkerJob
    """🔴 **실제 counsel enqueue → 실 PG → 실제 runner → 실제 원장.**

    ⚠ **개별 검사와 합산 검사가 같은 헬퍼를 쓴다** — 합산 쪽만 손으로 복제하면
    **한쪽만 낡는다**(99 #02).
    """
    supervisor = _supervisor(sessions)
    contexts = InMemoryContextStore()
    provider = FakeCounselProvider()
    runner = CounselPackRunner(
        supervisor=supervisor,
        context_store=contexts,
        draft_store=InMemoryDraftResultStore(),
        pack_store=PgPackResultStore(sessionmaker=sessions),
        #: 🔴 **실제 counsel PG sink다**(99 #37 해소) — 종전엔 타입이 맞는 PG 구현이 없어
        #:   스텝만 인메모리였다. 이제 counsel 계약 타입으로 PG에 앉는다.
        step_sink=PgCounselAgentStepSink(sessionmaker=sessions),
        run_store=PgRunStore(sessionmaker=sessions, clock=lambda: _NOW),
        call_log=LlmCallCollector(),
        planner=provider,
        writer=provider,
        checkpointer=InMemorySaver(),
        regen_max=DEFAULT_REGEN_MAX,
        lease_owner="worker-g3-counsel",
        now=lambda: _NOW,
    )
    job = await CounselPackEnqueuer(
        supervisor=supervisor, context_store=contexts, now=lambda: _NOW
    ).enqueue(
        tenant_id=tenant,
        class_ref="class-g3",
        contexts={"student-g3": _draft_context("student-g3")},
    )
    await _assert_enqueued(sessions, tenant, job)

    done = await runner.run_next(tenant_id=tenant)
    assert done is not None, "counsel runner가 잡을 집지 않았다"
    assert done.phase is JobPhase.SUCCEEDED, f"종단 상태가 {done.phase}다"
    assert done.result_ref, "result_ref가 없다"
    await _assert_ledger_bound(
        sessions, tenant, job, capability=Capability.COMPOSITION
    )

    #: 🔴 **counsel `AGENT_STEP`이 실제 PG에 앉았는가**(99 #37).
    #:   ⚠ 「저장소 왕복이 된다」로는 부족하다 — **워커가 실제로 소비**했는지를 본다.
    steps = await PgCounselAgentStepSink(sessionmaker=sessions).steps(job.job_id)
    assert steps, "counsel worker 완주 뒤에도 AGENT_STEP이 PG에 없다"
    assert all(step.agent_run_id == job.job_id for step in steps)
    #: `seq`가 중복 없이 정렬돼 있는가.
    seqs = [step.seq for step in steps]
    assert seqs == sorted(set(seqs)), f"seq가 중복이거나 정렬이 아니다: {seqs}"
    #: 반환 타입이 **counsel 레코드**인가 — probe 레코드면 계약이 갈린다.
    assert type(steps[0]) is CounselAgentStepRecord, type(steps[0]).__name__
    #: ⚠ **스텝 인자에 원문 컨텍스트가 안 실린다**(§5.2 — 마스킹 통과분만).
    #:   🔴 첫 판은 `student-g3`가 있으면 위반이라 적었는데 **그건 alias다** — 이 시스템의
    #:   경계는 전부 alias로 넘어오므로 그 존재는 **정상**이다(실측으로 잡았다).
    #:   ⇒ 실제로 물을 것은 **① 마스킹 토큰 잔존 0** ② **원문 컨텍스트를 통째로 안 담는다**다.
    dumped = repr([step.tool_args_masked for step in steps])
    assert "⟪" not in dumped, f"마스킹 토큰이 스텝 인자에 남았다: {dumped[:200]}"
    for leaked in ("확인 가능한 기록을 안내합니다", "꾸준함", "guardian-"):
        assert leaked not in dumped, (
            f"원문 컨텍스트가 스텝 인자에 실렸다({leaked!r}): {dumped[:200]}"
        )
    return job


async def _run_problem_generation(
    sessions: async_sessionmaker[AsyncSession], tenant: str
) -> Any:  # noqa: ANN401 — WorkerJob
    """🔴 **실제 pg enqueue → 실 PG → 실제 runner → 실제 원장.**

    ⚠ `finally: _record_execution()`을 **소스 grep으로 대신하지 않는다** — 실제 행을 본다.
    """
    from ai.contracts.problem_generation import ProblemRequest
    from ai.problem_generation.assembly import (
        ProblemGenerationRunner,
        _InMemoryProblemRequestStore,
        _InMemoryProblemResultStore,
    )
    from ai.problem_generation.enqueue import ProblemGenerationEnqueuer

    supervisor = _supervisor(sessions)
    requests = _InMemoryProblemRequestStore()
    runner = ProblemGenerationRunner(
        supervisor=supervisor,
        request_store=requests,
        result_store=_InMemoryProblemResultStore(),
        workflow=_SuccessWorkflow(),
        run_store=PgRunStore(sessionmaker=sessions, clock=lambda: _NOW),
        call_log=LlmCallCollector(),
        verify_config_version="verify-config.v1",
        prompt_version="v3",
        lease_owner="worker-g3-pg",
        now=lambda: _NOW,
    )
    job = await ProblemGenerationEnqueuer(
        supervisor=supervisor, request_store=requests, now=lambda: _NOW
    ).enqueue(
        ProblemRequest.model_validate(
            {
                "target_kind": "student",
                "target_ref": "student-g3",
                "target_source": "teacher_manual",
                "manual_targets": ["language.sentence_structure"],
                "snapshot_hash": "snapshot-g3",
                "taxonomy_version": "v1",
                "area_tag": "language",
                "type_tags": ["infer"],
                "item_format": "mcq",
                "count": 1,
                "request_id": f"req-{uuid.uuid4().hex[:8]}",
                "idempotency_key": f"idem-{uuid.uuid4().hex[:8]}",
                "tenant_id": tenant,
            }
        )
    )
    await _assert_enqueued(sessions, tenant, job)

    done = await runner.run_next(tenant_id=tenant)
    assert done is not None, "pg runner가 잡을 집지 않았다"
    assert done.phase is JobPhase.SUCCEEDED, f"종단 상태가 {done.phase}다"
    assert done.result_ref, "result_ref가 없다"
    await _assert_ledger_bound(
        sessions, tenant, job, capability=Capability.PROBLEM_GENERATION
    )
    return job


async def _run_mapping_probe(
    sessions: async_sessionmaker[AsyncSession], tenant: str
) -> Any:  # noqa: ANN401 — WorkerJob
    """🔴 **enqueue 차단은 풀렸고 원장 결손은 그대로다** — 둘을 섞지 않는다(㉾).

    ⚠ 테스트가 `AI_RUN`을 대신 만들지 않는다 — 만들면 결손이 가려진다.
    """
    from ai.db.repositories.probe_stores import PgProfileStore, PgSpecResultStore
    from ai.import_mapping.probe.enqueue import ProbeEnqueuer
    from ai.import_mapping.probe.worker import MappingProbeRunner
    from ai.import_mapping.profiling import ColumnProfile, SheetProfile, SourceProfile

    supervisor = _supervisor(sessions)
    profiles = PgProfileStore(sessionmaker=sessions)
    runner = MappingProbeRunner(
        supervisor=supervisor,
        profile_store=profiles,
        spec_store=PgSpecResultStore(sessionmaker=sessions),
        #: ⚠ probe 축은 타입이 맞는다 — 여기는 실제 PG sink다(#37은 counsel 축이다).
        step_sink=PgAgentStepSink(sessionmaker=sessions),
        checkpointer=InMemorySaver(),
        #: ⚠ **현재 체크인된 planner는 Fake다** — 실 LLM 배선은 별개의 현재 상태다.
        loop_max=3,
        lease_owner="worker-g3-probe",
        #: 🔴 **실제 PG 원장 저장소다**(99 ㉾ · 8/12) — 종전엔 이 인자가 없었고
        #: 러너가 `record_run()`을 아예 안 불렀다.
        run_store=PgRunStore(sessionmaker=sessions),
        call_log=LlmCallCollector(),
    )
    job = await ProbeEnqueuer(
        supervisor=supervisor, profile_store=profiles, now=lambda: _NOW
    ).enqueue(
        tenant_id=tenant,
        profile=SourceProfile(
            filename="g3.xlsx",
            sheets=(
                SheetProfile(
                    "s",
                    1,
                    (
                        ColumnProfile(
                            name="반",
                            n_total=1,
                            n_null=0,
                            n_unique=1,
                            dtype_guess="string",
                            suspect_pii=False,
                        ),
                    ),
                    (),
                ),
            ),
        ),
        file_hash=f"hash-{uuid.uuid4().hex[:8]}",
    )
    await _assert_enqueued(sessions, tenant, job)

    done = await runner.run_next(tenant_id=tenant)
    assert done is not None, "probe runner가 잡을 집지 않았다"
    assert done.phase is JobPhase.SUCCEEDED, f"조사가 완주 못 했다: {done.phase}"
    #: 🔴 **부모 `AI_RUN`을 손으로 넣지 않았다** — 러너가 만든 것만 센다(99 ㉾).
    #:   종전 이 자리는 *"원장은 없다"* 를 단정했다 — 그게 ㉾의 결손이었다.
    written = [
        row
        for row in await _ai_runs(sessions, tenant)
        if row.execution_id == job.execution_id
    ]
    assert len(written) == 1, f"조사 완주 뒤 AI_RUN이 {len(written)}건이다(1이어야 한다)"
    row = written[0]
    assert row.capability == Capability.IMPORT_MAPPING.value, row.capability
    assert row.tenant_id == tenant
    assert row.input_snapshot_hash == job.payload_hash, (
        "입력 스냅숏이 잡의 payload_hash가 아니다"
    )
    #: 🔴 **Fake planner라 LLM_CALL은 0건**이다 — 실행은 있었고 호출은 없었다.
    assert await _llm_call_count(sessions, job.execution_id) == 0, (
        "Fake planner인데 LLM_CALL이 남았다"
    )
    #: ⚠ **완주한 잡을 돌려준다** — enqueue 시점 잡에는 `result_ref`가 없다(실측: `None`).
    #:   `job_id`·`execution_id`는 같은 값이라 기존 호출부는 그대로다.
    return done


# ── G3-A · counsel ──────────────────────────────────────────────


def test_counsel_enqueue_then_worker_binds_the_ledger_in_real_pg() -> None:
    """실제 enqueue→runner 완주 뒤 감사가 `ok`를 낸다."""
    tenant = _tenant("counsel")

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        await _run_counsel(sessions, tenant)
        findings = await _audit(sessions, tenant)
        assert [f.verdict for f in findings] == [LedgerVerdict.OK], [
            f.reason for f in findings
        ]

    _run(scenario, tenant=tenant)


# ── G3-B · problem_generation ───────────────────────────────────


def test_problem_generation_enqueue_then_worker_binds_the_ledger() -> None:
    """실제 enqueue→runner 완주 뒤 감사가 `ok`를 낸다."""
    tenant = _tenant("pg")

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        await _run_problem_generation(sessions, tenant)
        findings = await _audit(sessions, tenant)
        assert [f.verdict for f in findings] == [LedgerVerdict.OK], [
            f.reason for f in findings
        ]

    _run(scenario, tenant=tenant)


# ── G3-C · mapping_probe ────────────────────────────────────────


def test_mapping_probe_enqueue_then_worker_binds_the_ledger() -> None:
    """🔴 **㉾ 해소(8/12)** — 조사 축도 다른 둘과 같이 `ok`다.

    ⚠ 종전 이름은 `..._but_leaves_the_ledger_gap`이었고 `separate_gap`을 단정했다 —
    그 판정값은 이제 **존재하지 않는다**(생산자가 0이라 없앴다).
    """
    tenant = _tenant("probe")

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        job = await _run_mapping_probe(sessions, tenant)
        findings = await _audit(sessions, tenant)
        assert [f.verdict for f in findings] == [LedgerVerdict.OK], [
            f.reason for f in findings
        ]
        #: 🔴 **AGENT_RUN.run_id ↔ AI_RUN.execution_id가 같은 값**인지 값으로 본다.
        assert findings[0].observation.run_id == job.execution_id
        assert not preflight_blocks(findings)

    _run(scenario, tenant=tenant)


def test_the_probe_links_agent_run_step_and_spec_to_one_execution() -> None:
    """🔴 조사 한 건의 **네 행이 같은 실행으로 묶이는가** — 값 대조.

    `AGENT_RUN.run_id` == `WorkerJob.execution_id` == `AI_RUN.execution_id` ·
    `MAPPING_SPEC.probe_agent_run` == `AGENT_RUN.id`.
    """
    tenant = _tenant("probelink")

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        job = await _run_mapping_probe(sessions, tenant)
        run_ids = await _scalars(
            sessions, "SELECT run_id FROM agent_run WHERE id = :j", j=job.job_id
        )
        spec_parents = await _scalars(
            sessions,
            "SELECT probe_agent_run FROM mapping_spec WHERE tenant_id = :t",
            t=tenant,
        )
        steps = await _scalars(
            sessions, "SELECT id FROM agent_step WHERE agent_run_id = :j", j=job.job_id
        )
        assert run_ids == [job.execution_id], f"AGENT_RUN.run_id가 다르다: {run_ids}"
        assert spec_parents == [job.job_id], f"spec의 부모가 다르다: {spec_parents}"
        assert steps, "AGENT_STEP이 없다 — 조사 이력이 안 남았다"

    _run(scenario, tenant=tenant)


def test_another_tenant_sees_none_of_the_probe_rows() -> None:
    """🔴 조사 **원장·스텝·spec 셋 다** 다른 테넌트에 보이면 안 된다.

    ⚠ **종전 판은 이름이 보는 것보다 넓었다** — docstring은 셋을 말하는데 실제로는
    감사와 `AI_RUN`만 봤다(로그 85 계열). 여기서 셋을 각각 센다.
    ⚠ **`AGENT_STEP`에는 테넌트 컬럼이 없다** — 부모 `AGENT_RUN`과 조인해서만 격리된다
    (⑱을 우회해 공통 계약을 바꾸지 않는다).
    """
    tenant = _tenant("probeiso")

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        job = await _run_mapping_probe(sessions, tenant)
        stranger = f"{tenant}-stranger"
        specs = PgSpecResultStore(sessionmaker=sessions)
        result_ref = job.result_ref
        assert result_ref is not None

        #: ── 같은 테넌트에서는 셋 다 실존한다(절단 가드 — 없으면 아래가 공짜다) ──
        assert await _audit(sessions, tenant), "내 테넌트에서도 감사가 비었다"
        assert await _ai_runs(sessions, tenant), "내 테넌트에서도 AI_RUN이 없다"
        assert await specs.get(result_ref, tenant_id=tenant) is not None
        assert await _steps_of_tenant(sessions, tenant), "내 테넌트에서도 STEP이 없다"

        #: ── 남의 테넌트에서는 셋 다 0 ──
        assert await _audit(sessions, stranger) == [], "남의 조사 실행이 보인다"
        assert await _ai_runs(sessions, stranger) == [], "남의 AI_RUN이 보인다"
        assert await specs.get(result_ref, tenant_id=stranger) is None, (
            "남의 MAPPING_SPEC이 해소된다"
        )
        assert await _steps_of_tenant(sessions, stranger) == [], (
            "남의 AGENT_STEP이 보인다"
        )

    _run(scenario, tenant=tenant)


def test_a_profile_of_another_tenant_is_never_executed_in_real_pg() -> None:
    """🔴 **남의 프로파일로는 실행 자체가 안 된다** (99 #40 · 실 PG).

    ⚠ **최종 조회에서 남의 행이 안 보이는지만 보면 이 결함이 통과한다** — 산출물이
    **내 테넌트로 재포장**되기 때문이다. 여기서는 **실행이 없었다**를 센다.
    """
    tenant = _tenant("xtenant")

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        owner = f"{tenant}-owner"
        profiles = PgProfileStore(sessionmaker=sessions)
        foreign_ref = await profiles.put(
            ProfileRecord(
                id=uuid.uuid4(),
                tenant_id=owner,  # ← 남의 테넌트 소유
                file_hash=f"hash-{uuid.uuid4().hex[:8]}",
                filename="secret.xlsx",
                sheets=serialize_profile(
                    SourceProfile(
                        filename="secret.xlsx",
                        sheets=(
                            SheetProfile(
                                "s",
                                1,
                                (
                                    ColumnProfile(
                                        name="반",
                                        n_total=1,
                                        n_null=0,
                                        n_unique=1,
                                        dtype_guess="string",
                                        suspect_pii=False,
                                    ),
                                ),
                                (),
                            ),
                        ),
                    )
                ),
                created_at=_NOW,
            )
        )
        supervisor = _supervisor(sessions)
        runner = MappingProbeRunner(
            supervisor=supervisor,
            profile_store=profiles,
            spec_store=PgSpecResultStore(sessionmaker=sessions),
            step_sink=PgAgentStepSink(sessionmaker=sessions),
            checkpointer=InMemorySaver(),
            loop_max=3,
            lease_owner="worker-xtenant",
            run_store=PgRunStore(sessionmaker=sessions),
            call_log=LlmCallCollector(),
        )
        job = await supervisor.enqueue(
            WorkerJob(
                job_id=uuid.uuid4(),
                execution_id=uuid.uuid4(),
                tenant_id=tenant,  # ← 내 테넌트 잡인데 payload는 남의 것
                worker_kind=WorkerKind.MAPPING_PROBE,
                operation=OperationKind.MAPPING_PROBE_RESOLVE,
                payload_ref=foreign_ref,
                payload_hash=f"sha256:{'c' * 64}",
                priority_class=PriorityClass.STANDARD,
                queued_at=_NOW,
            )
        )
        with pytest.raises(ValueError, match="참조 해소 실패"):
            await runner.run_next(tenant_id=tenant)

        #: 🔴 **실행이 없었다** — 셋 다 0이어야 한다.
        assert await _ai_runs(sessions, tenant) == [], "남의 입력으로 원장이 생겼다"
        assert await _steps_of_tenant(sessions, tenant) == [], "AGENT_STEP이 생겼다"
        assert await _scalars(
            sessions, "SELECT id FROM mapping_spec WHERE tenant_id = :t", t=tenant
        ) == [], "남의 입력이 내 테넌트 산출물로 재포장됐다"
        #: 잡은 종단으로 수렴한다(running 방치 금지 · 99 #18).
        landed = await supervisor.get(tenant_id=tenant, job_id=job.job_id)
        assert landed is not None and landed.phase is JobPhase.FAILED

    _run(scenario, tenant=tenant)


# ── §8 · 세 경로를 합친 감사 판정 ───────────────────────────────


def test_all_three_capabilities_audit_together_in_one_tenant() -> None:
    """🔴 **한 테넌트에서 세 실행이 끝난 뒤 감사가 세 판정을 각각 낸다.**

    ⚠ **행 순서에 기대지 않는다** — `execution_id`로 정확 대조한다.
    ⚠ **개별 검사와 같은 헬퍼**를 쓴다 — 조립을 복제하면 한쪽만 낡는다.
    """
    tenant = _tenant("all")

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        counsel_job = await _run_counsel(sessions, tenant)
        pg_job = await _run_problem_generation(sessions, tenant)
        probe_job = await _run_mapping_probe(sessions, tenant)

        findings = await _audit(sessions, tenant)
        by_run = {f.observation.run_id: f for f in findings}
        assert len(findings) == 3, f"세 실행이 안 보인다: {len(findings)}건"
        assert by_run[counsel_job.execution_id].verdict is LedgerVerdict.OK
        assert by_run[pg_job.execution_id].verdict is LedgerVerdict.OK
        #: ✅ **8/12부터 셋 다 `ok`다** — 종전엔 조사만 `separate_gap`이었다(99 ㉾).
        assert by_run[probe_job.execution_id].verdict is LedgerVerdict.OK

        counts = summarize(findings)
        assert counts[LedgerVerdict.VIOLATION] == 0
        assert counts[LedgerVerdict.UNKNOWN] == 0
        assert counts[LedgerVerdict.OK] == 3
        assert not preflight_blocks(findings)

    _run(scenario, tenant=tenant)
