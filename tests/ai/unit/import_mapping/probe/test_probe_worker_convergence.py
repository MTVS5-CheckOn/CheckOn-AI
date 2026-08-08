"""probe 워커가 **자기 실패를 수렴시키는가** (99 #18).

🔴 **묻는 것은 「`except`가 있는가」가 아니다** — 그건 넣은 뒤엔 당연하다.
**「예외가 났을 때 잡이 종단 phase에 도달하는가」**를 묻는다.

**방치 비용**(counsel 주석이 적어 뒀고 probe에도 같다): lease 만료 → `recover_expired` →
재실행을 `max_recovery_attempts`(**기본 3** · `contracts/agents.py:18`)까지 태운다.
⇒ **같은 일을 네 번 하고** 원장에는 아무 사유도 안 남는다.

⚠ **`_WorkerKilled`(BaseException) 선례와 반대다** — PR-λ는 종단이 **와서** 재개가 안 오는
것을 겪었고, 여기는 종단이 **와야** 한다.
"""

from __future__ import annotations

import importlib
from typing import Final
from uuid import UUID

import pytest
from langgraph.errors import GraphRecursionError

from ai.contracts.agents import (
    DEFAULT_MAX_RECOVERY_ATTEMPTS,
    JobPhase,
    WorkerJob,
)
from ai.import_mapping.probe import worker as probe_worker

#: ⚠ 하네스를 **재사용**한다 — 같은 조립을 두 벌로 쓰면 따로 늙는다(99 ⑰·㉚).
#: ⚠ **`failure/`가 아니라 여기 둔다** — 하네스가 이 디렉터리에 살아 형제 모듈로 들 수 있고
#:   (`test_probe_recursion_limit.py`가 같은 형태다), `failure/`에서는 그 import가 안 된다.
#:   **하네스를 복제하는 것보다 자리를 맞추는 것이 싸다**(#02).
harness = importlib.import_module(
    "tests.ai.unit.import_mapping.probe.test_probe_worker"
    if __package__
    else "test_probe_worker"
)

_TENANT: Final = "t1"


def _boom(**_kwargs: object) -> int:
    """상한 유도를 1로 갈아 끼운다 — 그래프가 `GraphRecursionError`를 낸다.

    🔴 **워커 안에서 실제로 예외가 나게 하는 가장 짧은 길**이다. 하네스 앞단에서 막으면
    *"종단됐다"* 가 아니라 **"안 봤다"** 가 된다(절단 가드가 그것을 본다).
    """
    return 1


def _run_once(patch: pytest.MonkeyPatch) -> WorkerJob | None:
    supervisor, runner, profiles, _specs, _sink = harness._harness()  # noqa: SLF001
    ref = harness._put_profile(profiles, harness._ROSTER)  # noqa: SLF001
    patch.setattr(probe_worker, "graph_recursion_limit", _boom)

    async def scenario() -> WorkerJob | None:
        await harness._enqueue(supervisor, ref)  # noqa: SLF001
        try:
            await runner.run_next(tenant_id=_TENANT)
        except GraphRecursionError:
            #: ⚠ **전파는 이 검사의 축이 아니다** — 축은 「잡이 종단됐는가」다.
            #:   전파 여부는 `test_probe_recursion_limit.py`가 따로 고정한다.
            pass
        found: WorkerJob | None = await supervisor.get(
            tenant_id=_TENANT, job_id=UUID(int=5)
        )
        return found

    job: WorkerJob | None = harness._run(scenario())  # noqa: SLF001
    return job


def test_the_exception_actually_happens_inside_the_worker() -> None:
    """🔴 절단 가드 — 예외가 **워커 안에서** 나야 이 파일이 무언가를 본 것이다.

    상한 유도를 갈아 끼우는 것이 안 먹으면 그래프가 기본값(10007)으로 **그냥 성공**하고,
    그러면 *"종단됐다"* 가 **아무 의미가 없다**(선례: `test_probe_recursion_limit`의 대조군).
    """
    supervisor, runner, profiles, _specs, _sink = harness._harness()  # noqa: SLF001
    ref = harness._put_profile(profiles, harness._ROSTER)  # noqa: SLF001

    async def scenario() -> None:
        await harness._enqueue(supervisor, ref)  # noqa: SLF001
        await runner.run_next(tenant_id=_TENANT)

    with (
        pytest.MonkeyPatch.context() as patch,
        pytest.raises(GraphRecursionError),
    ):
        patch.setattr(probe_worker, "graph_recursion_limit", _boom)
        harness._run(scenario())  # noqa: SLF001


def test_the_job_does_not_stay_running_after_an_exception() -> None:
    """🔴 **red ① — 방치 재현.** 예외가 나면 잡이 `running`에 남는가.

    고치기 전: `running`. ⚠ 그러면 lease 만료 recovery가 **기본 3회**까지 같은 일을 다시
    하고 원장에는 사유가 없다.
    """
    with pytest.MonkeyPatch.context() as patch:
        job = _run_once(patch)
    assert job is not None
    assert job.phase is not JobPhase.RUNNING, (
        f"예외가 났는데 잡이 `running`에 남았다 — lease 만료 recovery가 "
        f"{DEFAULT_MAX_RECOVERY_ATTEMPTS}회까지 같은 일을 다시 하고 원장에는 사유가 없다"
    )


def test_the_job_converges_to_failed_with_a_reason() -> None:
    """🔴 **red ② — 수렴의 종류.** `fail`인가 `pause`인가.

    **`fail`이다.** ⚠ `pause`는 **되돌릴 수 있는 배압**에 쓴다 — counsel이 `pause`를 쓰는
    유일한 자리가 `LlmCircuitOpenError`(서킷)이고 **probe에는 서킷이 없다**(전수 0건).
    ⇒ probe의 예외는 전부 *"이 잡은 이대로는 안 된다"* 라 **종단이 정답**이다.

    그리고 **사유가 남아야 한다** — 종단만 하고 `error_code`가 비면 원장을 읽는 사람이
    *"왜 실패했나"* 를 못 묻는다.
    """
    with pytest.MonkeyPatch.context() as patch:
        job = _run_once(patch)
    assert job is not None
    assert job.phase is JobPhase.FAILED, f"종단이 `failed`가 아니다: {job.phase}"
    assert job.error_code == probe_worker.ERROR_WORKER_INTERNAL, (
        f"사유가 없거나 다르다: {job.error_code!r} — 종단만 하고 사유가 비면 "
        "원장을 읽는 사람이 「왜 실패했나」를 못 묻는다"
    )
