"""mapping_probe WorkerJob 어댑터 — 디스패처 lease→start→succeed로 그래프를 실행한다(§5).

operation=mapping_probe.resolve 잡을:
  ① lease_next(MAPPING_PROBE) → ② start(checkpoint_ref=thread_id, lease_generation)
  → ③ 그래프 실행(도구 호출마다 체크포인트) → ④ agent_step 영속 → ⑤ spec 저장(result_ref)
  → ⑥ succeed(result_ref, lease_generation).
lease_generation을 start·succeed에 그대로 전달해 fencing 규약(§5.1)을 지킨다 — 만료된
실행자의 후속 저장은 저장소가 StaleLeaseError로 거부한다. 부분 미해결이어도 succeeded
(error_codes §2.5) — 결과 계약(spec_draft)을 저장했으므로.

LLM·도구는 Protocol+Fake 기본(실 LLM·게이트웨이 후속). checkpointer는 주입(테스트 InMemorySaver).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Final
from uuid import UUID, uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver

from ai.agents.supervisor import Supervisor
from ai.contracts.agents import WorkerJob, WorkerKind
from ai.import_mapping.probe.graph import build_probe_graph, graph_recursion_limit
from ai.import_mapping.probe.planner import FakeProbePlanner, ProbePlanner
from ai.import_mapping.probe.state import MappingProbeState, MappingSpecDraft
from ai.import_mapping.probe.stores import (
    AgentStepRecord,
    AgentStepSink,
    ProfileStore,
    SpecRecord,
    SpecResultStore,
    deserialize_profile,
)
from ai.import_mapping.probe.tools import FakeProbeTools

_SPEC_VERSION = 1

logger = logging.getLogger(__name__)

#: 🔴 **미분류 실패의 사유** — `error_codes` §2.1의 공용 어휘를 쓴다(counsel과 같은 문자열).
#: ⚠ pg는 자기 값(`problem_worker_internal`)을 쓰는데 **그건 B 소유 축**이라 여기서 안 맞춘다 —
#: 같은 어휘를 쓰는 것이 원장을 읽는 쪽에 싸다(99 #18).
ERROR_WORKER_INTERNAL: Final = "worker_internal_error"


class MappingProbeRunner:
    """조사 잡 하나를 lease→실행→succeed로 처리한다(디스패처 워커 루프의 mapping_probe 담당)."""

    def __init__(
        self,
        *,
        supervisor: Supervisor,
        profile_store: ProfileStore,
        spec_store: SpecResultStore,
        step_sink: AgentStepSink,
        checkpointer: BaseCheckpointSaver[Any],
        planner: ProbePlanner | None = None,
        loop_max: int,
        lease_owner: str,
        new_id: Callable[[], UUID] = uuid4,
    ) -> None:
        self._sv = supervisor
        self._profiles = profile_store
        self._specs = spec_store
        self._steps = step_sink
        self._checkpointer = checkpointer
        self._planner = planner or FakeProbePlanner()
        self._loop_max = loop_max
        self._lease_owner = lease_owner
        self._new_id = new_id

    async def run_next(self, *, tenant_id: str) -> WorkerJob | None:
        """다음 mapping_probe 잡을 lease해 실행하고 succeeded로 수렴한다. 없으면 None."""
        job = await self._sv.lease_next(
            tenant_id=tenant_id,
            worker_kind=WorkerKind.MAPPING_PROBE,
            lease_owner=self._lease_owner,
        )
        if job is None:
            return None
        return await self._run_guarded(job)

    async def _run_guarded(self, job: WorkerJob) -> WorkerJob:
        """예외를 **잡 종단으로 수렴**시킨다 — running 방치 금지(99 #18).

        방치하면 lease 만료 recovery를 `max_recovery_attempts`(**기본 3**)까지 태우고
        그때마다 **전체 재실행**이다. 원장에는 사유가 하나도 안 남는다.

        🔴 **counsel의 `_run_guarded`를 복사하지 않았다.** 그쪽이 예외를 넷 열거해
        `pause`/`fail`로 갈라 떨구는데 **그 넷이 probe 축에 하나도 없다**(전수 0건 —
        `LlmCircuitOpenError`·`ContextBundleMissingError`·`TenantMismatchError`·
        `ContextHashMismatchError`). 복사하면 **못 나는 예외를 잡는 코드**가 생긴다(#22 부류).

        ⚠ **`pause`가 없는 이유** — `pause`는 **되돌릴 수 있는 배압**에 쓰고 counsel이 그것을
        쓰는 유일한 자리가 서킷인데 **probe에는 서킷이 0건**이다. probe가 낼 수 있는 것은
        `ValueError`(참조 해소 실패·컬럼 수 불일치·미지 도구)·`MappingInferenceError`·
        `GraphRecursionError` 계열이고 **전부 「이대로는 안 된다」**라 종단이 정답이다.

        🔴 **수렴한 뒤 다시 던진다**(pg 형태 · counsel은 삼킨다). 이유 둘:
        ⓐ **호출자에게 사실을 숨기지 않는다** — probe는 프로덕션 드레인이 아직 없어
          (전수: `imports.py`는 enqueue만 한다) 삼켜서 얻을 가용성이 없다.
        ⓑ `test_probe_recursion_limit`이 **전파를 현재 동작으로 단정**하고 있어, 재던지면
          그 단정이 **그대로 참**이다 — 축이 다른 테스트를 이 변경이 흔들지 않는다.

        ⚠ **수렴 자체가 실패해도 원래 예외를 잃지 않는다** — `fail`이 터지면 그것을 기록만
        하고 원래 예외를 올린다(pg가 같은 형태다).
        """
        try:
            return await self._execute(job)
        except Exception:
            logger.exception("mapping_probe 워커 실패 job=%s", job.job_id)
            try:
                await self._sv.fail(
                    tenant_id=job.tenant_id,
                    job_id=job.job_id,
                    lease_owner=self._lease_owner,
                    lease_generation=job.lease_generation,
                    error_code=ERROR_WORKER_INTERNAL,
                )
            except Exception:  # noqa: BLE001 — 수렴 실패를 원래 예외로 덮지 않는다
                logger.exception("mapping_probe 실패 수렴 중 오류 job=%s", job.job_id)
            raise

    async def _execute(self, job: WorkerJob) -> WorkerJob:
        profile_rec = await self._profiles.get(job.payload_ref)
        if profile_rec is None:
            raise ValueError(f"source_profile 참조 해소 실패: {job.payload_ref}")
        profile = deserialize_profile(profile_rec.sheets)
        thread_id = str(job.job_id)

        # ② start — checkpoint_ref 연결 + fencing(lease_generation).
        await self._sv.start(
            tenant_id=job.tenant_id,
            job_id=job.job_id,
            lease_owner=self._lease_owner,
            lease_generation=job.lease_generation,
            checkpoint_ref=thread_id,
        )

        # ③ 그래프 실행 — 도구 호출마다 체크포인트(§2.3).
        graph = build_probe_graph(
            tools=FakeProbeTools(profile),
            planner=self._planner,
            loop_max=self._loop_max,
            checkpointer=self._checkpointer,
        )
        columns = [c.name for sheet in profile.sheets for c in sheet.columns]
        init = MappingProbeState(
            tenant_id=job.tenant_id,
            source_profile_id=profile_rec.id,
            sheets_meta={"columns": columns},
        )
        # ainvoke — async 체크포인터(AsyncPostgresSaver)를 구동한다. InMemorySaver도 호환.
        # 🔴 **호출마다 상한을 명시한다**(불변식 6 · 99 #08 ⓑ) — 안 주면 langgraph 기본값
        #    `getenv("LANGGRAPH_DEFAULT_RECURSION_LIMIT", "10007")`이 쓰이는데 그건
        #    **사실상 무한**이고 **BE 운영이 만질 수 있는 저장소 밖 값**이다.
        final = await graph.ainvoke(
            init,
            config={
                "configurable": {"thread_id": thread_id},
                "recursion_limit": graph_recursion_limit(loop_max=self._loop_max),
            },
        )
        draft: MappingSpecDraft = final["spec_draft"]

        # ④ agent_step 영속 — 도구 호출 로그(AGENT_STEP 컬럼과 1:1, 마스킹 통과분만).
        # agent_run_id = job_id — AGENT_STEP.agent_run_id → AGENT_RUN.id 이고 §5에서
        # WorkerJob은 AGENT_RUN(id=job_id)에 1:1 투영된다(execution_id는 run_id=ai_run 연결용).
        for step in final["steps"]:
            await self._steps.record(
                AgentStepRecord(
                    id=self._new_id(),
                    agent_run_id=job.job_id,
                    seq=step.seq,
                    node_name="tool_call",
                    tool_called=step.tool,
                    tool_args_masked=dict(step.tool_args),
                    llm_call_id=None,
                    outcome="ok",
                )
            )

        # ⑤ spec 저장 → result_ref(본문 복제 없음 — 슈퍼바이저엔 참조만).
        result_ref = await self._specs.put(
            SpecRecord(
                id=self._new_id(),
                tenant_id=job.tenant_id,
                source_profile_id=profile_rec.id,
                version=_SPEC_VERSION,
                spec=draft.model_dump(mode="json"),
                status="succeeded",
                probe_agent_run=job.job_id,  # MAPPING_SPEC.probe_agent_run → AGENT_RUN.id(=job_id)
            )
        )

        # ⑥ succeed — 부분 미해결이어도 결과 계약을 저장했으므로 succeeded(error_codes §2.5).
        return await self._sv.succeed(
            tenant_id=job.tenant_id,
            job_id=job.job_id,
            lease_owner=self._lease_owner,
            lease_generation=job.lease_generation,
            result_ref=result_ref,
        )
