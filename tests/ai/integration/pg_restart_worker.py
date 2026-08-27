"""PG 재시작 E2E의 독립 워커 프로세스 진입점."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ai.agents.supervisor import Supervisor
from ai.contracts.agents import WorkerJob, WorkerKind
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.problem_generation import (
    ItemResult,
    ProblemItemStatus,
    ProblemRequest,
    ProblemSetResult,
    ProblemSetStatus,
    TargetSource,
)
from ai.db.repositories.agent_job import PgJobStore
from ai.db.repositories.problem_candidate_store import PgCandidateStore
from ai.db.repositories.problem_job_queue import pending_problem_tenants
from ai.db.repositories.problem_runtime_store import (
    PgProblemRequestStore,
    PgProblemResultStore,
)
from ai.db.repositories.problem_set_store import PgProblemSetStore
from ai.db.repositories.run_store import PgRunStore
from ai.db.settings import get_db_settings
from ai.problem_generation.application.drain import ProblemDrainLoop
from ai.problem_generation.domain.models import CandidateSnapshot
from ai.problem_generation.enqueue import ProblemGenerationEnqueuer

_TENANT = "tenant-pg-process-restart"
_JOB_ID = UUID("00000000-0000-4000-8000-00000000d121")
_EXECUTION_ID = UUID("00000000-0000-4000-8000-00000000d122")
_SET_ID = UUID("00000000-0000-4000-8000-00000000d123")
_ITEM_ID = UUID("00000000-0000-4000-8000-00000000d124")
_CONTEXT_ID = UUID("00000000-0000-4000-8000-00000000d125")


def _request() -> ProblemRequest:
    return ProblemRequest.model_validate(
        {
            "target_kind": "student",
            "target_ref": "student-restart",
            "target_source": "teacher_manual",
            "manual_targets": ["language.grammar.sentence.structure"],
            "snapshot_hash": f"sha256:{'a' * 64}",
            "taxonomy_version": "v1",
            "area_tag": "language",
            "type_tags": ["concept"],
            "item_format": "mcq",
            "count": 1,
            "request_id": "request-process-restart",
            "tenant_id": _TENANT,
            "idempotency_key": "idem-process-restart",
        }
    )


def _candidate() -> CandidateSnapshot:
    return CandidateSnapshot.model_validate(
        {
            "set_id": str(_SET_ID),
            "slot_index": 0,
            "attempt_no": 1,
            "item": {
                "area_tag": "language",
                "type_tag": "concept",
                "item_format": "mcq",
                "skill_node_id": "language.grammar.sentence.structure",
                "stem": "문장 구조에 대한 설명으로 옳은 것은?",
                "choices": [
                    {
                        "no": no,
                        "text": f"선택지 {no}",
                        "why_wrong": None if no == 1 else f"선택지 {no}는 근거와 다르다.",
                        "misconception_tag": None if no == 1 else "rule_scope_misread",
                    }
                    for no in range(1, 6)
                ],
                "answer": {"correct_no": 1},
                "rationale": "문법 규칙 근거에 따라 1번이 옳다.",
                "evidence": [{"kind": "grammar_rule", "ref": "rule://sentence-structure"}],
            },
            "solve_result": {
                "chosen": 1,
                "reasoning": "근거와 일치한다.",
                "confidence": 0.99,
                "multiple_answers_possible": False,
                "target_skill_node_id": "language.grammar.sentence.structure",
                "measured_skill_node_id": "language.grammar.sentence.structure",
                "aligned": True,
                "alignment_confidence": 0.99,
                "alignment_reason": "같은 기능을 측정한다.",
            },
            "context_pack_id": str(_CONTEXT_ID),
            "difficulty_est": 0.5,
            "difficulty_band": "medium",
            "snapshot_hash": f"sha256:{'b' * 64}",
        }
    )


def _supervisor(sessions: async_sessionmaker[AsyncSession]) -> Supervisor:
    return Supervisor(
        store=PgJobStore(sessionmaker=sessions),
        lease_duration=timedelta(seconds=1),
        priority_aging_interval=timedelta(minutes=10),
    )


async def _seed(sessions: async_sessionmaker[AsyncSession]) -> dict[str, object]:
    ids = iter((_JOB_ID, _EXECUTION_ID))
    job = await ProblemGenerationEnqueuer(
        supervisor=_supervisor(sessions),
        request_store=PgProblemRequestStore(sessionmaker=sessions),
        new_id=lambda: next(ids),
    ).enqueue(_request())
    return {"phase": job.phase.value, "job_id": str(job.job_id)}


async def _lease_and_exit(
    sessions: async_sessionmaker[AsyncSession],
) -> dict[str, object]:
    supervisor = _supervisor(sessions)
    job = await supervisor.lease_next(
        tenant_id=_TENANT,
        worker_kind=WorkerKind.PROBLEM_GENERATION,
        lease_owner="worker-before-restart",
    )
    assert job is not None
    running = await supervisor.start(
        tenant_id=_TENANT,
        job_id=job.job_id,
        lease_owner="worker-before-restart",
        lease_generation=job.lease_generation,
        checkpoint_ref=str(job.job_id),
    )
    request = await PgProblemRequestStore(sessionmaker=sessions).get(
        running.payload_ref, tenant_id=_TENANT
    )
    assert request is not None
    context = ExecutionContext(
        execution_id=running.execution_id,
        tenant_id=_TENANT,
        capability=Capability.PROBLEM_GENERATION,
        input_snapshot_hash=request.snapshot_hash,
        versions=VersionSet(
            pipeline_version="0.1.0",
            engine_version="problem-generation-0.1",
            schema_version="0.1",
            contract_version="0.1",
            prompt_version="v8",
            graph_version="curriculum-five-area-v1",
            taxonomy_version=request.taxonomy_version,
            verify_config_version="verify-config.v1",
        ),
    )
    assert running.started_at is not None
    await PgRunStore(sessionmaker=sessions).begin_run(
        context.to_run_metadata(created_at=running.started_at)
    )
    await PgProblemSetStore(sessionmaker=sessions, tenant_id=_TENANT).create(
        set_id=_SET_ID,
        request=request,
        execution_context=context,
        diagnostic_purpose=False,
    )
    candidate_ref = await PgCandidateStore(sessionmaker=sessions, tenant_id=_TENANT).put(
        _candidate()
    )
    return {
        "phase": running.phase.value,
        "request_id": request.request_id,
        "candidate_ref": candidate_ref,
    }


async def _recover(sessions: async_sessionmaker[AsyncSession]) -> dict[str, object]:
    supervisor = _supervisor(sessions)
    completed = asyncio.Event()
    observed: dict[str, object] = {}

    async def run_next(tenant_id: str) -> WorkerJob | None:
        job = await supervisor.lease_next(
            tenant_id=tenant_id,
            worker_kind=WorkerKind.PROBLEM_GENERATION,
            lease_owner="worker-after-restart",
        )
        if job is None:
            return None
        request = await PgProblemRequestStore(sessionmaker=sessions).get(
            job.payload_ref, tenant_id=tenant_id
        )
        assert request is not None
        candidate = await PgCandidateStore(sessionmaker=sessions, tenant_id=tenant_id).get(
            f"item-candidate:{_SET_ID}:0:1"
        )
        assert candidate.snapshot_hash == f"sha256:{'b' * 64}"
        await supervisor.start(
            tenant_id=tenant_id,
            job_id=job.job_id,
            lease_owner="worker-after-restart",
            lease_generation=job.lease_generation,
            checkpoint_ref=str(job.job_id),
        )
        outcome = ProblemSetResult(
            set_id=_SET_ID,
            status=ProblemSetStatus.GENERATED,
            target_source=TargetSource.TEACHER_MANUAL,
            personalized=False,
            requested_count=1,
            processed_count=1,
            unstarted_count=0,
            items=(
                ItemResult(
                    item_id=_ITEM_ID,
                    status=ProblemItemStatus.VERIFIED,
                    attempt_no=1,
                ),
            ),
        )
        result_ref = await PgProblemResultStore(sessionmaker=sessions).put(
            tenant_id=tenant_id,
            job_id=job.job_id,
            result=outcome,
        )
        succeeded = await supervisor.succeed(
            tenant_id=tenant_id,
            job_id=job.job_id,
            lease_owner="worker-after-restart",
            lease_generation=job.lease_generation,
            result_ref=result_ref,
        )
        observed.update(
            phase=succeeded.phase.value,
            recovery_count=succeeded.recovery_count,
            request_id=request.request_id,
            result_ref=result_ref,
        )
        completed.set()
        return succeeded

    drain = ProblemDrainLoop(
        run_next=run_next,
        discover_tenants=lambda: pending_problem_tenants(sessionmaker=sessions, limit=10),
        max_jobs_per_cycle=1,
        cycle_interval_seconds=0.01,
        idle_interval_seconds=0.01,
        failure_backoff_initial_seconds=0.01,
        failure_backoff_max_seconds=0.02,
        shutdown_grace_seconds=1.0,
    )
    await drain.start()
    await asyncio.wait_for(completed.wait(), timeout=3.0)
    await drain.stop()
    return observed


async def _verify(sessions: async_sessionmaker[AsyncSession]) -> dict[str, object]:
    supervisor = _supervisor(sessions)
    job = await supervisor.get(tenant_id=_TENANT, job_id=_JOB_ID)
    assert job is not None and job.result_ref is not None
    result = await PgProblemResultStore(sessionmaker=sessions).get(
        job.result_ref, tenant_id=_TENANT
    )
    assert isinstance(result, ProblemSetResult)
    assert (
        await PgProblemResultStore(sessionmaker=sessions).get(
            job.result_ref, tenant_id="tenant-other"
        )
        is None
    )
    return {
        "phase": job.phase.value,
        "recovery_count": job.recovery_count,
        "set_id": str(result.set_id),
    }


async def _main(mode: str) -> None:
    engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        actions = {
            "seed": _seed,
            "lease-and-exit": _lease_and_exit,
            "recover": _recover,
            "verify": _verify,
        }
        result = await actions[mode](sessions)
        print(json.dumps(result, sort_keys=True))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1]))
