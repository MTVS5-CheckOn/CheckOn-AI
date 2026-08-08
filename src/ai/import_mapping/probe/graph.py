"""mapping_probe LangGraph ReAct 그래프 — langgraph_state §2.1.

    profile_read → probe(reason+tool+observe, 루프 ≤N) → propose_spec → confidence_check → end

체크포인트 단위 = 도구 호출 1회(§2.3) — probe 노드 1회 방문 = 1 super-step = 1 체크포인트.
checkpointer는 주입한다(테스트=InMemorySaver, prod=PostgresSaver). LLM 접점(reason)은
Fake 플래너(결정론). 루프 상한은 설정 주입(ImportSettings.import_probe_loop_max ≤5).

불변식(§2.2): ① 상한 도달 시 남은 컬럼 전부 unresolved ② 관찰에 ⟪확인필요⟫ 있으면 폐기+루프
1회 소모 ③ 최종 spec 컬럼 수 = resolved + unresolved.
"""

from __future__ import annotations

from collections.abc import Hashable
from typing import Any, Final

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from ai.import_mapping.probe.planner import ProbePlanner, ToolAction, candidate_columns
from ai.import_mapping.probe.state import (
    UNCERTAIN_TOKEN,
    ColumnMapping,
    MappingProbeState,
    MappingSpecDraft,
    ProbeStep,
    UnresolvedColumn,
)
from ai.import_mapping.probe.tools import ProbeTool

_PROBE_NODE = "probe"
_PROPOSE_NODE = "propose_spec"

#: 조사 루프 **밖**에서 도는 super-step 수 — 🔴 **실측값이다**(8/8 · 99 #08 ⓑ).
#:
#: 손으로 세면 `profile_read` + `propose_spec` + `confidence_check` = 3인데 **실측은 4**다
#: (`loop_max` 1 → 5 · 2 → 6 · 5 → 9 · 12 → 16). counsel 그래프에서도 **똑같이 +1**이
#: 나오므로 오셈이 아니라 `END` 전이가 super-step 하나를 먹는 라이브러리 성질이다.
#: 실측표: `docs/handoff/2026-08-08_graph_superstep_measurement.md`.
#:
#: ⚠ **여유분이 들어 있지 않다** — 노드를 늘리면 이 값도 늘려야 하고 그걸 테스트가 잡는다.
_GRAPH_OVERHEAD_STEPS: Final = 4


def graph_recursion_limit(*, loop_max: int) -> int:
    """이 조사 실행의 `recursion_limit` — **그래프 모양에서 유도**한다(03 §1 · 불변식 6).

    🔴 **위험의 방향은 「기본값이 낮아 죽는다」가 아니다.** langgraph의
    `DEFAULT_RECURSION_LIMIT`은 `int(getenv("LANGGRAPH_DEFAULT_RECURSION_LIMIT", "10007"))`
    이라 **사실상 무한**이다: ⓐ 불변식 6의 방어가 없고 ⓑ 🔴 **그 환경변수를 BE 운영이
    만질 수 있다** — 호출마다 명시하면 저장소 밖 값이 우리 값을 못 덮는다.

    🔴 **`loop_max`를 인자로 받는다 — Settings를 여기서 읽지 않는다.** 순수 함수여야
    재현성이 선다(불변식 8). 값의 정본은 `ImportSettings.import_probe_loop_max`(`ge=1`)이고
    워커가 `self._loop_max`로 들고 있다.

    ⚠ **counsel·pg와 유도식이 다르다** — 세 그래프의 모양이 다르다. probe는 **상한이 이미
    설정에 있어서** 계약(pg)이나 실행 시점 크기(counsel)를 볼 필요가 없다.

    ⚠ **후보가 먼저 소진되면 실제 super-step은 이보다 적다**(실측: 컬럼 5개 · `loop_max=6`
    → 9). 이 값은 **상한이 실제로 물릴 때의 최대치**다.
    """
    return max(loop_max, 1) + _GRAPH_OVERHEAD_STEPS


def _all_columns(state: MappingProbeState) -> list[str]:
    columns = state.sheets_meta.get("columns", [])
    return [str(c) for c in columns] if isinstance(columns, list) else []


