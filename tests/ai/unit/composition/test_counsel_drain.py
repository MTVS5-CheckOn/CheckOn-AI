"""배경 드레인 — 상한·테넌트 독립·관측 (99 #85 ① · №21).

🔴 **이 파일이 지키는 것은 「드레인이 돈다」가 아니라 「드레인이 조용히 멈추지 않는다」다.**
№20 이 밟은 고착(#126 ③)을 드레인이 푸는데, **드레인 자신이 멈추면 같은 고착이 한 층 깊은
곳에서 다시 난다** — 8/12 체크포인터가 정확히 그 형태였다(기동 정상 · `/v1/ready` 200).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest

from ai.composition.counsel.drain import (
    PENDING_PHASES,
    DrainHeartbeat,
    drain_forever,
    sweep_once,
)
from ai.composition.counsel.settings import CounselSettings, get_counsel_settings
from ai.contracts.agents import JobPhase

_T0: Final = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clear() -> Iterator[None]:
    get_counsel_settings.cache_clear()
    yield
    get_counsel_settings.cache_clear()


class _Clock:
    def __init__(self) -> None:
        self.now = _T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def test_paused_is_not_drained() -> None:
    """🔴 `paused` 는 배압으로 **의도적으로** 멈춘 것이다.

    드레인이 되살리면 서킷이 무의미해진다.
    """
    assert JobPhase.PAUSED not in PENDING_PHASES
    assert PENDING_PHASES == {JobPhase.QUEUED, JobPhase.LEASED, JobPhase.RUNNING}


async def _body_the_sweep_respects_both_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    """🔴 **모든 루프에 상한**(불변식 6) — 테넌트 수와 테넌트당 건수 둘 다."""
    seen: list[str] = []

    #: 🔴 **천장을 둔다** — 상한이 사라지면 이 대역이 «안 끝난다» 대신 **red** 를 낸다.
    #: 실측(고의 파괴 4-①): 천장이 없으면 검사가 red 가 아니라 **hang** 이고,
    #: 멈춘 검사는 «잡았다» 가 아니다.
    _CEILING = 100

    async def never_empty(tenant_id: str) -> object:
        seen.append(tenant_id)
        assert len(seen) <= _CEILING, (
            f"드레인이 {_CEILING}회를 넘겼다 — 테넌트당/테넌트 수 상한이 사라졌다(불변식 6)"
        )
        return object()  # 항상 잡이 있었다고 답한다

    tenants = [f"t{i}" for i in range(50)]

    async def fake_tenants(limit: int) -> list[str]:
        return tenants[:limit]

    monkeypatch.setattr("ai.composition.counsel.drain.pending_tenants", fake_tenants)
    settings = CounselSettings(counsel_drain_tenants_per_sweep=3, counsel_drain_jobs_per_tenant=2)
    ran = await sweep_once(run_job=never_empty, settings=settings)
    assert ran == 6, f"3테넌트 × 2건이어야 하는데 {ran}건 돌았다"
    assert sorted(set(seen)) == ["t0", "t1", "t2"], "테넌트 상한을 넘었다"


async def _body_one_tenant_cannot_starve_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    """🔴 №20 ④ 가 산 **테넌트 독립**을 드레인이 깨지 않는다.

    ⚠ 한 테넌트가 자기 상한을 다 써도 **다음 테넌트가 자기 몫을 받는다.**
    """
    per_tenant: dict[str, int] = {}

    async def busy(tenant_id: str) -> object:
        per_tenant[tenant_id] = per_tenant.get(tenant_id, 0) + 1
        #: 🔴 천장 — 상한이 사라지면 hang 이 아니라 red 여야 한다(고의 파괴 4-①).
        assert sum(per_tenant.values()) <= 100, "테넌트당 상한이 사라졌다(불변식 6)"
        return object()

    async def fake_tenants(limit: int) -> list[str]:
        return ["greedy", "quiet"][:limit]

    monkeypatch.setattr("ai.composition.counsel.drain.pending_tenants", fake_tenants)
    await sweep_once(
        run_job=busy,
        settings=CounselSettings(
            counsel_drain_tenants_per_sweep=2, counsel_drain_jobs_per_tenant=4
        ),
    )
    assert per_tenant == {"greedy": 4, "quiet": 4}


async def _body_a_failing_tenant_does_not_kill_the_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """한 테넌트가 터져도 **다음 테넌트로 간다** — 남의 실패가 전체를 멈추면 안 된다."""
    touched: list[str] = []

    async def explode_on_first(tenant_id: str) -> object:
        touched.append(tenant_id)
        assert len(touched) <= 100, "상한이 사라졌다(불변식 6)"
        if tenant_id == "boom":
            raise RuntimeError("일부러")
        return object()

    async def fake_tenants(limit: int) -> list[str]:
        return ["boom", "ok"][:limit]

    monkeypatch.setattr("ai.composition.counsel.drain.pending_tenants", fake_tenants)
    ran = await sweep_once(
        run_job=explode_on_first,
        settings=CounselSettings(
            counsel_drain_tenants_per_sweep=2, counsel_drain_jobs_per_tenant=3
        ),
    )
    assert "ok" in touched, "앞 테넌트가 터지자 뒤 테넌트를 안 봤다"
    assert ran == 3


async def _body_the_loop_backs_off_instead_of_spinning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """한 바퀴가 통째로 실패하면 **후퇴한다** — 오류 폭주를 막는다."""
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    async def boom(_limit: int) -> list[str]:
        raise RuntimeError("스윕 실패")

    monkeypatch.setattr("ai.composition.counsel.drain.pending_tenants", boom)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def unused(_tenant: str) -> object:  # pragma: no cover — 도달 안 한다
        raise AssertionError

    beat = await drain_forever(
        run_job=unused,
        settings=CounselSettings(counsel_drain_error_backoff_seconds=7.0),
        max_sweeps=2,
    )
    assert beat.consecutive_errors == 2
    assert beat.last_error == "RuntimeError"
    assert slept == [7.0, 7.0], f"후퇴가 설정값이 아니다: {slept}"


# ── 🔴 관측 — 멈추면 알 수 있는가 (§A-2 ④) ──────────────────────────


def test_a_drain_that_never_swept_goes_stale() -> None:
    """🔴 **한 번도 못 돌아도 stale 이다** — 기동 시각이 기준이다.

    ⚠ `last_sweep_at` 만 보면 «한 번도 안 돈 드레인» 이 영원히 정상으로 보인다.
    """
    clock = _Clock()
    beat = DrainHeartbeat(now=clock, started_at=clock.now)
    settings = CounselSettings(counsel_drain_stale_after_seconds=30.0)
    assert not beat.is_stale(settings=settings)
    clock.advance(31)
    assert beat.is_stale(settings=settings), "한 번도 안 돈 드레인이 정상으로 보인다"


def test_a_stopped_drain_goes_stale_after_the_bound() -> None:
    """돌다가 멈추면 상한 뒤에 stale 이다."""
    clock = _Clock()
    beat = DrainHeartbeat(now=clock, started_at=clock.now)
    settings = CounselSettings(counsel_drain_stale_after_seconds=30.0)
    beat.sweep_ok(3)
    clock.advance(10)
    assert not beat.is_stale(settings=settings)
    clock.advance(25)
    assert beat.is_stale(settings=settings)


def test_the_snapshot_carries_no_identifiers() -> None:
    """🔴 관측이 **본문·job_id·tenant_id 를 안 싣는다** — 불변식 3."""
    clock = _Clock()
    beat = DrainHeartbeat(now=clock, started_at=clock.now)
    beat.sweep_ok(2)
    snapshot = beat.snapshot()
    assert set(snapshot) == {
        "sweeps",
        "jobs_run",
        "consecutive_errors",
        "last_error",
        "last_sweep_at",
        "stale",
    }
    assert snapshot["jobs_run"] == 2


def test_the_sweep_respects_both_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    """asyncio 플러그인 없이 도는 관례 — 본문은 `_body` 에 있다."""
    asyncio.run(_body_the_sweep_respects_both_bounds(monkeypatch))


def test_one_tenant_cannot_starve_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    """asyncio 플러그인 없이 도는 관례 — 본문은 `_body` 에 있다."""
    asyncio.run(_body_one_tenant_cannot_starve_the_others(monkeypatch))


def test_a_failing_tenant_does_not_kill_the_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    """asyncio 플러그인 없이 도는 관례 — 본문은 `_body` 에 있다."""
    asyncio.run(_body_a_failing_tenant_does_not_kill_the_sweep(monkeypatch))


def test_the_loop_backs_off_instead_of_spinning(monkeypatch: pytest.MonkeyPatch) -> None:
    """asyncio 플러그인 없이 도는 관례 — 본문은 `_body` 에 있다."""
    asyncio.run(_body_the_loop_backs_off_instead_of_spinning(monkeypatch))


# ── 🔴 밖에서 보이는가 — 주기 하트비트 (№24-W §A) ──────────────────────


async def _drain_with_clock(
    clock: _Clock, *, settings: CounselSettings, sweeps: int
) -> DrainHeartbeat:
    """스윕마다 시계를 1초씩 밀며 루프를 `sweeps` 바퀴 돌린다."""
    beat = DrainHeartbeat(now=clock, started_at=clock.now)

    async def tenants(_limit: int) -> list[str]:
        clock.advance(1)
        return []

    async def unused(_tenant: str) -> object:  # pragma: no cover — 잡이 없다
        raise AssertionError

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("ai.composition.counsel.drain.pending_tenants", tenants)
        patch.setattr(asyncio, "sleep", _noop_sleep)
        return await drain_forever(
            run_job=unused, heartbeat=beat, settings=settings, max_sweeps=sweeps
        )


async def _noop_sleep(_seconds: float) -> None:
    return None


@pytest.mark.anyio
async def test_the_heartbeat_log_carries_both_sweeps_and_jobs_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """🔴 **`jobs_run` 이 문면에 없으면 「할 일이 없다」와 「멈췄다」가 안 갈린다.**

    `sweeps` 만 찍으면 *"돌고 있다"* 는 보이지만 *"일을 하고 있나"* 가 안 보이고,
    `jobs_run` 만 찍으면 0 이 계속될 때 **루프가 도는 중인지 멈췄는지** 모른다.
    이 검사가 이 커밋의 존재 이유다(§A-5 안전선).
    """
    clock = _Clock()
    settings = CounselSettings(counsel_drain_heartbeat_seconds=3.0)
    with caplog.at_level(logging.INFO, logger="ai.composition.counsel.drain"):
        await _drain_with_clock(clock, settings=settings, sweeps=5)
    beats = [r.getMessage() for r in caplog.records if "하트비트" in r.getMessage()]
    assert beats, "주기 하트비트가 한 번도 안 찍혔다 — 밖에서 드레인을 볼 수 없다"
    assert "'sweeps'" in beats[0], f"문면에 sweeps 가 없다: {beats[0]}"
    assert "'jobs_run'" in beats[0], (
        f"문면에 jobs_run 이 없다: {beats[0]} — 「돌고 있는데 할 일이 없다」와 "
        "「멈췄다」가 구분되지 않는다"
    )


@pytest.mark.anyio
async def test_the_heartbeat_period_comes_from_settings(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """주기가 설정에서 온다 — 하드코딩이면 값을 바꿔도 로그 수가 안 갈린다(03 §1)."""
    counts = []
    for period in (2.0, 100.0):
        caplog.clear()
        clock = _Clock()
        with caplog.at_level(logging.INFO, logger="ai.composition.counsel.drain"):
            await _drain_with_clock(
                clock,
                settings=CounselSettings(counsel_drain_heartbeat_seconds=period),
                sweeps=6,
            )
        counts.append(sum(1 for r in caplog.records if "하트비트" in r.getMessage()))
    assert counts[0] > counts[1], (
        f"주기를 2초와 100초로 바꿨는데 하트비트 수가 안 갈린다({counts}) — 하드코딩이다"
    )


@pytest.mark.anyio
async def test_the_heartbeat_log_carries_no_identifiers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """🔴 관측이 본문·job_id·tenant_id 를 안 싣는다 — 불변식 3."""
    clock = _Clock()
    with caplog.at_level(logging.INFO, logger="ai.composition.counsel.drain"):
        await _drain_with_clock(
            clock,
            settings=CounselSettings(counsel_drain_heartbeat_seconds=2.0),
            sweeps=4,
        )
    beats = [r.getMessage() for r in caplog.records if "하트비트" in r.getMessage()]
    assert beats
    for forbidden in ("tenant", "job_id", "text", "student", "parent"):
        assert forbidden not in beats[0], f"하트비트 문면에 `{forbidden}` 이 실렸다: {beats[0]}"
