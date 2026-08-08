"""잡 원장이 **자기 규모를 말하는가** — 상한이 아니라 관측이다(99 ㊐ ⓑ).

🔴 **숫자(상한)를 정하지 않는다.** 소유는 A 단독인데 **소비자가 둘**이고(B가 pg 워커에서
같은 팩토리를 부른다) 지금은 근거가 0이라 **누구도 못 정하는 게 맞다.** 만든 것은
카운터와 경고뿐이고, **그 카운터가 나중에 숫자를 정할 근거를 만든다.**

⚠ **이 파일은 `test_the_job_ledger_is_not_capped_and_that_is_deliberate`와 짝이다** —
저쪽이 *"지우지 않는다"* 를 지키고 이쪽이 *"그래서 세기라도 한다"* 를 지킨다.
"""

from __future__ import annotations

import ast
import asyncio
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final
from uuid import UUID

from ai.agents.job_store import InMemoryJobStore
from ai.contracts.agents import (
    OperationKind,
    WorkerJob,
    WorkerKind,
    default_priority_for_operation,
)

_T0: Final = datetime(2026, 8, 8, tzinfo=UTC)
_HASH: Final = "sha256:" + "b" * 64

_SRC_ROOT: Final = Path(__file__).resolve().parents[3].parent / "src" / "ai"
_LEDGER_MODULE: Final = "job_store.py"
#: 읽는 자리가 있어야 하는 카운터 이름 — 하나라도 소비되면 「읽는 사람 0명」이 아니다.
_COUNTERS: Final = ("job_ledger_size", "job_ledger_added")


def _run[T](awaitable: Awaitable[T]) -> T:
    return asyncio.run(awaitable)  # type: ignore[arg-type]


def _job(n: int, *, tenant_id: str = "tenant-a") -> WorkerJob:
    operation = OperationKind.COUNSEL_PACK_GENERATE
    return WorkerJob(
        job_id=UUID(int=n),
        execution_id=UUID(int=9000 + n),
        tenant_id=tenant_id,
        worker_kind=WorkerKind.COUNSEL_PACK,
        operation=operation,
        payload_ref=f"command://{operation.value}/{n}",
        payload_hash=_HASH,
        priority_class=default_priority_for_operation(operation),
        queued_at=_T0,
        max_recovery_attempts=3,
    )


# ── ① 읽는 사람이 0명이면 관측이 아니다 ──────────────────────────


def _files_mentioning_counters() -> list[Path]:
    """`job_store.py` **밖에서** 카운터 이름을 쓰는 파일."""
    return sorted(
        path
        for path in _SRC_ROOT.rglob("*.py")
        if path.name != _LEDGER_MODULE
        and any(name in path.read_text(encoding="utf-8") for name in _COUNTERS)
    )


def test_the_scan_reaches_the_source_tree() -> None:
    """🔴 검사 경로가 끊기면 통과가 아니라 실패다 — 0파일이면 경로가 틀린 것이다."""
    assert len(list(_SRC_ROOT.rglob("*.py"))) > 50, (
        f"소스 트리를 못 찾았다: {_SRC_ROOT} — 이 검사는 아무것도 안 보고 있다"
    )


def test_the_job_ledger_counter_has_a_reader() -> None:
    """🔴 **관측 장치를 만들 때 읽는 자리를 같이 만든다.**

    선례가 이 저장소 안에 있다 — `LlmCallCollector.evicted_runs`는 8/5부터 카운터와
    경고를 갖고도 **읽는 사람이 0명이라 두 달을 살았고**, 그게 워커의 원장 누락(99 ㉸)이
    오래 안 보인 실질 이유였다(로그 59). **세는 것과 보는 것은 다른 일이다.**
    """
    readers = _files_mentioning_counters()
    assert readers, (
        f"잡 원장 카운터({', '.join(_COUNTERS)})를 읽는 자리가 `{_LEDGER_MODULE}` 밖에 "
        "하나도 없다 — 카운터만 있고 읽는 사람이 0명이면 관측이 아니라 죽은 코드다"
    )


# ── ② 증가가 실제로 일어나고, 줄어드는 자리는 없다 ────────────────


def test_the_ledger_counts_what_it_holds() -> None:
    store = InMemoryJobStore()
    assert len(store) == 0 and store.added == 0

    for n in range(1, 6):
        _run(store.add(_job(n)))

    assert store.added == 5, store.added
    assert len(store) == 5, len(store)


def test_the_ledger_never_shrinks_even_after_lease_and_recovery() -> None:
    """🔴 **㊐의 사실을 코드로 고정한다 — 줄어드는 자리가 0이다.**

    lease·recovery·완료를 거쳐도 행이 남는다. ⚠ 이게 *"괜찮다"* 는 뜻이 아니라
    **상한을 A가 단독으로 못 정한다**는 뜻이고, 그래서 세기부터 시작했다.
    """
    store = InMemoryJobStore()
    jobs = [_job(n) for n in range(11, 14)]
    for job in jobs:
        _run(store.add(job))

    leased = _run(
        store.lease_next(
            tenant_id="tenant-a",
            worker_kind=WorkerKind.COUNSEL_PACK,
            lease_owner="worker-1",
            acquired_at=_T0,
            expires_at=_T0.replace(minute=5),
            priority_aging_interval=timedelta(hours=1),
        )
    )
    assert leased is not None
    _run(
        store.recover_expired(
            tenant_id="tenant-a",
            worker_kind=WorkerKind.COUNSEL_PACK,
            recovered_at=_T0.replace(hour=23),
        )
    )

    assert len(store) == 3, f"잡이 사라졌다 — 원장은 줄지 않아야 한다: {len(store)}"
    assert store.added == 3, store.added


def test_the_growth_note_is_not_a_cap() -> None:
    """⚠ **경고 지점은 상한이 아니다** — 넘어도 아무것도 안 지운다.

    🔴 소스에 `popitem`·`max_items`가 없는지는 **짝 테스트**가 본다. 여기서는 *"넘겨도
    행이 그대로 있다"* 를 **동작으로** 본다 — 이름만 피하고 지우면 그쪽은 통과한다.
    """
    store = InMemoryJobStore()
    store._next_growth_log = 2  # 경고 지점을 앞으로 당긴다(상한이 아님을 보이려고)
    for n in range(21, 26):
        _run(store.add(_job(n)))

    assert len(store) == 5, f"경고 지점을 넘겼는데 행이 줄었다: {len(store)}"
    assert store.added == 5


def test_the_growth_start_is_documented_as_not_a_cap() -> None:
    """🔴 **상수 옆에 「상한이 아니다」가 적혀 있다.**

    숫자만 남으면 다음 사람이 그것을 상한으로 읽고 eviction을 붙인다 — 그게 ㊐가
    **하지 말라고 한 일**이다. 근거가 코드 옆에 없으면 근거는 사라진다.
    """
    source = (_SRC_ROOT / "agents" / _LEDGER_MODULE).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assigned = {
        target.id
        for node in tree.body
        if isinstance(node, ast.AnnAssign | ast.Assign)
        for target in ([node.target] if isinstance(node, ast.AnnAssign) else node.targets)
        if isinstance(target, ast.Name)
    }
    assert "_GROWTH_LOG_START" in assigned, assigned
    assert "상한이 아니라" in source, "경고 지점 상수 옆에 「상한이 아니다」가 없다"