def build_probe_graph(
    *,
    tools: ProbeTool,
    planner: ProbePlanner,
    loop_max: int,
    checkpointer: BaseCheckpointSaver[Any],
    interrupt_before: tuple[str, ...] = (),
) -> Any:  # noqa: ANN401 — LangGraph 컴파일 그래프(CompiledStateGraph) 제네릭이 버전별로 달라 Any
    """의존성을 클로저로 묶어 컴파일된 그래프를 반환한다. 노드는 부분 state 업데이트를 낸다.

    interrupt_before는 재개(장애 복구) 검증용 — 지정 노드 앞에서 체크포인트 남기고 중단한다.
    """

    def _route(state: MappingProbeState) -> str:
        # 후보 소진 or 상한 도달 → 제안. 그 외 → 계속 조사(불변식 ①).
        if not candidate_columns(state) or state.loop_count >= loop_max:
            return _PROPOSE_NODE
        return _PROBE_NODE

    def profile_read(state: MappingProbeState) -> dict[str, Any]:
        return {}  # sheets_meta는 기동 시 입력됨 — 검증 지점(무변경)

    def probe(state: MappingProbeState) -> dict[str, Any]:
        action = planner.next_action(state)
        if not isinstance(action, ToolAction):  # 방어 — _route가 후보 있을 때만 진입
            return {}
        observation = _call_tool(tools, action.tool, action.tool_args)
        step = ProbeStep(
            seq=len(state.steps),
            thought=action.thought,
            tool=action.tool,
            tool_args=action.tool_args,
            observation_masked=observation,
        )
        resolved = dict(state.resolved_columns)
        unresolved = list(state.unresolved_columns)
        if UNCERTAIN_TOKEN not in observation:  # 불변식 ② — 마스킹 실패 관찰은 폐기(판정 안 함)
            verdict = planner.classify(action.column, step)
            if isinstance(verdict, ColumnMapping):
                resolved[action.column] = verdict
            else:
                unresolved.append(verdict)
        return {
            "steps": [*state.steps, step],
            "loop_count": state.loop_count + 1,
            "resolved_columns": resolved,
            "unresolved_columns": unresolved,
        }

    def propose_spec(state: MappingProbeState) -> dict[str, Any]:
        # 상한 도달로 미조사된 컬럼은 전부 unresolved(억지 매핑 금지, 불변식 ①).
        settled = set(state.resolved_columns) | {u.source for u in state.unresolved_columns}
        leftover = [
            UnresolvedColumn(source=name, reason="조사 상한 도달 — 미해결(강사 확인 필요)")
            for name in _all_columns(state)
            if name not in settled
        ]
        return {"unresolved_columns": [*state.unresolved_columns, *leftover]}

    def confidence_check(state: MappingProbeState) -> dict[str, Any]:
        resolved = tuple(state.resolved_columns.values())
        overall = sum(c.confidence for c in resolved) / len(resolved) if resolved else 0.0
        draft = MappingSpecDraft(
            resolved=resolved,
            unresolved=tuple(state.unresolved_columns),
            overall_confidence=overall,
        )
        # 불변식 ③ — 최종 컬럼 수 = resolved + unresolved = 전체 입력 컬럼.
        if len(draft.resolved) + len(draft.unresolved) != len(_all_columns(state)):
            raise ValueError("spec 컬럼 수가 입력 컬럼 수와 다르다(누락)")
        return {"spec_draft": draft, "overall_confidence": overall}

    builder = StateGraph(MappingProbeState)
    builder.add_node("profile_read", profile_read)
    builder.add_node(_PROBE_NODE, probe)
    builder.add_node(_PROPOSE_NODE, propose_spec)
    builder.add_node("confidence_check", confidence_check)
    routes: dict[Hashable, str] = {_PROBE_NODE: _PROBE_NODE, _PROPOSE_NODE: _PROPOSE_NODE}
    builder.add_edge(START, "profile_read")
    builder.add_conditional_edges("profile_read", _route, routes)
    builder.add_conditional_edges(_PROBE_NODE, _route, routes)
    builder.add_edge(_PROPOSE_NODE, "confidence_check")
    builder.add_edge("confidence_check", END)
    return builder.compile(
        checkpointer=checkpointer, interrupt_before=list(interrupt_before)
    )


def _call_tool(tools: ProbeTool, name: str, args: dict[str, str]) -> str:
    if name == "get_unique_values":
        return tools.get_unique_values(args["column"])
    if name == "get_more_sample":
        return tools.get_more_sample(args["sheet"], int(args.get("rows", "20")))
    if name == "check_join_key":
        return tools.check_join_key(args["sheet_a"], args["sheet_b"])
    raise ValueError(f"알 수 없는 도구: {name}")
