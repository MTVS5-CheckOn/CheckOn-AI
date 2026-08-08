"""counsel 그래프의 `recursion_limit` — **유도되고, 실제로 그래프에 닿는다** (99 #08 ⓑ).

🔴 **이 파일이 잡는 결함의 형태는 「유도 함수는 있고 배선이 없다」**다. 값을 만드는 것과
그 값이 `ainvoke`에 도달하는 것은 다른 사건이고, 전자만 있으면 **테스트는 초록인데
프로덕션은 여전히 langgraph 기본값**으로 돈다.

⚠ 기본값은 `int(getenv("LANGGRAPH_DEFAULT_RECURSION_LIMIT", "10007"))`이라 **사실상
무한**이다 — 배선이 빠져도 아무것도 안 죽어서 **조용하다.**
"""

from __future__ import annotations

import asyncio
import importlib
from typing import Any

import pytest

from ai.composition.counsel.graph import graph_recursion_limit

#: 🔴 **워커 하니스를 복제하지 않는다** — 같은 조립을 두 곳에 두면 갈린다(#02 부류).
#: `test_counsel_worker.py`가 이미 `_harness`·`CounselPackEnqueuer` 조립을 들고 있다.
_worker_tests = importlib.import_module(
    "tests.ai.unit.composition.test_counsel_worker"
    if __package__ else "test_counsel_worker"
)

_MEASURED = {1: 4, 2: 5, 5: 8, 20: 23}
"""🔴 **실측 super-step**(8/8) — `docs/handoff/2026-08-08_graph_superstep_measurement.md`.

`recursion_limit`을 1부터 올려 `GraphRecursionError`가 사라지는 최소값을 찾은 결과다.
⚠ **손셈은 `N + 2`였고 실측이 `N + 3`이었다** — probe에서도 같은 +1이 나온다(`END` 전이).
유도식을 손셈으로 썼으면 **모든 실행이 정확히 1 모자라 잘렸다.**
"""


@pytest.mark.parametrize(("students", "expected"), sorted(_MEASURED.items()))
def test_derived_limit_admits_the_maximum_normal_run(
    students: int, expected: int
) -> None:
    """🔴 유도값이 **정상 최대 실행을 안 자른다** — 실측 super-step과 정확히 같다.

    ⚠ **여유분을 얹지 않았다.** 노드를 늘리면 이 단정이 red가 나고, 그때 고칠 곳은
    `_GRAPH_OVERHEAD_STEPS`다. 여유를 얹으면 이 red가 안 난다.
    """
    assert graph_recursion_limit(student_count=students) == expected


def test_the_limit_is_derived_not_a_constant() -> None:
    """입력이 늘면 상한도 는다 — 하드코딩이면 이 단정이 죽는다(03 §1)."""
    small = graph_recursion_limit(student_count=5)
    large = graph_recursion_limit(student_count=10)
    assert large - small == 5, "학생 5명이 늘었는데 상한이 그만큼 안 늘었다"


def test_the_limit_beats_the_library_default_when_the_job_is_large() -> None:
    """🔴 **핀이 되돌아가도 우리 값이 이긴다** — 이 PR의 값이 그것이다.

    langgraph가 종전에 쓰던 기본값 25보다 큰 잡이 실재한다(22명 초과). 그때 우리가
    명시하지 않으면 **정상 실행이 잘린다** — 지금 기본값이 10007이라 안 잘릴 뿐이고,
    그 값은 `LANGGRAPH_DEFAULT_RECURSION_LIMIT` 환경변수로 **저장소 밖에서 바뀐다.**
    """
    assert graph_recursion_limit(student_count=50) > 25
    #: 경계 — 22명이 정확히 25다(§1.2 summary 예시가 "22명 중…"이라 실제 규모다).
    assert graph_recursion_limit(student_count=22) == 25


def test_zero_students_still_gets_a_workable_limit() -> None:
    """빈 번들이라도 `plan`·`summarize`는 돈다 — 0을 넘기면 그래프가 시작도 못 한다."""
    assert graph_recursion_limit(student_count=0) >= 3


def _enqueue(supervisor: Any, contexts: Any, refs: list[str]) -> Any:  # noqa: ANN401
    """잡 하나를 큐에 넣는다 — 조립은 `test_counsel_worker`의 것을 그대로 쓴다."""
    enqueuer = _worker_tests.CounselPackEnqueuer(
        supervisor=supervisor,
        context_store=contexts,
        new_id=_worker_tests._counter(),
        now=lambda: _worker_tests._NOW,
    )
    return asyncio.run(
        enqueuer.enqueue(
            tenant_id="t1",
            class_ref="cl_a1",
            contexts={r: _worker_tests._context(r) for r in refs},
        )
    )


@pytest.mark.parametrize("students", [["st_1"], ["st_1", "st_2", "st_3"]])
def test_the_recursion_limit_actually_reaches_the_graph(students: list[str]) -> None:
    """🔴 **뒤집기 — 배선이 빠지면 red.** 유도 함수만 있고 config에 안 실리면 잡는다.

    🔴 **워커를 통째로 돌린다.** 유도 함수를 직접 부르고 그 값을 내가 그래프에 넘기면
    **동어반복**이다 — *"1을 넘기면 1에서 끊긴다"* 를 확인할 뿐 `worker._execute`가 그
    값을 싣는지는 안 본다. 선례(`test_workflow.py::test_the_recursion_limit_actually_
    reaches_the_graph`)도 워크플로 전체를 돌린다.

    유도 함수를 **1을 돌려주게** 갈아 끼우면, 배선이 있으면 잡이 `worker_internal_error`로
    수렴하고(`_run_guarded`가 `GraphRecursionError`를 잡는다) 배선이 없으면 기본값
    10007로 **그냥 성공**한다.
    """
    from ai.composition.counsel import worker as counsel_worker
    from ai.contracts.agents import JobPhase

    supervisor, runner, contexts, _sink = _worker_tests._harness(students)
    job = _enqueue(supervisor, contexts, students)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(counsel_worker, "graph_recursion_limit", lambda **_: 1)
        done = asyncio.run(runner.run_next(tenant_id=job.tenant_id))

    assert done is not None
    assert done.phase is JobPhase.FAILED, (
        f"상한 1인데 잡이 {done.phase}로 끝났다 — config에 recursion_limit이 안 실렸다"
    )
    assert done.error_code == counsel_worker.ERROR_WORKER_INTERNAL, done.error_code


def test_the_same_job_succeeds_with_the_real_limit() -> None:
    """⚠ **대조군** — 진짜 유도값이면 같은 잡이 성공한다.

    이게 없으면 위 red가 *"상한 때문"* 인지 *"이 잡이 원래 실패"* 인지 구분되지 않는다.
    """
    from ai.contracts.agents import JobPhase

    refs = ["st_1", "st_2", "st_3"]
    supervisor, runner, contexts, _sink = _worker_tests._harness(refs)
    job = _enqueue(supervisor, contexts, refs)
    done = asyncio.run(runner.run_next(tenant_id=job.tenant_id))
    assert done is not None and done.phase is JobPhase.SUCCEEDED, done


