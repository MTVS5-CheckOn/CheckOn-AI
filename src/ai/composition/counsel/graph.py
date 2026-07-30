"""counsel_pack LangGraph 그래프 — `langgraph_state.md` §1.1·§1.3.

    plan → [학생 루프: assemble_context → generate_draft → gate_check → record] → summarize

**체크포인트 단위 = 학생 1명 완료**(§1.3) — 중단 지점은 학생 루프 경계에서만이다.
블록 생성 중간에는 멈추지 않는다. checkpointer는 주입한다(테스트=InMemorySaver,
prod=PostgresSaver). LLM 접점(plan·generate_draft)은 Protocol 뒤에 있고 CI 기본은 Fake다.

상한(불변식 6): 게이트 실패 재생성 ≤ `regen_max`(ERD DRAFT_BLOCK "≤3"). 학생 루프는
`student_refs` 길이로 유한하다. 재개는 `cursor`부터 — 이미 만든 draft를 재생성하지 않는다.

**학생 1명 실패가 루프를 멈추지 않는다**(불변식 ③) — 실패는 `StudentResult`에 담고 계속한다.

⚠ LangSmith 계측을 이 모듈에 넣지 않는다 — 노드 경계 훅은 `part_b/09` §2-16(B 제안 진행 중).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from ai.composition.counsel.gate import check_counsel_gate
from ai.composition.counsel.grounding import ground_emphasis
from ai.composition.counsel.provider import (
    CounselPlanner,
    DraftWriter,
    RedactionBlockedError,
    max_chars_for,
)
from ai.composition.counsel.state import CounselPackState
from ai.composition.counsel.stores import DraftRecord, DraftResultStore
from ai.contracts.composition import DraftContext, DraftKind, DraftStatus, StudentResult
from ai.contracts.execution import ExecutionContext
from ai.contracts.llm import LlmError

logger = logging.getLogger(__name__)


class LlmCircuitOpenError(RuntimeError):
    """연속 LLM 실패가 임계에 달했다 — 워커가 paused로 수렴시킨다(§1.3 서킷)."""


_PLAN_NODE = "plan"
_STUDENT_NODE = "student"
_SUMMARIZE_NODE = "summarize"


def summarize(results: list[StudentResult]) -> str:
    """§1.2 summary — "N명 중 M명 생성·K명 데이터 부족·J명 실패". 순수 함수."""
    total = len(results)
    generated = sum(1 for r in results if r.status is DraftStatus.GENERATED)
    insufficient = sum(1 for r in results if r.status is DraftStatus.REJECTED_INSUFFICIENT)
    failed = sum(1 for r in results if r.status is DraftStatus.FAILED)
    return (
        f"{total}명 중 {generated}명 생성·{insufficient}명 데이터 부족·{failed}명 실패"
    )


def build_counsel_graph(
    *,
    planner: CounselPlanner,
    writer: DraftWriter,
    contexts: Mapping[str, DraftContext],
    execution_context: ExecutionContext,
    checkpointer: BaseCheckpointSaver[Any],
    regen_max: int,
    llm_failure_circuit: int,
    draft_store: DraftResultStore,
    tenant_id: str,
    agent_run_id: UUID,
    new_draft_id: Callable[[], UUID],
    now: Callable[[], datetime],
    interrupt_before: tuple[str, ...] = (),
) -> Any:  # noqa: ANN401 — LangGraph 컴파일 그래프 제네릭이 버전별로 달라 Any
    """의존성을 클로저로 묶어 컴파일된 그래프를 반환한다.

    `contexts`는 워커가 `context_ref`를 역참조해 넘긴다 — **state에 담기지 않는다**(§1.2 ⑨).
    `new_draft_id`·`now`는 주입한다(시계·난수 금지 03 §3). `draft_store`는 게이트 통과 본문의
    영속 경계다 — 기본 카운터(`UUID(int=n)`)는 잡 간 충돌하므로 두지 않는다.
    """

    #: 연속 LLM 실패 카운터 — 클로저 상태(그래프 인스턴스 = 잡 1건).
    consecutive = {"llm_failed": 0}

    async def plan(state: CounselPackState) -> dict[str, Any]:
        """강조점을 고르고 **근거 실존을 검증**한다. plan은 부가정보다.

        plan 실패·전량 드롭이면 **강조점 없이 초안 생성을 계속**한다(잡 실패 아님).
        ⚠ 이 실패는 **서킷 카운터에 넣지 않는다** — 서킷(§1.3)은 "LLM 연속 실패 3학생"으로
        **학생 단위 write 실패**를 세는 것이고, plan은 잡당 1회라 학생 수와 무관하다.
        plan을 카운트하면 임계 1회 실패로 22명 전체가 paused가 된다.
        """
        if state.emphasis_points:  # 재개 — plan은 이미 끝났다(멱등)
            return {}
        try:
            planned = await planner.plan(
                contexts=contexts,
                student_refs=state.student_refs,
                execution_context=execution_context,
            )
        except LlmError as exc:  # plan 실패 = 무강조 진행(초안은 계속 만든다)
            logger.info("plan 실패 — 무강조 진행 reason=%s", type(exc).__name__)
            return {"emphasis_points": {}}
        outcome = ground_emphasis(planned, contexts=contexts)
        if outcome.drops:
            logger.info("강조점 %d건 드롭 — 사유별 기록 완료", len(outcome.drops))
        return {"emphasis_points": outcome.emphasis_points}

    async def student(state: CounselPackState) -> dict[str, Any]:
        """학생 1명 처리 — assemble_context → generate_draft → gate_check → record.

        이 노드 1회 방문 = 1 super-step = **체크포인트 1개**(§1.3 학생 경계).
        """
        student_ref = state.next_student()
        if student_ref is None:
            return {}

        # ① assemble_context — context_ref 역참조분에서 이 학생 몫을 꺼낸다
        context = contexts.get(student_ref)
        if context is None:  # 컨텍스트 부재 = 데이터 부족(정상 상태, error_codes §2.1)
            return _record(
                state,
                StudentResult(
                    student_ref=student_ref,
                    status=DraftStatus.REJECTED_INSUFFICIENT,
                    fail_reason="context_missing",
                ),
            )

        # ②③ generate_draft → gate_check (게이트 실패 시 재생성 ≤ regen_max)
        max_chars = max_chars_for(context)
        last_reason = ""
        for _ in range(regen_max):
            try:
                text = await writer.write(
                    context=context,
                    execution_context=execution_context,
                    emphasis=tuple(state.emphasis_points.get(student_ref, ())),
                )
            except RedactionBlockedError:  # fail-closed — 미전송(불변식 3)
                return _record(
                    state,
                    StudentResult(
                        student_ref=student_ref,
                        status=DraftStatus.FAILED,
                        fail_reason="redaction_blocked",
                    ),
                )
            except LlmError as exc:  # 재시도 없이 실패 기록 — 루프는 계속(불변식 ③)
                # §1.3 서킷 — 연속 실패가 임계에 달하면 전면 장애로 보고 협력 중단한다.
                # 22명×재생성을 다 던지는 낭비를 끊는다(불변식 6). 판정은 워커가 한다.
                consecutive["llm_failed"] += 1
                if consecutive["llm_failed"] >= llm_failure_circuit:
                    raise LlmCircuitOpenError(
                        f"연속 LLM 실패 {consecutive['llm_failed']}학생 — 서킷 개방"
                    ) from exc
                return _record(
                    state,
                    StudentResult(
                        student_ref=student_ref,
                        status=DraftStatus.FAILED,
                        fail_reason=f"llm_failed:{type(exc).__name__}",
                    ),
                )
            consecutive["llm_failed"] = 0  # 성공 전송 — 연속 카운터 초기화
            gate = check_counsel_gate(text, context, max_chars=max_chars)
            if gate.passed:
                # ④ record — **게이트 통과 직후** 본문을 영속한다. 순서가 곧 불변식 1이다
                #    (LLM 산출물은 게이트를 거쳐야 저장된다).
                #    저장을 **이 노드 안에서** 하는 이유 2개:
                #    ① state에는 본문을 싣지 않는 규율(§1.2 ⑨ — ref만)이라 노드 밖에선
                #       text가 존재하지 않는다.
                #    ② 학생 경계 체크포인트보다 먼저 영속돼야 재개 시 완료 학생의 초안이
                #       실존한다(§1.3 "재생성 없음(멱등)").
                draft_id = new_draft_id()
                await draft_store.put(
                    DraftRecord(
                        id=draft_id,
                        run_id=execution_context.execution_id,
                        agent_run_id=agent_run_id,
                        tenant_id=tenant_id,
                        kind=DraftKind.COUNSEL_PACK.value,
                        student_ref=student_ref,
                        guardian_ref=context.guardian_ref,
                        label_snapshot=context.label_snapshot.model_dump(mode="json"),
                        status=DraftStatus.GENERATED.value,
                        fail_reason=None,
                        created_at=now(),
                        content=text,
                    )
                )
                return _record(
                    state,
                    StudentResult(
                        student_ref=student_ref,
                        draft_id=draft_id,
                        status=DraftStatus.GENERATED,
                    ),
                )
            last_reason = gate.reason

        return _record(  # 재생성 소진 — 초안 전체는 실패로 정직하게 기록
            state,
            StudentResult(
                student_ref=student_ref,
                status=DraftStatus.FAILED,
                fail_reason=f"gate_exhausted:{last_reason}",
            ),
        )

    def _record(state: CounselPackState, result: StudentResult) -> dict[str, Any]:
        """cursor·results를 함께 전진시킨다 — 불변식 ②(cursor == len(results))."""
        return {"cursor": state.cursor + 1, "results": [*state.results, result]}

    def summarize_node(state: CounselPackState) -> dict[str, Any]:
        return {"summary": summarize(state.results)}

    def _route(state: CounselPackState) -> str:
        return _SUMMARIZE_NODE if state.is_complete else _STUDENT_NODE

    graph: Any = StateGraph(CounselPackState)
    graph.add_node(_PLAN_NODE, plan)
    graph.add_node(_STUDENT_NODE, student)
    graph.add_node(_SUMMARIZE_NODE, summarize_node)
    graph.add_edge(START, _PLAN_NODE)
    routes: dict[Any, str] = {
        _STUDENT_NODE: _STUDENT_NODE,
        _SUMMARIZE_NODE: _SUMMARIZE_NODE,
    }
    graph.add_conditional_edges(_PLAN_NODE, _route, routes)
    graph.add_conditional_edges(_STUDENT_NODE, _route, routes)  # 학생 경계 self-loop
    graph.add_edge(_SUMMARIZE_NODE, END)
    return graph.compile(
        checkpointer=checkpointer, interrupt_before=list(interrupt_before)
    )


__all__ = ["LlmCircuitOpenError", "build_counsel_graph", "summarize"]
