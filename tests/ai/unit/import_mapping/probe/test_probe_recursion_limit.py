"""probe 그래프의 `recursion_limit` — **유도되고, 실제로 그래프에 닿는다** (99 #08 ⓑ).

🔴 **잡는 결함의 형태는 「유도 함수는 있고 배선이 없다」**다. 값을 만드는 것과 그 값이
`ainvoke`에 도달하는 것은 다른 사건이고, 전자만 있으면 **테스트는 초록인데 프로덕션은
여전히 langgraph 기본값**으로 돈다.

⚠ 기본값은 `int(getenv("LANGGRAPH_DEFAULT_RECURSION_LIMIT", "10007"))`이라 **사실상
무한**이다 — 배선이 빠져도 아무것도 안 죽어서 **조용하다.**
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from ai.contracts.agents import JobPhase
from ai.import_mapping.probe.graph import graph_recursion_limit

#: 🔴 **하니스를 복제하지 않는다** — 같은 조립을 두 곳에 두면 갈린다(#02 부류).
_worker_tests = importlib.import_module(
    "tests.ai.unit.import_mapping.probe.test_probe_worker"
    if __package__
    else "test_probe_worker"
)

_MEASURED = {1: 5, 2: 6, 5: 9, 12: 16}
"""🔴 **실측 super-step**(8/8) — `docs/handoff/2026-08-08_graph_superstep_measurement.md`.

⚠ **손셈은 `loop_max + 3`이었고 실측이 `loop_max + 4`였다** — counsel 그래프에서도 같은
+1이 나온다(`END` 전이). 유도식을 손셈으로 썼으면 **상한이 물리는 모든 실행이 잘렸다.**
"""


@pytest.mark.parametrize(("loop_max", "expected"), sorted(_MEASURED.items()))
def test_derived_limit_admits_the_maximum_normal_run(
    loop_max: int, expected: int
) -> None:
    """🔴 유도값이 **상한이 물리는 정상 실행을 안 자른다** — 실측과 정확히 같다.

    ⚠ **여유분을 얹지 않았다.** 노드를 늘리면 이 단정이 red가 나고 고칠 곳은
    `_GRAPH_OVERHEAD_STEPS`다.
    """
    assert graph_recursion_limit(loop_max=loop_max) == expected


def test_the_limit_is_derived_not_a_constant() -> None:
    """입력이 늘면 상한도 는다 — 하드코딩이면 이 단정이 죽는다(03 §1)."""
    assert graph_recursion_limit(loop_max=10) - graph_recursion_limit(loop_max=5) == 5


def test_the_limit_beats_the_library_default_when_the_loop_cap_is_large() -> None:
    """🔴 **핀이 되돌아가도 우리 값이 이긴다.**

    `import_probe_loop_max`는 `ge=1`이고 상한이 없다 — 운영이 30으로 올리면 종전 기본값
    25로는 **정상 실행이 잘린다.** 지금 기본값이 10007이라 안 잘릴 뿐이고, 그 값은
    `LANGGRAPH_DEFAULT_RECURSION_LIMIT`으로 **저장소 밖에서 바뀐다.**
    """
    assert graph_recursion_limit(loop_max=30) > 25


def test_the_derivation_is_pure_and_does_not_read_settings() -> None:
    """🔴 `loop_max`를 **인자로** 받는다 — Settings를 읽으면 재현성이 깨진다(불변식 8).

    ⚠ 같은 인자에 같은 값이어야 한다. 함수가 전역·env를 보면 이 단정이 통과해도
    **다른 프로세스에서 다른 값**이 나올 수 있으므로, 인자 없이는 못 부르는 것이 요점이다.
    """
    assert graph_recursion_limit(loop_max=7) == graph_recursion_limit(loop_max=7)
    with pytest.raises(TypeError):
        graph_recursion_limit()  # type: ignore[call-arg]


def test_the_recursion_limit_actually_reaches_the_graph() -> None:
    """🔴 **뒤집기 — 배선이 빠지면 red.**

    🔴 **워커를 통째로 돌린다.** 유도 함수를 직접 부르고 그 값을 내가 그래프에 넘기면
    **동어반복**이다 — 워커가 그 값을 싣는지는 안 본다.

    유도 함수를 **1을 돌려주게** 갈아 끼우면, 배선이 있으면 `GraphRecursionError`가
    올라오고 배선이 없으면 기본값 10007로 **그냥 성공**한다.

    🔴 **counsel과 단정 형태가 다르다 — probe 워커에는 수렴 가드가 없다.**
    counsel은 `_run_guarded`가 *"running 방치 금지(3-5)"* 로 모든 예외를 잡아
    `worker_internal_error`로 떨구는데, `probe/worker.py`에는 `except` 절이 **하나도 없다**
    (전수 확인 8/8) — 예외가 호출자까지 올라가고 **잡은 `running`에 방치된다.**
    ⚠ **이 PR에서 안 고쳤다** — 축이 다르고(상한 vs 수렴) 별건으로 등재했다(99 #18).
    여기서는 **현재 동작 그대로** 단정한다 — 없는 동작을 기대하면 이 테스트가 거짓이 된다.
    """
    from langgraph.errors import GraphRecursionError

    from ai.import_mapping.probe import worker as probe_worker

    supervisor, runner, profiles, _specs, _sink = _worker_tests._harness()
    ref = _worker_tests._put_profile(profiles, _worker_tests._ROSTER)

    async def scenario() -> Any:  # noqa: ANN401
        await _worker_tests._enqueue(supervisor, ref)
        return await runner.run_next(tenant_id="t1")

    with (
        pytest.MonkeyPatch.context() as patch,
        pytest.raises(GraphRecursionError),
    ):
        patch.setattr(probe_worker, "graph_recursion_limit", lambda **_: 1)
        _worker_tests._run(scenario())


def test_the_same_job_succeeds_with_the_real_limit() -> None:
    """⚠ **대조군** — 진짜 유도값이면 같은 잡이 성공한다.

    이게 없으면 위 red가 *"상한 때문"* 인지 *"이 잡이 원래 실패"* 인지 구분되지 않는다.
    """
    supervisor, runner, profiles, _specs, _sink = _worker_tests._harness()
    ref = _worker_tests._put_profile(profiles, _worker_tests._ROSTER)

    async def scenario() -> Any:  # noqa: ANN401
        await _worker_tests._enqueue(supervisor, ref)
        return await runner.run_next(tenant_id="t1")

    done = _worker_tests._run(scenario())
    assert done is not None and done.phase is JobPhase.SUCCEEDED, done
