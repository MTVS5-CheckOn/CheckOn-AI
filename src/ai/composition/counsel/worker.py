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

import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Final
from uuid import UUID, uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver

from ai.agents.supervisor import Supervisor, system_utc_now
from ai.composition.counsel.graph import LlmCircuitOpenError, build_counsel_graph
from ai.composition.counsel.provider import CounselPlanner, DraftWriter
from ai.composition.counsel.settings import get_counsel_settings
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

logger = logging.getLogger(__name__)


#: 잡 단위 error_code — error_codes §2.5에 등재된 어휘다(`worker_recovery_exhausted` 선례의
#: snake_case·접두 생법). ⚠ 학생 단위 `fail_reason`(graph.py의 `context_missing` 등)과
#: **문자열이 겹치지 않게** 접두를 붙였다 — 대시보드가 문자열로 집진하면 잡 장애와 학생
#: 정상 스킵이 섞여 장애 오판이 된다.
ERROR_CONTEXT_BUNDLE_MISSING: Final = "context_bundle_missing"
ERROR_CONTEXT_HASH_MISMATCH: Final = "context_hash_mismatch"
ERROR_TENANT_MISMATCH: Final = "tenant_mismatch"
ERROR_WORKER_INTERNAL: Final = "worker_internal_error"


class ContextHashMismatchError(ValueError):
    """`context_ref` 역참조 해시가 state의 `context_hash`와 다르다 — 체크포인트 손상(불변식 ④)."""


class ContextBundleMissingError(ValueError):
    """`payload_ref`가 해소되지 않는다 — 결과 계약을 확정할 수 없다."""


