"""counsel_pack WorkerJob 어댑터 — 디스패처 lease→start→succeed로 그래프를 실행한다(§5).

`probe/worker.py`와 같은 골격이다:
  ① lease_next(COUNSEL_PACK) → ② start(checkpoint_ref=thread_id, lease_generation)
  → ③ 그래프 실행(학생 경계마다 체크포인트) → ④ agent_step 영속 → ⑤ 결과 저장(result_ref)
  → ⑥ succeed(result_ref, lease_generation).

`lease_generation`을 start·succeed에 그대로 전달해 **fencing 규약(§5.1)**을 지킨다.

**부분 미해결도 succeeded**(error_codes §2.5) — "22명 중 19명 생성·2명 데이터 부족·1명 실패"는
명시적 결과 계약을 저장했으므로 Job은 `succeeded`다. 도메인 상태(`DraftStatus`)와
실행 phase(`JobPhase`)를 섞지 않는다.

**불변식 ④:** 재개·기동 시 `context_ref`를 역참조한 묶음의 해시를 state의 `context_hash`와
대조하고, 불일치면 손상된 체크포인트로 실패시킨다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver

from ai.agents.supervisor import Supervisor, system_utc_now
from ai.composition.counsel.graph import build_counsel_graph
from ai.composition.counsel.provider import CounselPlanner, DraftWriter
from ai.composition.counsel.state import CounselPackState
from ai.composition.counsel.stores import (
    AgentStepRecord,
    AgentStepSink,
    ContextBundleRecord,
    ContextStore,
    CounselPackResultRecord,
    DraftResultStore,
    PackResultStore,
)
from ai.contracts.agents import WorkerJob, WorkerKind
from ai.contracts.execution import ExecutionContext


class ContextHashMismatchError(ValueError):
    """`context_ref` 역참조 해시가 state의 `context_hash`와 다르다 — 체크포인트 손상(불변식 ④)."""


class CounselPackRunner:
    """상담팩 잡 하나를 lease→실행→succeed로 처리한다."""

    def __init__(
        self,
        *,
        supervisor: Supervisor,
        context_store: ContextStore,
        step_sink: AgentStepSink,
        draft_store: DraftResultStore,
        pack_store: PackResultStore,
        planner: CounselPlanner,
        writer: DraftWriter,
        checkpointer: BaseCheckpointSaver[Any],
        regen_max: int,
        lease_owner: str,
        new_id: Callable[[], UUID] = uuid4,
        now: Callable[[], datetime] = system_utc_now,
    ) -> None:
        self._sv = supervisor
        self._contexts = context_store
        self._drafts = draft_store
        self._packs = pack_store
        self._steps = step_sink
        self._planner = planner
        self._writer = writer
        self._checkpointer = checkpointer
        self._regen_max = regen_max
        self._lease_owner = lease_owner
        self._new_id = new_id
        self._now = now

    async def run_next(self, *, tenant_id: str) -> WorkerJob | None:
        """다음 counsel_pack 잡을 lease해 실행하고 succeeded로 수렴한다. 없으면 None."""
        job = await self._sv.lease_next(
            tenant_id=tenant_id,
            worker_kind=WorkerKind.COUNSEL_PACK,
            lease_owner=self._lease_owner,
        )
        if job is None:
            return None
        return await self._execute(job)

    async def _execute(self, job: WorkerJob) -> WorkerJob:
        bundle = await self._contexts.get(job.payload_ref, tenant_id=job.tenant_id)
        if bundle is None:
            raise ValueError(f"컨텍스트 묶음 참조 해소 실패: {job.payload_ref}")
        thread_id = str(job.job_id)

        # ② start — checkpoint_ref 연결 + fencing(lease_generation).
        await self._sv.start(
            tenant_id=job.tenant_id,
            job_id=job.job_id,
            lease_owner=self._lease_owner,
            lease_generation=job.lease_generation,
            checkpoint_ref=thread_id,
        )

        init = CounselPackState(
            tenant_id=job.tenant_id,
            class_ref=bundle.class_ref,
            student_refs=sorted(bundle.contexts),  # 처리 순서 고정(재현성)
            context_ref=job.payload_ref,
            context_hash=bundle.content_hash,
            plan_version=job.payload_hash,
        )
        if init.context_hash != bundle.content_hash:  # 불변식 ④ — 이중 확인
            raise ContextHashMismatchError(job.payload_ref)

        # ③ 그래프 실행 — 학생 경계마다 체크포인트(§1.3).
        graph = build_counsel_graph(
            planner=self._planner,
            writer=self._writer,
            contexts=bundle.contexts,
            execution_context=_execution_context(job),
            checkpointer=self._checkpointer,
            regen_max=self._regen_max,
            draft_store=self._drafts,
            tenant_id=job.tenant_id,
            agent_run_id=job.job_id,
            new_draft_id=self._new_id,
            now=self._now,
        )
        final = await graph.ainvoke(
            init, config={"configurable": {"thread_id": thread_id}}
        )

        # ④ agent_step 영속 — 학생 처리 이력(AGENT_STEP 1:1, 마스킹 통과분만).
        # agent_run_id = job_id (AGENT_STEP.agent_run_id → AGENT_RUN.id, §5 1:1 투영).
        for seq, result in enumerate(final["results"]):
            await self._steps.record(
                AgentStepRecord(
                    id=self._new_id(),
                    agent_run_id=job.job_id,
                    seq=seq,
                    node_name="student",
                    tool_called=None,
                    tool_args_masked={"student_ref": result.student_ref},
                    llm_call_id=None,
                    outcome=result.status.value,
                )
            )

        # ⑤ 결과 계약 저장 — 요약 + 학생별 결과 포인터. **입력 ref(payload_ref)를 결과로
        #    쓰지 않는다** — 백엔드가 역참조하면 초안이 아니라 입력이 나온다.
        result_ref = await self._store_pack(job, bundle, final)

        # ⑥ succeed — 부분 미해결이어도 결과 계약을 저장했으므로 succeeded(error_codes §2.5).
        return await self._sv.succeed(
            tenant_id=job.tenant_id,
            job_id=job.job_id,
            lease_owner=self._lease_owner,
            lease_generation=job.lease_generation,
            result_ref=result_ref,
        )

    async def _store_pack(
        self, job: WorkerJob, bundle: ContextBundleRecord, final: Mapping[str, Any]
    ) -> str:
        """요약+학생별 결과를 `pack://`로 영속하고 그 ref를 돌려준다.

        저장소는 **필수 주입**이다 — 옵셔널로 두면 "결과가 어디에도 도착하지 않는" 이 PR의
        원죄가 조용히 재발한다.
        """
        return await self._packs.put(
            CounselPackResultRecord(
                id=self._new_id(),
                tenant_id=job.tenant_id,
                class_ref=bundle.class_ref,
                summary=final["summary"] or "",
                results=tuple(final["results"]),
                created_at=self._now(),
            )
        )

    async def result_of(self, result_ref: str) -> CounselPackResultRecord | None:
        """`result_ref` 역참조 — 백엔드·테스트가 결과 계약을 읽는 경로."""
        return await self._packs.get(result_ref)


def _execution_context(job: WorkerJob) -> ExecutionContext:
    """워커 실행의 ExecutionContext — LLM 호출 기록(recorder)이 함께 받는다."""
    from ai.contracts.execution import Capability, VersionSet

    return ExecutionContext(
        execution_id=job.execution_id,
        tenant_id=job.tenant_id,
        capability=Capability.COMPOSITION,
        input_snapshot_hash=job.payload_hash,
        versions=VersionSet(
            pipeline_version="0.1",
            engine_version="counsel-pack-0.1",
            schema_version="0.1",
            contract_version="0.1",
        ),
    )


__all__ = ["ContextHashMismatchError", "CounselPackRunner"]
