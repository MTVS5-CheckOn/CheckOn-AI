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
from ai.composition.counsel.prompt import PROMPT_VERSION
from ai.composition.counsel.provider import (
    COUNSEL_GEN_PARAMS,
    CounselPlanner,
    DraftWriter,
)
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
from ai.db.repositories.run_store import (
    LlmCallCollector,
    RunStore,
    default_llm_call_collector,
)
from ai.db.store_factory import build_run_store

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
        run_store: RunStore | None = None,
        call_log: LlmCallCollector | None = None,
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
        #: 실행 원장(AI_RUN·LLM_CALL) — 🔴 **적재 실패의 축은 경로마다 다르다**(8/11 실측):
        #:
        #:     성공 경로  `_record_execution(..., swallow_errors=False)` → 예외가 올라가
        #:                `_run_guarded`가 잡아 잡을 `worker_internal_error`로 떨군다
        #:                ⇒ **fail-closed**
        #:     실패 경로  `swallow_errors=True` → 삼키고 로그만 남긴다 ⇒ **fail-open**
        #:
        #: ⚠ **종전 주석은 *"적재 실패는 fail-open이다(관측이지 게이트 아님)"* 였고 그건
        #: 성공 경로에서 **코드와 반대**였다**(99 ㉹). 주석은 자동 검출이 안 되므로 코드가
        #: 바뀌어도 조용하다 — 읽는 사람이 코드와 반대로 알고 있었다.
        #:
        #: **왜 갈라 두는가:** 실패 경로에서 적재 오류를 올리면 **원인 예외를 덮는다** —
        #: *"LLM이 죽었다"* 가 *"원장이 죽었다"* 로 뒤집혀 진단이 반대로 간다
        #: (`_record_execution` docstring). 성공 경로에는 덮을 원인 예외가 없으므로
        #: 원장을 못 남긴 채 성공으로 응답하지 않는다(감지 원장의 D-② 판단과 같다 —
        #: *"저장이 목적인데 실패를 삼키면 평가셋이 쌓이는 줄 알았는데 비어 있다"*).
        #:
        #: ⚠ **classify·detect도 같은 판정이다** — `classify.py`는 *"실패 경로에서만
        #: 적재 오류를 삼킨다"*, `detect.py`는 캐시(fail-open)·원장(fail-closed)·
        #: LLM_CALL(fail-open)을 갈라 적었다. **세 곳 중 이 주석만 틀렸다.**
        self._runs = run_store or build_run_store()
        #: LLM 호출 수집기 — 게이트웨이 조립부 recorder의 기본값과 **같은 인스턴스**여야
        #: 한다(공용 싱글턴). 다른 걸 꽂으면 버킷이 갈려 수집분이 영속되지 않는다.
        self._call_log = call_log or default_llm_call_collector()
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
        """잡 하나를 실행한다.

        🔴 **원장의 경계는 `_execution_context`다** (99 ㉺ · 8/11 확정):

        > **`_execution_context(job)`가 만들어지면 실행이 시작된 것이고, 그때부터
        > 무슨 일이 나든 `AI_RUN`을 남긴다. 그 앞에서 죽으면 남길 실행이 없다.**

        ⚠ **자의적인 선이 아니다 — 근거가 테스트에 이미 서 있었다.**
        `test_ledger_survives_every_failure.py::test_no_ledger_before_the_execution_starts`
        가 `bundle_missing`·`tenant_mismatch` 둘을 파라미터화해 *"실행이 없는데 실행
        기록을 만드는 것"* 을 막는다. 🔴 **그 근거가 테스트에만 있어서 워커를 읽는
        사람에게 안 보였고**, 그래서 99에 *"그 선이 자의적이지 않다는 근거가 필요하다"* 로
        등재돼 있었다. 근거는 코드 옆에 있어야 한다.

        선 앞뒤:

            앞 (원장 없음)   `ContextBundleMissingError`   묶음이 없다 — 실행할 것이 없다
                             `TenantMismatchError`         남의 묶음이다 — 실행하면 안 된다
            ────────────── `context = _execution_context(job)` ──────────────
            뒤 (원장 남김)   `ContextHashMismatchError`    🔴 **재개 경로에서만 난다** —
                             체크포인트에 `cursor`가 있다는 건 **이전 실행이 이미 돌았다**는
                             뜻이고, 이번 호출도 재개를 시도한 **실행**이다. 그래프가
                             `ainvoke`까지 안 갔어도 원장을 남기는 것이 맞다
                             (`_resume_input` 참조)
                             서킷 개방 · 미분류 실패도 여기 뒤다
        """
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
        context = _execution_context(job)
        #: student_ref → 그 학생의 초안을 만든 LLM_CALL 행 id. 그래프가 채우고 ④가 읽는다.
        #: state에 싣지 않는 이유: §1.2 필드 집합이 문서와 1:1로 고정돼 있고(대조 테스트),
        #: 재개 시엔 이미 기록된 seq를 건너뛰므로 이 맵이 비어도 정확성이 유지된다.
        student_call_ids: dict[str, UUID] = {}
        graph = build_counsel_graph(
            planner=self._planner,
            writer=self._writer,
            contexts=bundle.contexts,
            execution_context=context,
            checkpointer=self._checkpointer,
            call_log=self._call_log,
            student_call_ids=student_call_ids,
            regen_max=self._regen_max,
            llm_failure_circuit=self._circuit,
            draft_store=self._drafts,
            tenant_id=job.tenant_id,
            agent_run_id=job.job_id,
            new_draft_id=self._new_id,
            now=self._now,
        )
        config = {"configurable": {"thread_id": thread_id}}
        #: 🔴 `finally`가 **모든 수렴 경로**를 지나게 하려고 둔 플래그다(라우터 2곳과 같은
        #:  형태 — #117 → #119 → 여기가 4번째). 실패 경로에서만 적재 오류를 삼킨다.
        failed = True
        try:
            graph_input = await self._resume_input(graph, config, job, bundle)
            final = await graph.ainvoke(graph_input, config=config)
            failed = False
        finally:
            # ④′ 실행 원장 — AI_RUN + LLM_CALL. 🔴 **agent_step보다 먼저**다: agent_step의
            #    `llm_call_id`가 가리킬 LLM_CALL 행이 먼저 서야 참조가 실존한다(99 ⓒ).
            #    `finally`로 옮기면서 그 순서가 **더 강하게** 지켜진다 — agent_step(④)은
            #    성공 경로에만 있으므로 원장이 항상 먼저다.
            #    AI_RUN은 호출 0건이어도 남긴다 — 불변식 8은 "모든 실행"을 기록하며, CI 기본
            #    `FakeCounselProvider`는 게이트웨이를 타지 않아 호출이 실제로 0건이다.
            # 🔴 **성공·서킷 개방·해시 불일치·미분류 실패가 모두 여기를 지난다.** 종전에는
            #    `ainvoke` 뒤에만 있어서 그 **넷**이 원장을 건너뛰었다
            #    (8/7 실측: 서킷 개방 시 AI_RUN 0 · 수집기에 호출 3건 방치).
            #    ⚠ **(8/7 정정) 종전 문면은 「`_run_guarded`가 잡는 5종이 전부」였는데
            #      넷을 다섯으로 부풀린 것이다.** `_run_guarded`는 5절이지만 그중 둘
            #      (`bundle_missing`·`tenant_mismatch`)은 `_execution_context` **앞**에서
            #      죽어 **원장에 남을 실행이 없다** — 그게 정상이고
            #      `test_ledger_survives_every_failure.py`의
            #      `test_no_ledger_before_the_execution_starts`가 그것을 고정한다
            #      (*"실행이 없는데 실행 기록을 만드는 것"* 을 막는다).
            #      🔴 **코드를 옮기지 마라 — 옮기면 그 테스트가 red다.**
            #    ⚠ `except` 절을 추가해 때우지 않았다 — 복제가 둘이면 경로는 셋이다(#119).
            await self._record_execution(context, swallow_errors=failed)

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
                    # 🔴 종전 상수 `None` — `execution_id`에서 LLM 호출로 갈 간선이 끊겨
                    # 재현 추적(불변식 8)이 절반만 섰다(99 ㊻ⓒ). 값은 그 학생의 **마지막
                    # 성공 호출**이다: 게이트 재생성 3회 중 최종본을 낸 호출을 가리킨다
                    # (버려진 시도가 아니라 산출물을 만든 호출). 전송이 없었던 학생
                    # (컨텍스트 부재·마스킹 차단)은 None이 정답이다.
                    llm_call_id=student_call_ids.get(result.student_ref),
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

    async def _record_execution(
        self, context: ExecutionContext, *, swallow_errors: bool
    ) -> None:
        """실행 원장 1건 — 성공·서킷 개방·해시 불일치·미분류 실패 **전부**가 부른다.

        ⚠ `take(execution_id)`는 **어느 경로에서도** 불려야 한다. 안 부르면 버킷이 남아
        수집기가 LRU로 밀어낼 때까지 방치되고, `LlmCallCollector`가 그때
        `evicted_runs`를 세며 경고를 찍는다 — **그 경고를 읽는 사람이 0명이었다**(8/7
        전수 grep). 이 결함이 오래 안 보인 실질 이유다.

        🔴 `swallow_errors`는 실패 경로 전용이고 **필수**다. `finally` 안에서 적재가
        실패하면 원인 예외가 **교체된다** — `LlmCircuitOpenError`가 사라지고
        `_run_guarded`가 그걸 `WORKER_INTERNAL`로 떨군다. 즉 *"서킷이 열렸다"* 가
        *"워커가 알 수 없는 이유로 죽었다"* 로 바뀌고, **paused로 갈 잡이 failed로 간다**
        (사용자에게 보이는 결과가 달라진다).

        ⚠ 성공 경로는 삼키지 않는다 — 현행 동작 유지다. 이 파일 상단 주석은 원장 적재를
        *"fail-open(관측이지 게이트 아님)"* 이라 적어 놨지만 **실측(8/7)은 그 반대다**:
        `record_run`이 터지면 `_run_guarded`의 `except Exception`이 잡아 잡을
        `worker_internal_error`로 떨군다. 주석과 코드가 갈렸고, 어느 쪽이 정본인지는
        판정 대상이라 **이 PR에서 바꾸지 않았다**(99 등재).
        """
        calls = self._call_log.take(context.execution_id)
        last = calls[-1].record if calls else None
        try:
            await self._runs.record_run(
                context.to_run_metadata(
                    created_at=self._now(),
                    # 실측값을 옮긴다 — 조립부 설정을 여기서 재선언하면 두 값이 갈린다.
                    model_provider=last.provider if last is not None else None,
                    model_name=last.model if last is not None else None,
                    # 🔴 **사용 축이다**(99 ㊧ 계열 · 8/7 판정) — 이 실행이 **실제로 쓴**
                    #    샘플링 파라미터다. LLM을 안 부른 실행(캐시 히트·Fake·폴백)에
                    #    상수를 적어 두면 *"그 값으로 돌렸다"* 는 **거짓**이 된다.
                    #    ⚠ 같은 행의 `model_provider`·`model_name`이 이미 조건부다 —
                    #      한 행 안에서 축이 갈리면 읽는 쪽이 어느 쪽으로도 읽는다.
                    generation_params=(
                        COUNSEL_GEN_PARAMS if last is not None else None
                    ),
                ),
                calls,
            )
        except Exception:  # noqa: BLE001 — 원인 예외를 덮지 않는다(위 docstring)
            if not swallow_errors:
                raise
            logger.exception("실패 경로의 실행 원장 적재 실패 — 원인 예외를 유지한다")

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
                # plan 결과 사유를 결과 계약에 실어 보낸다 — state 밖으로 나가는 유일한
                # 경로다(잡이 끝나면 체크포인트는 재개 대상이 아니다 · 99 ㉲).
                plan_outcome=final["plan_outcome"],
                plan_dropped=final["plan_dropped"],
                # 🔴 강조점 **값**도 같은 이유로 내보낸다(99 ㉮) — 사유(`plan_outcome`)만
                # 나가고 값이 안 나가서 refine이 매 턴 강조점 없이 다시 썼다.
                emphasis_points={
                    ref: tuple(points)
                    for ref, points in final["emphasis_points"].items()
                },
            )
        )

    async def result_of(self, result_ref: str) -> CounselPackResultRecord | None:
        """`result_ref` 역참조 — 백엔드·테스트가 결과 계약을 읽는 경로."""
        return await self._packs.get(result_ref)


def _execution_context(job: WorkerJob) -> ExecutionContext:
    """워커 실행의 ExecutionContext — LLM 호출 기록(recorder)·AI_RUN이 함께 받는다.

    `prompt_version`은 **프롬프트 모듈이 소유한다**(`counsel/prompt.PROMPT_VERSION`).
    AI_RUN.prompt_version이 null이면 "어떤 프롬프트가 이 초안을 냈는가"를 되짚을 수 없어
    재현성이 반쪽이 된다(불변식 8) — 그래서 리터럴을 새로 만들지 않고 정본을 참조한다.

    ⚠ pipeline·engine·schema·contract 버전은 아직 이 함수의 리터럴이다 — 버전 소유
    (라우터 상수 vs 워커)를 정하는 것은 99 ㉗ⓒ의 남은 절반이고 여기서 섞지 않는다.
    """
    from ai.contracts.execution import Capability, VersionSet

    return ExecutionContext(
        execution_id=job.execution_id,
        tenant_id=job.tenant_id,
        capability=Capability.COMPOSITION,
        input_snapshot_hash=job.payload_hash,
        versions=VersionSet(
            pipeline_version="0.1",
            engine_version="counsel-pack-0.1",
            prompt_version=PROMPT_VERSION,
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
