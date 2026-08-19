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
from collections.abc import Callable, Mapping, MutableMapping
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from ai.composition.counsel.gate import check_counsel_gate
from ai.composition.counsel.grounding import ground_emphasis
from ai.composition.counsel.provider import (
    CounselPlanner,
    DraftWriter,
    PlanUnparsedError,
    RedactionBlockedError,
    max_chars_for,
    min_chars_for,
)
from ai.composition.counsel.state import CounselPackState
from ai.composition.counsel.stores import DraftRecord, DraftResultStore
from ai.composition.gate_feedback import instruction_for
from ai.contracts.composition import (
    DraftContext,
    DraftKind,
    DraftStatus,
    PlanOutcome,
    StudentResult,
)
from ai.contracts.execution import ExecutionContext
from ai.contracts.llm import LlmError
from ai.db.repositories.run_store import LlmCallCollector, default_llm_call_collector
from ai.runtime.draft_observation import ORIGIN_DRAFT, observe_gated_draft

logger = logging.getLogger(__name__)


class LlmCircuitOpenError(RuntimeError):
    """연속 LLM 실패가 임계에 달했다 — 워커가 paused로 수렴시킨다(§1.3 서킷)."""


_PLAN_NODE = "plan"
_STUDENT_NODE = "student"
_SUMMARIZE_NODE = "summarize"

#: 학생 루프 **밖**에서 도는 super-step 수 — 🔴 **실측값이다**(8/8 · 99 #08 ⓑ).
#:
#: 손으로 세면 `plan` + `summarize` = 2인데 **실측은 3**이다(학생 1명 → 4 · 2명 → 5 ·
#: 5명 → 8 · 20명 → 23). probe 그래프에서도 **똑같이 +1**이 나오므로 오셈이 아니라
#: `END` 전이가 super-step 하나를 먹는 라이브러리 성질이다.
#: 실측표: `docs/handoff/2026-08-08_graph_superstep_measurement.md`.
#:
#: ⚠ **여유분이 들어 있지 않다.** 노드를 늘리면 이 값도 늘려야 하고, 그걸
#: `test_derived_limit_admits_the_maximum_normal_run`이 red로 잡는다 — 여유를 얹으면
#: 그 red가 안 난다.
_GRAPH_OVERHEAD_STEPS: Final = 3


