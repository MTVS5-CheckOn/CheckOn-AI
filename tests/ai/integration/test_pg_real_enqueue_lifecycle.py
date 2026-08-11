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
    InMemoryAgentStepSink,
    InMemoryContextStore,
    InMemoryDraftResultStore,
)
from ai.composition.counsel.worker import CounselPackRunner
from ai.contracts.agents import JobPhase
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
from ai.db.repositories.ledger_audit import PgLedgerAudit
from ai.db.repositories.pack_store import PgPackResultStore
from ai.db.repositories.probe_stores import PgAgentStepSink
from ai.db.repositories.run_store import LlmCallCollector, PgRunStore
from ai.db.settings import get_db_settings
from ai.evaluation.pg_ledger_preflight import preflight_blocks

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


async def _ai_runs(sessions: async_sessionmaker[AsyncSession], tenant: str) -> list[Any]:
    async with sessions() as session:
        return list(
            (
                await session.execute(
                    text(
                        "SELECT execution_id, tenant_id, capability FROM ai_run"
                        " WHERE tenant_id = :t"
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


async def _run_counsel(
    sessions: async_sessionmaker[AsyncSession], tenant: str
) -> Any:  # noqa: ANN401 — WorkerJob
    """실제 counsel enqueue → runner 완주. 잡을 돌려준다."""
    supervisor = _supervisor(sessions)
    contexts = InMemoryContextStore()
    provider = FakeCounselProvider()
    runner = CounselPackRunner(
        supervisor=supervisor,
        context_store=contexts,
        draft_store=InMemoryDraftResultStore(),
        pack_store=PgPackResultStore(sessionmaker=sessions),
        #: ⚠ **counsel에는 타입이 맞는 PG step sink가 없다**(G3 발견 · 8/11).
        #:   `PgAgentStepSink`는 **probe 축의 `AgentStepRecord`**로 타입돼 있고 counsel의
        #:   동명 클래스와 **필드는 같지만 별개 클래스**다 ⇒ mypy가 거부한다.
        #:   **이 파일의 축은 `AGENT_RUN`↔`AI_RUN` 결합**이므로 스텝은 인메모리로 두고
        #:   그 사실을 완료 보고에 남긴다(프로덕션은 이 PR에서 안 고친다).
        step_sink=InMemoryAgentStepSink(),
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
    await runner.run_next(tenant_id=tenant)
    return job


# ── G3-A · counsel ──────────────────────────────────────────────


def test_counsel_enqueue_then_worker_binds_the_ledger_in_real_pg() -> None:
    """🔴 **실제 `CounselPackEnqueuer` → 실 PG → 실제 runner → 실제 원장**.

    ⚠ enqueue 직후에는 **같은 ID의 `AI_RUN`이 아직 없는 것이 정상**이다 —
    G1이 그 사실을 표현할 수 있게 만들었다(#36 판정 ③).
    """
    tenant = _tenant("counsel")

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        supervisor = _supervisor(sessions)
        contexts = InMemoryContextStore()
        provider = FakeCounselProvider()
        collector = LlmCallCollector()
        runner = CounselPackRunner(
            supervisor=supervisor,
            context_store=contexts,
            draft_store=InMemoryDraftResultStore(),
            #: 🔴 실제 PG 저장소들 — 결과·스텝·원장이 전부 DB로 간다.
            pack_store=PgPackResultStore(sessionmaker=sessions),
            #: ⚠ counsel에는 타입이 맞는 PG step sink가 없다 — 위 `_run_counsel` 주석 참조.
            step_sink=InMemoryAgentStepSink(),
            run_store=PgRunStore(sessionmaker=sessions, clock=lambda: _NOW),
            call_log=collector,
            planner=provider,
            writer=provider,
            checkpointer=InMemorySaver(),
            regen_max=DEFAULT_REGEN_MAX,
            lease_owner="worker-g3-counsel",
            now=lambda: _NOW,
        )
        enqueuer = CounselPackEnqueuer(
            supervisor=supervisor, context_store=contexts, now=lambda: _NOW
        )

        job = await enqueuer.enqueue(
            tenant_id=tenant,
            class_ref="class-g3",
            contexts={"student-g3": _draft_context("student-g3")},
        )

        # ── enqueue 직후 ──────────────────────────────────────
        rows = await _agent_runs(sessions, tenant)
        assert len(rows) == 1, f"AGENT_RUN이 1건이 아니다: {rows}"
        assert rows[0].status in {JobPhase.QUEUED.value, JobPhase.LEASED.value}
        assert rows[0].run_id == job.execution_id, "run_id가 execution_id와 다르다"
        #: 🔴 **부모가 아직 없어도 정상** — G1 전에는 이 INSERT 자체가 죽었다.
        assert await _ai_runs(sessions, tenant) == []

        #: **PG에서 다시 읽어** 복원되는가 — 값이 살아 있는지는 왕복으로만 안다.
        restored = await PgJobStore(sessionmaker=sessions).get(
            tenant_id=tenant, job_id=job.job_id
        )
        assert restored is not None, "PG에서 잡을 복원 못 했다"
        assert restored.execution_id == job.execution_id

        # ── worker 완주 후 ────────────────────────────────────
        done = await runner.run_next(tenant_id=tenant)
        assert done is not None, "runner가 잡을 집지 않았다"
        assert done.phase is JobPhase.SUCCEEDED, f"종단 상태가 {done.phase}다"
        assert done.result_ref, "result_ref가 없다"

        ledger = await _ai_runs(sessions, tenant)
        assert len(ledger) == 1, f"AI_RUN이 1건이 아니다: {ledger}"
        assert ledger[0].execution_id == job.execution_id, "원장 결합이 어긋났다"
        assert ledger[0].tenant_id == tenant
        assert ledger[0].capability == Capability.COMPOSITION.value

        findings = await _audit(sessions, tenant)
        assert [f.verdict for f in findings] == [LedgerVerdict.OK], [
            f.reason for f in findings
        ]

    _run(scenario, tenant=tenant)


# ── G3-C · mapping_probe ────────────────────────────────────────


def test_mapping_probe_enqueue_runs_but_leaves_the_ledger_gap() -> None:
    """🔴 **enqueue 차단은 풀렸고 원장 결손은 그대로다** — 둘을 섞지 않는다.

    G1이 열어 준 것은 **잡이 PG에 앉는 것**이다. `mapping_probe`는 `record_run()`을
    **한 번도 안 부르므로** `AI_RUN`이 없고, 그것은 **㉾의 별도 결손**이다.

    ⚠ **`ok`로 판정하지 않는다** — 원장이 없는 것을 정상으로 부르면 ㉾가 사라진다.
    ⚠ **`violation`으로 G3를 실패 처리하지 않는다** — 판정 ③이 그렇게 확정했다.
    ⚠ **테스트가 `AI_RUN`을 대신 만들지 않는다** — 그러면 결손이 가려진다.
    """
    tenant = _tenant("probe")

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        from ai.db.repositories.probe_stores import PgProfileStore, PgSpecResultStore
        from ai.import_mapping.probe.enqueue import ProbeEnqueuer
        from ai.import_mapping.probe.worker import MappingProbeRunner
        from ai.import_mapping.profiling import (
            ColumnProfile,
            SheetProfile,
            SourceProfile,
        )

        supervisor = _supervisor(sessions)
        profiles = PgProfileStore(sessionmaker=sessions)
        enqueuer = ProbeEnqueuer(
            supervisor=supervisor, profile_store=profiles, now=lambda: _NOW
        )
        runner = MappingProbeRunner(
            supervisor=supervisor,
            profile_store=profiles,
            spec_store=PgSpecResultStore(sessionmaker=sessions),
            step_sink=PgAgentStepSink(sessionmaker=sessions),
            checkpointer=InMemorySaver(),
            #: ⚠ **현재 체크인된 planner는 Fake다** — 실 LLM 배선은 별개의 현재 상태다.
            loop_max=3,
            lease_owner="worker-g3-probe",
        )
        profile = SourceProfile(
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
        )

        job = await enqueuer.enqueue(
            tenant_id=tenant, profile=profile, file_hash="hash-g3"
        )

        rows = await _agent_runs(sessions, tenant)
        assert len(rows) == 1, f"AGENT_RUN이 1건이 아니다: {rows}"
        assert rows[0].run_id == job.execution_id
        restored = await PgJobStore(sessionmaker=sessions).get(
            tenant_id=tenant, job_id=job.job_id
        )
        assert restored is not None and restored.execution_id == job.execution_id

        done = await runner.run_next(tenant_id=tenant)
        assert done is not None, "probe runner가 잡을 집지 않았다"
        #: 계약된 종단 상태에 도달했는가 — 어느 값인지는 러너 계약이 정한다.
        assert done.phase in {JobPhase.SUCCEEDED, JobPhase.FAILED}, done.phase

        #: 🔴 **원장은 없다** — 테스트가 만들지 않았고 러너도 안 만든다.
        assert await _ai_runs(sessions, tenant) == []

        findings = await _audit(sessions, tenant)
        assert [f.verdict for f in findings] == [LedgerVerdict.SEPARATE_GAP], [
            f.reason for f in findings
        ]
        assert "㉾" in findings[0].reason, "㉾ 안건과 연결되지 않았다"
        #: ⚠ **㉾는 관문을 막지 않는다** — 그것이 현재 확정 판정이다.
        assert not preflight_blocks(findings)

    _run(scenario, tenant=tenant)


# ── G3-B · problem_generation ───────────────────────────────────


class _SuccessWorkflow:
    """결정론 성공 대역 — **실 LLM을 안 부른다.**

    ⚠ 결과 종류는 이 파일의 축이 아니다(원장 결합을 본다) — 계약상 유효한 최소 결과를 낸다.
    """

    async def run(self, request: object, context: object) -> object:
        from ai.contracts.problem_generation import RejectedInsufficientOutcome

        del request, context
        return RejectedInsufficientOutcome(status_reason="g3_fixture_no_weakness_data")


def test_problem_generation_enqueue_then_worker_binds_the_ledger() -> None:
    """🔴 **실제 `ProblemGenerationEnqueuer` → 실 PG → 실제 runner → 실제 원장**.

    ⚠ `finally: _record_execution()`이 불렸는지를 **소스 grep으로 대신하지 않는다** —
    **실제 `AI_RUN` 행**을 본다.
    """
    tenant = _tenant("pg")

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        from ai.contracts.problem_generation import ProblemRequest
        from ai.problem_generation.assembly import (
            ProblemGenerationRunner,
            _InMemoryProblemRequestStore,
            _InMemoryProblemResultStore,
        )
        from ai.problem_generation.enqueue import ProblemGenerationEnqueuer

        supervisor = _supervisor(sessions)
        requests = _InMemoryProblemRequestStore()
        results = _InMemoryProblemResultStore()
        collector = LlmCallCollector()
        enqueuer = ProblemGenerationEnqueuer(
            supervisor=supervisor, request_store=requests, now=lambda: _NOW
        )
        runner = ProblemGenerationRunner(
            supervisor=supervisor,
            request_store=requests,
            result_store=results,
            workflow=_SuccessWorkflow(),
            run_store=PgRunStore(sessionmaker=sessions, clock=lambda: _NOW),
            call_log=collector,
            verify_config_version="verify-config.v1",
            prompt_version="v3",
            lease_owner="worker-g3-pg",
            now=lambda: _NOW,
        )

        request = ProblemRequest.model_validate(
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
        job = await enqueuer.enqueue(request)

        # ── enqueue 직후 ──────────────────────────────────────
        rows = await _agent_runs(sessions, tenant)
        assert len(rows) == 1, f"AGENT_RUN이 1건이 아니다: {rows}"
        assert rows[0].run_id == job.execution_id
        #: 🔴 **부모 선삽입 없음** — 여기서 `AI_RUN`은 아직 비어야 한다.
        assert await _ai_runs(sessions, tenant) == []
        restored = await PgJobStore(sessionmaker=sessions).get(
            tenant_id=tenant, job_id=job.job_id
        )
        assert restored is not None and restored.execution_id == job.execution_id

        # ── worker 완주 후 ────────────────────────────────────
        done = await runner.run_next(tenant_id=tenant)
        assert done is not None, "pg runner가 잡을 집지 않았다"
        assert done.phase is JobPhase.SUCCEEDED, f"종단 상태가 {done.phase}다"
        assert done.result_ref, "result_ref가 없다"

        ledger = await _ai_runs(sessions, tenant)
        assert len(ledger) == 1, f"AI_RUN이 1건이 아니다: {ledger}"
        assert ledger[0].execution_id == job.execution_id
        assert ledger[0].tenant_id == tenant
        assert ledger[0].capability == Capability.PROBLEM_GENERATION.value

        findings = await _audit(sessions, tenant)
        assert [f.verdict for f in findings] == [LedgerVerdict.OK], [
            f.reason for f in findings
        ]

    _run(scenario, tenant=tenant)


# ── §8 · 세 경로를 합친 감사 판정 ───────────────────────────────


def test_all_three_capabilities_audit_together_in_one_tenant() -> None:
    """🔴 **한 테넌트에서 세 실행이 끝난 뒤 감사가 세 판정을 각각 낸다.**

    ⚠ **행 순서에 기대지 않는다** — `execution_id`로 정확 대조한다.
    ⚠ **`separate_gap`을 숨기지 않는다** — 리포트에 ㉾가 남아야 하지만 **관문은 막지 않는다.**
    """
    tenant = _tenant("all")

    async def scenario(sessions: async_sessionmaker[AsyncSession]) -> None:
        from ai.contracts.problem_generation import ProblemRequest
        from ai.db.repositories.probe_stores import PgProfileStore, PgSpecResultStore
        from ai.import_mapping.probe.enqueue import ProbeEnqueuer
        from ai.import_mapping.probe.worker import MappingProbeRunner
        from ai.import_mapping.profiling import (
            ColumnProfile,
            SheetProfile,
            SourceProfile,
        )
        from ai.problem_generation.assembly import (
            ProblemGenerationRunner,
            _InMemoryProblemRequestStore,
            _InMemoryProblemResultStore,
        )
        from ai.problem_generation.enqueue import ProblemGenerationEnqueuer

        counsel_job = await _run_counsel(sessions, tenant)

        supervisor = _supervisor(sessions)
        requests = _InMemoryProblemRequestStore()
        pg_job = await ProblemGenerationEnqueuer(
            supervisor=supervisor, request_store=requests, now=lambda: _NOW
        ).enqueue(
            ProblemRequest.model_validate(
                {
                    "target_kind": "student",
                    "target_ref": "student-all",
                    "target_source": "teacher_manual",
                    "manual_targets": ["language.sentence_structure"],
                    "snapshot_hash": "snapshot-all",
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
        await ProblemGenerationRunner(
            supervisor=supervisor,
            request_store=requests,
            result_store=_InMemoryProblemResultStore(),
            workflow=_SuccessWorkflow(),
            run_store=PgRunStore(sessionmaker=sessions, clock=lambda: _NOW),
            call_log=LlmCallCollector(),
            verify_config_version="verify-config.v1",
            prompt_version="v3",
            lease_owner="worker-all-pg",
            now=lambda: _NOW,
        ).run_next(tenant_id=tenant)

        profiles = PgProfileStore(sessionmaker=sessions)
        probe_job = await ProbeEnqueuer(
            supervisor=supervisor, profile_store=profiles, now=lambda: _NOW
        ).enqueue(
            tenant_id=tenant,
            profile=SourceProfile(
                filename="all.xlsx",
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
            file_hash="hash-all",
        )
        await MappingProbeRunner(
            supervisor=supervisor,
            profile_store=profiles,
            spec_store=PgSpecResultStore(sessionmaker=sessions),
            step_sink=PgAgentStepSink(sessionmaker=sessions),
            checkpointer=InMemorySaver(),
            loop_max=3,
            lease_owner="worker-all-probe",
        ).run_next(tenant_id=tenant)

        findings = await _audit(sessions, tenant)
        by_run = {f.observation.run_id: f for f in findings}
        assert len(findings) == 3, f"세 실행이 안 보인다: {len(findings)}건"

        assert by_run[counsel_job.execution_id].verdict is LedgerVerdict.OK
        assert by_run[pg_job.execution_id].verdict is LedgerVerdict.OK
        assert by_run[probe_job.execution_id].verdict is LedgerVerdict.SEPARATE_GAP

        counts = summarize(findings)
        assert counts[LedgerVerdict.VIOLATION] == 0
        assert counts[LedgerVerdict.UNKNOWN] == 0
        assert counts[LedgerVerdict.SEPARATE_GAP] == 1
        assert counts[LedgerVerdict.OK] == 2
        #: 🔴 ㉾가 남아 있어도 **관문은 막지 않는다** — 현재 확정 판정이다.
        assert not preflight_blocks(findings)

    _run(scenario, tenant=tenant)
