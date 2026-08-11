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
from datetime import datetime
from typing import Any, Final
from uuid import UUID, uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver

from ai.agents.supervisor import Supervisor, system_utc_now
from ai.contracts.agents import WorkerJob, WorkerKind
from ai.contracts.execution import Capability, ExecutionContext
from ai.db.repositories.run_store import LlmCallCollector, RunStore
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
from ai.import_mapping.versions import import_versions

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
        run_store: RunStore,
        call_log: LlmCallCollector,
        now: Callable[[], datetime] = system_utc_now,
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
        #: 🔴 **기본값을 두지 않는다**(99 ㉾). 여기에 `RunStore | None = None`을 두고
        #: 안에서 만들면 **조립부가 안 넘겨도 검사가 초록**이라 배선 결손이 안 보인다 —
        #: `#37`(counsel 스텝 싱크)이 정확히 그 형태로 넉 달 살아남았다.
        self._runs = run_store
        self._call_log = call_log
        #: 시계 주입 — `datetime.now()` 직접 호출 금지(03 §3).
        self._now = now

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
        # ── 🔴 **여기까지는 「실행 전」이다** — 원장을 만들지 않는다 (99 ㉾) ──
        #    실행할 **입력 자체가 없으면** 실행이 없었던 것이고, 없는 실행의 기록을
        #    지어내는 것은 불변식 8이 요구하는 바가 아니다. counsel이 같은 판단이다
        #    (`bundle_missing`·`tenant_mismatch`는 `_execution_context` 앞에서 죽는다).
        profile_rec = await self._profiles.get(
            job.payload_ref, tenant_id=job.tenant_id
        )
        if profile_rec is None:
            #: ⚠ **부재와 교차 테넌트가 같은 번역을 받는다** — 존재 은닉이다.
            #:   둘 다 *"실행할 입력이 없다"* 이고, 어느 쪽인지 말하면 남의 테넌트에
            #:   그 ref가 있다는 사실이 새어 나간다(counsel의 GET 404와 같은 판단).
            raise ValueError(f"source_profile 참조 해소 실패: {job.payload_ref}")
        if profile_rec.tenant_id != job.tenant_id:
            #: 🔴 **이중 방어**(99 #40) — 저장소가 이미 걸렀지만 여기서 다시 본다.
            #:   저장소 대역이 계약을 어겨 **잘못된 행을 돌려줄 수 있고**(테스트 대역·
            #:   미래 구현·캐시 층) 그때도 **실행하면 안 된다.** 격리를 한 층에만 두지 않는다.
            #: ⚠ 문면에 **남의 테넌트 값을 싣지 않는다.**
            raise ValueError(f"source_profile 테넌트 불일치: {job.payload_ref}")
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

        # ── 🔴 **여기부터가 실행이다** — 이 뒤의 성공·실패는 전부 원장을 남긴다 ──
        #    ⚠ 원장의 단위는 **잡 한 건**이다(조사 루프 한 회전이 아니다). 도구 호출은
        #    같은 `execution_id` 아래 `AGENT_STEP`으로 남는다.
        context = ExecutionContext(
            execution_id=job.execution_id,
            tenant_id=job.tenant_id,
            capability=Capability.IMPORT_MAPPING,
            input_snapshot_hash=job.payload_hash,
            #: 🔴 **응답이 읽는 것과 같은 함수**다(99 #20) — 여기서 재선언하면
            #: `meta.versions`와 `AI_RUN`이 갈린다.
            versions=import_versions(),
        )

        #: 🔴 `finally`가 **모든 수렴 경로**를 지나게 하는 플래그다 — counsel과 같은 형태.
        #:   실패 경로에서만 적재 오류를 삼킨다(원인 예외를 덮지 않으려는 것).
        #: 🔴 **실행 경계 직후부터 감싼다**(8/12 보완) — 종전에는 그래프 **조립**과 state
        #:   조립이 `try` **밖**이라, 거기서 죽으면 원장이 0건이고 그건 *"실행이 없었다"* 로
        #:   읽혔다. **`start()`를 이미 지났으므로 실행은 있었다.**
        failed = True
        try:
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
            failed = False
        finally:
            # ③′ 실행 원장 — AI_RUN(+LLM_CALL). 🔴 **`agent_step`보다 먼저**다:
            #    `AGENT_STEP.llm_call_id`가 채워지는 날 그 참조 대상이 먼저 서 있어야 한다
            #    (지금은 항상 `None`이라 눈에 안 보인다 · counsel이 같은 순서다).
            #    ⚠ 호출 0건이어도 남긴다 — 불변식 8은 "모든 실행"이고, 현재
            #      `FakeProbePlanner`는 게이트웨이를 안 타 호출이 실제로 0건이다.
            await self._record_execution(context, swallow_errors=failed)

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

    async def _record_execution(
        self, context: ExecutionContext, *, swallow_errors: bool
    ) -> None:
        """실행 원장 1건 — 성공·그래프 실패·상한 실패 **전부**가 부른다 (99 ㉾).

        ⚠ `take(execution_id)`는 **어느 경로에서도** 불려야 한다. 안 부르면 버킷이 남아
        수집기가 LRU로 밀어낼 때까지 방치되고, 그 축출은 경고 로그로만 나간다 —
        counsel에서 **그 경고를 읽는 사람이 0명**이었다(8/7 전수).

        🔴 **사용 축 셋은 실측값을 옮긴다.** 현재 planner는 `FakeProbePlanner`라 호출이
        0건이고, 그러면 `model_provider`·`model_name`·`generation_params`는 **`None`이
        정답**이다 — 조립부 설정을 여기서 재선언하면 *"그 값으로 돌렸다"* 가 **거짓**이
        된다(99 ㊧ 계열). ⚠ **counsel처럼 `GEN_PARAMS` 상수를 두지 않았다** — probe에는
        게이트웨이가 아직 없어 **적을 실측값 자체가 없다**. 실 planner가 붙는 회차에
        그 자리를 채운다(없는 값을 미리 지어 두면 #22 부류다).

        🔴 `swallow_errors`는 실패 경로 전용이고 **필수**다. `finally` 안에서 적재가
        실패하면 원인 예외가 **교체**되고, `_run_guarded`가 그것을 `worker_internal_error`로
        떨군다 — *"그래프가 상한에 걸렸다"* 가 *"워커가 알 수 없이 죽었다"* 가 된다.
        """
        calls = self._call_log.take(context.execution_id)
        last = calls[-1].record if calls else None
        try:
            await self._runs.record_run(
                context.to_run_metadata(
                    created_at=self._now(),
                    model_provider=last.provider if last is not None else None,
                    model_name=last.model if last is not None else None,
                ),
                calls,
            )
        except Exception:  # noqa: BLE001 — 원인 예외를 덮지 않는다(위 docstring)
            if not swallow_errors:
                raise
            logger.exception("실패 경로의 실행 원장 적재 실패 — 원인 예외를 유지한다")