def graph_recursion_limit(*, student_count: int) -> int:
    """이 잡의 `recursion_limit` — **그래프 모양에서 유도**한다(03 §1 · 불변식 6).

    🔴 **위험의 방향은 「기본값이 낮아 죽는다」가 아니다.** langgraph의
    `DEFAULT_RECURSION_LIMIT`은 `int(getenv("LANGGRAPH_DEFAULT_RECURSION_LIMIT", "10007"))`
    이라 **사실상 무한**이다. 둘이 문제다:

      ⓐ **불변식 6의 방어가 없다** — `cursor`가 안 올라가는 버그가 나면 10007번 돈다.
      ⓑ 🔴 **저장소 밖에서 바뀐다** — 그 환경변수를 **BE 운영이 만질 수 있다.**
        호출마다 명시하면 그 변수가 우리 값을 못 덮는다.

    ⚠ **이건 루프를 막는 장치가 아니다.** 진짜 상한은 `cursor` 단조 증가와 `is_complete`가
    든다 — 여기는 그게 깨졌을 때 걸리는 **마지막 그물**이고, 기준은 *"정상 최대치보다 크고
    폭주보다 작게"* 다.

    🔴 **pg(`workflow.graph_recursion_limit`)의 식을 복사하지 않았다 — 그래프 모양이 다르다.**
    pg는 `request.count`라는 **계약 상한**에서 유도하는데, counsel은 학생 수에 계약 상한이
    없다(라우터가 `contexts={student_ref: context}`로 N=1이고 벌크는 미구현). 그래서
    **실행 시점 번들 크기**에서 유도한다. 복사했으면 counsel은 과대가 된다.

    ⚠ **게이트 재생성은 안 센다** — `for _ in range(regen_max + 1)`이 `student` 노드
    **안의 파이썬 루프**라 super-step을 안 먹는다(실측 확인).

    ⚠ **재개 시 `cursor`를 빼서 좁히지 않는다** — 같은 잡의 상한이 실행마다 달라지고
    (재개가 두 번이면 값이 셋) 불변식 8과 충돌한다. **상한은 잡의 성질이지 시도의 성질이
    아니다.** 전체 학생 수로 유도한 값은 재개 시 과대 공급이라 안전하다.
    """
    return max(student_count, 1) + _GRAPH_OVERHEAD_STEPS


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
    call_log: LlmCallCollector | None = None,
    student_call_ids: MutableMapping[str, UUID] | None = None,
) -> Any:  # noqa: ANN401 — LangGraph 컴파일 그래프 제네릭이 버전별로 달라 Any
    """의존성을 클로저로 묶어 컴파일된 그래프를 반환한다.

    `contexts`는 워커가 `context_ref`를 역참조해 넘긴다 — **state에 담기지 않는다**(§1.2 ⑨).
    `new_draft_id`·`now`는 주입한다(시계·난수 금지 03 §3). `draft_store`는 게이트 통과 본문의
    영속 경계다 — 기본 카운터(`UUID(int=n)`)는 잡 간 충돌하므로 두지 않는다.

    `call_log`·`student_call_ids`는 관측 배선이다(99 ㊻ⓒ) — 학생별로 "이 초안을 만든 LLM
    호출"의 행 id를 워커가 준 맵에 적어 준다. **state가 아니라 맵인 이유**는 §1.2 필드
    집합이 문서와 1:1로 고정돼 있어서다(대조 테스트가 강제). `consecutive` 카운터와 같은
    성격의 클로저 밖 가변 상태다.
    """

    # 🔴 `regen_max=0`은 루프를 0회 돌려 **LLM 호출 0건인데 `gate_exhausted:`(사유 빈칸)**로
    # 내보낸다 — 게이트가 막은 것처럼 보이지만 아무것도 생성하지 않았고, 그런데도
    # `llm_sent`가 아니라 quota만 흐려진다. 배선 실수를 조립 시점에 잡는다
    # (`gateway.py`가 `transport_retry`에 "0..1 밖이면 기동 실패"를 건 선례 · 불변식 6).
    if regen_max < 1:
        raise ValueError(
            f"regen_max는 1 이상이어야 한다(받은 값: {regen_max}) — 0이면 생성 시도가 "
            "0회인데 gate_exhausted로 나간다"
        )

    #: 연속 LLM 실패 카운터 — 클로저 상태(그래프 인스턴스 = 잡 1건).
    consecutive = {"llm_failed": 0}
    log = call_log or default_llm_call_collector()
    call_ids: MutableMapping[str, UUID] = (
        student_call_ids if student_call_ids is not None else {}
    )

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
        # 🔴 `PlanUnparsedError`가 **먼저**다 — `LlmError`의 하위형이라 순서가 뒤집히면
        #    형식 위반이 전부 `llm_failed`로 뭉개진다(구분하려고 만든 값이 죽는다).
        except PlanUnparsedError as exc:
            logger.info("plan 응답 형식 위반 — 무강조 진행 detail=%s", exc)
            return {"emphasis_points": {}, "plan_outcome": PlanOutcome.UNPARSED}
        # 🔴 **`RedactionBlockedError`도 `LlmError` 하위라 여기가 `LlmError`보다 먼저다** —
        #    student 노드·`refine.py`가 이미 같은 순서 규약을 쓴다. 종전에는 이 절이 없어
        #    **plan만** 마스킹 차단을 `llm_failed`로 묶었다(99 #05).
        #    ⚠ 값 문자열을 student의 `fail_reason="redaction_blocked"`와 **같게** 뒀다 —
        #      한 결함이 어느 노드에서 나느냐에 따라 다르게 기록되던 것을 맞춘 것이다.
        except RedactionBlockedError:  # fail-closed — 미전송(불변식 3)
            logger.info("plan 프롬프트 마스킹 불확실 — 미전송 · 무강조 진행")
            return {
                "emphasis_points": {},
                "plan_outcome": PlanOutcome.REDACTION_BLOCKED,
            }
        except LlmError as exc:  # plan 실패 = 무강조 진행(초안은 계속 만든다)
            logger.info("plan 실패 — 무강조 진행 reason=%s", type(exc).__name__)
            return {"emphasis_points": {}, "plan_outcome": PlanOutcome.LLM_FAILED}
        outcome = ground_emphasis(planned, contexts=contexts)
        if outcome.drops:
            logger.info("강조점 %d건 드롭 — 사유별 기록 완료", len(outcome.drops))
        # 🔴 **전량 드롭과 "고를 게 없었다"는 다른 사건이다.** 둘 다 강조점 0건이지만
        #    전자는 근거 날조·record_id 누락이고 후자는 정상이다(99 ㉲).
        all_dropped = bool(planned) and not outcome.emphasis_points
        return {
            "emphasis_points": outcome.emphasis_points,
            "plan_outcome": (
                PlanOutcome.ALL_DROPPED if all_dropped else PlanOutcome.OK
            ),
            "plan_dropped": len(outcome.drops),
        }

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
                llm_sent=False,  # 호출 자체가 없다 — 원가 0
            )

        # ②③ generate_draft → gate_check (게이트 실패 시 재생성 ≤ regen_max)
        max_chars = max_chars_for(context)
        min_chars = min_chars_for(context)  # 하한도 같은 자리에서 파생(99 #13)
        last_reason = ""
        #: 이 학생에 대해 전송이 한 번이라도 있었나 — `quota_consumed`의 판정 근거다.
        #: 🔴 **단위는 "인터랙티브 생성 1건"이지 호출 수가 아니다.** 게이트 재생성으로 3번
        #: 불러도 1이다: 호출 수는 LLM_CALL 행수로 이미 정확히 남고(변경 B), 여기서 또
        #: 세면 두 지표가 갈려 어느 쪽이 원장인지 알 수 없게 된다.
        llm_sent = False
        # 🔴 **재생성 N회 = 시도 N+1회.** 종전 `range(regen_max)`는 총 시도가 N회라
        # 재생성이 N−1회였다 — 예산을 1회 깎았고 ERD `DRAFT_BLOCK.regen_count`(le=3)의
        # 3은 도달 불가능한 값이었다. `classify/classifier.py`의
        # `range(MAX_PARSE_RETRY + 1)`과 같은 관례로 맞춘다.
        for _ in range(regen_max + 1):
            try:
                # 직전 게이트 사유를 수정 지시로 넘긴다(05 §6-2) — 같은 프롬프트를 상한까지
                # 반복하면 결정론 생성에서 같은 실패만 되풀이한다(비용 N배·개선 0).
                # 1회차는 last_reason이 비어 지시도 비므로 프롬프트가 바이트 동일하다.
                text = await writer.write(
                    context=context,
                    execution_context=execution_context,
                    emphasis=tuple(state.emphasis_points.get(student_ref, ())),
                    gate_feedback=instruction_for(last_reason),
                )
            except RedactionBlockedError:  # fail-closed — 미전송(불변식 3)
                return _record(
                    state,
                    StudentResult(
                        student_ref=student_ref,
                        status=DraftStatus.FAILED,
                        fail_reason="redaction_blocked",
                    ),
                    # 🔴 **전송 전** 차단이다 — 원가가 발생하지 않았으므로 세지 않는다.
                    # 앞 시도에서 전송이 있었다면 llm_sent가 이미 True다.
                    llm_sent=llm_sent,
                )
            except LlmError as exc:  # 재시도 없이 실패 기록 — **학생 큐 루프**는 계속
                # 🔴 **(8/19) 「루프」가 어느 루프인지 채운다** — **다음 학생**으로 계속
                #    간다는 뜻이지 **이 학생의 재생성 루프**를 계속 돈다는 뜻이 아니다.
                #    아래 `return _record(...)` 가 이 학생을 **그 자리에서 종결**한다.
                # ⚠ **N=1(v1 counsel)에서는 계속할 학생이 없어** 결과적으로 잡이 즉시
                #    끝난다 — 그래서 *「빈 응답이 재생성을 0회 한다」* 로 보인다(99 #87).
                #    🔴 **주석이 거짓이었던 게 아니라 「어느 루프인가」가 없었다.**
                #    이 단어가 없으면 다음 사람이 엉뚱한 데를 고친다.
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
                    # 전송은 시도됐다(타임아웃·벤더 오류) — 원가가 발생할 수 있으므로 센다.
                    llm_sent=True,
                )
            consecutive["llm_failed"] = 0  # 성공 전송 — 연속 카운터 초기화
            llm_sent = True
            # 🔴 방금 성공한 호출이 **이 학생의** 호출이다 — 여기서 잡아야 정확하다.
            #    루프 밖에서 읽으면 다음 학생의 호출을 가리키고, plan 노드 호출과도 섞인다.
            #    재생성했다면 매 시도 갱신되어 **최종본을 낸 호출**이 남는다.
            call_id = log.last_success_id(execution_context.execution_id)
            if call_id is not None:
                call_ids[student_ref] = call_id
            gate = check_counsel_gate(
                text, context, max_chars=max_chars, min_chars=min_chars
            )
            if gate.passed:
                #: 🔴 **관측만 한다 — 차단하지 않는다**(99 #79·#80·#28). 게이트를 통과한
                #: 본문에서 B군 어휘 적중과 출력측 마스킹 불확실을 **센다.** 여기서 나오는
                #: 숫자가 「B군을 게이트에 넣어도 되나」의 유일한 판정 재료다 —
                #: 실 LLM 산출을 레포로 안 옮기는 규율 때문에 그 표본이 달리 안 생긴다.
                observe_gated_draft(
                    text,
                    origin=ORIGIN_DRAFT,
                    tenant_id=tenant_id,
                    execution_id=str(execution_context.execution_id),
                )
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
                    llm_sent=True,
                )
            last_reason = gate.reason

        return _record(  # 재생성 소진 — 초안 전체는 실패로 정직하게 기록
            state,
            StudentResult(
                student_ref=student_ref,
                status=DraftStatus.FAILED,
                fail_reason=f"gate_exhausted:{last_reason}",
            ),
            # 산출물은 없지만 전송 3회의 원가는 실제로 발생했다 — 0으로 기록하면
            # "원가가 있었는데 흔적이 없다"가 된다(㊻ⓑ가 막으려는 바로 그 상태).
            llm_sent=True,
        )

    def _record(
        state: CounselPackState, result: StudentResult, *, llm_sent: bool
    ) -> dict[str, Any]:
        """cursor·results·quota_consumed를 함께 전진시킨다.

        불변식 ②(cursor == len(results))를 지키는 유일한 관문이라 미터링 증가도 여기 둔다 —
        경로마다 따로 올리면 어느 경로가 빠졌는지 알 수 없다(㊻ⓑ의 형태가 정확히 그것이다).

        🔴 `quota_consumed`의 단위는 **인터랙티브 생성 1건**이다(CLAUDE.md §7 — 차단·카운트·
        표시는 전부 백엔드이고 AI에 남는 건 원가 기록뿐). 전송이 한 번이라도 있었으면 1,
        없었으면 0이다.

        ⚠ **refine 턴은 이 카운터에 안 들어간다** — pack state 밖에서 도는 별도 경로다
        (라우터가 턴마다 AI_RUN을 남기고 호출은 LLM_CALL 행으로 남는다).
        """
        return {
            "cursor": state.cursor + 1,
            "results": [*state.results, result],
            "quota_consumed": state.quota_consumed + (1 if llm_sent else 0),
        }

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


__all__ = [
    "LlmCircuitOpenError",
    "build_counsel_graph",
    "graph_recursion_limit",
    "summarize",
]