class TenantMismatchError(ValueError):
    """묶음의 tenant가 잡의 tenant와 다르다 — 테넌트 격리 위반(CLAUDE.md §4)."""


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
        llm_failure_circuit: int | None = None,
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
        #: 연속 LLM 실패 학생 수 임계 — §1.3 "LLM 연속 실패 3학생(서킷)". 하드코딩하지 않고
        #: settings에서 읽되 생성자 주입이 이긴다(테스트 결정론).
        self._circuit = (
            llm_failure_circuit
            if llm_failure_circuit is not None
            else get_counsel_settings().counsel_llm_failure_circuit
        )

    async def run_next(self, *, tenant_id: str) -> WorkerJob | None:
        """다음 counsel_pack 잡을 lease해 실행하고 succeeded로 수렴한다. 없으면 None."""
        job = await self._sv.lease_next(
            tenant_id=tenant_id,
            worker_kind=WorkerKind.COUNSEL_PACK,
            lease_owner=self._lease_owner,
        )
        if job is None:
            return None
        return await self._run_guarded(job)

    async def _run_guarded(self, job: WorkerJob) -> WorkerJob:
        """복구 불가 예외를 **즉시** 수렴시킨다 — running 방치 금지(3-5).

        방치하면 lease 만료 recovery를 `max_recovery_attempts`(기본 3)까지 태우고 그때마다
        전체 재실행이라 LLM 비용이 3배가 된다. 실패는 정직하게 즉시 기록한다.

        ⚠ `BaseException`(프로세스 kill·SystemExit)은 잡지 않는다 — 그건 lease 만료·recovery가
        다룰 영역이고 워커가 대신 판정할 수 없다.
        """
        try:
            return await self._execute(job)
        except LlmCircuitOpenError:
            return await self._sv.pause(
                tenant_id=job.tenant_id,
                job_id=job.job_id,
                lease_owner=self._lease_owner,
                lease_generation=job.lease_generation,
                checkpoint_ref=str(job.job_id),
            )
        except ContextBundleMissingError:
            return await self._fail(job, ERROR_CONTEXT_BUNDLE_MISSING)
        except TenantMismatchError:
            return await self._fail(job, ERROR_TENANT_MISMATCH)
        except ContextHashMismatchError:
            return await self._fail(job, ERROR_CONTEXT_HASH_MISMATCH)
        except Exception:  # noqa: BLE001 — 미분류도 방치하지 않는다(정직한 수렴)
            logger.exception("counsel_pack 워커 미분류 실패 job=%s", job.job_id)
            return await self._fail(job, ERROR_WORKER_INTERNAL)

    async def _fail(self, job: WorkerJob, error_code: str) -> WorkerJob:
        return await self._sv.fail(
            tenant_id=job.tenant_id,
            job_id=job.job_id,
            lease_owner=self._lease_owner,
            lease_generation=job.lease_generation,
            error_code=error_code,
        )

    async def _execute(self, job: WorkerJob) -> WorkerJob:
        bundle = await self._contexts.get(job.payload_ref, tenant_id=job.tenant_id)
        if bundle is None:
            raise ContextBundleMissingError(job.payload_ref)
        if bundle.tenant_id != job.tenant_id:  # 저장소 격리의 이중 방어
            raise TenantMismatchError(job.payload_ref)
        thread_id = str(job.job_id)

        # ② start — checkpoint_ref 연결 + fencing(lease_generation).
        await self._sv.start(
            tenant_id=job.tenant_id,
            job_id=job.job_id,
            lease_owner=self._lease_owner,
            lease_generation=job.lease_generation,
            checkpoint_ref=thread_id,
        )

        # ③ 그래프 실행 — 학생 경계마다 체크포인트(§1.3).
        graph = build_counsel_graph(
            planner=self._planner,
            writer=self._writer,
            contexts=bundle.contexts,
            execution_context=_execution_context(job),
            checkpointer=self._checkpointer,
            regen_max=self._regen_max,
            llm_failure_circuit=self._circuit,
            draft_store=self._drafts,
            tenant_id=job.tenant_id,
            agent_run_id=job.job_id,
            new_draft_id=self._new_id,
            now=self._now,
        )
        config = {"configurable": {"thread_id": thread_id}}
        graph_input = await self._resume_input(graph, config, job, bundle)
        final = await graph.ainvoke(graph_input, config=config)

        # ④ agent_step 영속 — 학생 처리 이력(AGENT_STEP 1:1, 마스킹 통과분만).
        # agent_run_id = job_id (AGENT_STEP.agent_run_id → AGENT_RUN.id, §5 1:1 투영).
        # **멱등**: 재개 시 이 루프가 다시 돌아 `(agent_run_id, seq)`가 중복된다 — 이미
        # 기록된 seq는 건너뛴다(재개가 생긴 순간 이건 잠재 버그에서 실버그가 된다).
        recorded = {step.seq for step in await self._steps.steps(job.job_id)}
        for seq, result in enumerate(final["results"]):
            if seq in recorded:
                continue
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

    async def _resume_input(
        self,
        graph: Any,  # noqa: ANN401 — LangGraph 컴파일 그래프 제네릭
        config: dict[str, Any],
        job: WorkerJob,
        bundle: ContextBundleRecord,
    ) -> CounselPackState | None:
        """체크포인트가 있으면 **input=None으로 재개**, 없으면 init을 투입한다(§1.3).

        `None`을 넘기면 LangGraph가 저장된 state를 `cursor`부터 이어간다 — init을 다시 넣으면
        plan 재호출·완료 학생 전원 재생성·draft_id 이중 발급이 된다("cursor부터 · 재생성
        없음(멱등)"과 정면 충돌).

        **불변식 ④는 여기가 진짜 자리다** — 저장된 state의 `context_hash`를 **재역참조한
        묶음의 해시**와 대조한다. 이전 코드는 방금 만든 init의 해시를 같은 값과 비교하는
        자기 대조라 손상을 검출할 수 없었다.
        """
        snapshot = await graph.aget_state(config)
        stored: Mapping[str, Any] | None = getattr(snapshot, "values", None) or None
        if stored and stored.get("cursor") is not None:
            if stored.get("context_hash") != bundle.content_hash:
                raise ContextHashMismatchError(job.payload_ref)
            return None  # 재개 — 저장된 state의 cursor부터
        return CounselPackState(
            tenant_id=job.tenant_id,
            class_ref=bundle.class_ref,
            student_refs=sorted(bundle.contexts),  # 처리 순서 고정(재현성)
            context_ref=job.payload_ref,
            context_hash=bundle.content_hash,
            plan_version=job.payload_hash,
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


__all__ = [
    "ERROR_CONTEXT_BUNDLE_MISSING",
    "ERROR_CONTEXT_HASH_MISMATCH",
    "ERROR_TENANT_MISMATCH",
    "ERROR_WORKER_INTERNAL",
    "ContextBundleMissingError",
    "ContextHashMismatchError",
    "CounselPackRunner",
    "TenantMismatchError",
]
