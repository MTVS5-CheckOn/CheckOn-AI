"""counsel 배경 드레인 — **잡을 돌리는 주체가 POST 가 아니다** (99 #85 ① · №21).

🔴 **왜 있나** — 인라인 드레인(`counsel_inline_drain_max`)은 «Kafka 가 아직 없어서» 넣은
임시물이었고 №20 실측이 그 값을 냈다: 같은 테넌트 **동시 3건이면 `succeeded` 0**, 그리고
**던져 놓고 GET 만 폴링하면 20.5초가 지나도 안 풀린다.** 후자에 한 겹이 더 있다 — BE 는
Kafka 완료 통지를 기다리는데 통지는 잡이 끝나야 나오고, 잡은 다음 POST 가 와야 돈다.
그 POST 를 보낼 주체가 지금 기다리는 BE 다 ⇒ **데드락**이다.

⇒ 이 모듈이 **별도 프로세스**로 돈다:

    uv run --frozen python -m ai.composition.counsel.drain

🔴 **왜 앱 안의 태스크(ⓐ)가 아닌가** — «앱이 죽으면 드레인도 죽는다» 보다 큰 이유가 있다:
**드레인 동시성이 곧 PG 커넥션 수**다(`store_backend=pg` 는 잡마다 `AsyncPostgresSaver`
커넥션을 새로 연다 — SQLAlchemy 풀 **밖**이다). 같은 프로세스면 드레인과 요청 처리가
**같은 커넥션 여유를 다투고**, №20 ⑤ 에서 이미 커넥션이 병목이었다(동시 300 →
`TooManyConnectionsError`). **별도 프로세스라야 그 몫을 따로 잡을 수 있다.**

━━ 🔴 이 루프가 지켜야 하는 것 넷 ━━

  ①  **테넌트 독립을 안 깬다.** `lease_next` 가 `worker_kind + tenant_id` 로만 집으므로
     드레인은 **테넌트 목록을 돈다**. 한 워커가 전 테넌트를 한 줄로 돌면 №20 ④ 가 산
     테넌트 독립(5×2 = 10/10)이 무의미해진다.
  ②  **모든 루프에 상한**(불변식 6) — 주기·테넌트당 건수·한 바퀴 테넌트 수·오류 후퇴가
     전부 `CounselSettings` 다. 🔴 여기 리터럴을 박지 마라(03 §1).
  ③  **중복 방지는 lease 하나뿐이다** — 드레인이 둘 이상 돌아도 `lease_next` 가 막는다.
     그래서 프로세스를 늘려도 안전하고, 그 사실을 검사가 지킨다.
  ④  🔴 **멈추면 알 수 있어야 한다** — `DrainHeartbeat` 가 마지막 성공 시각을 들고 있고
     `is_stale()` 이 판정한다. **이걸 빠뜨리면 8/12 체크포인터를 한 층 더 쌓는 것**이다
     (그때도 기동은 정상이었고 `/v1/ready` 는 200 이었다).

━━ 🔴 **배포에서 이 워커가 갖춰야 하는 것 넷** (99 #128 · №22 §A) ━━

⚠ **compose 파일은 저장소 밖이다** — 배포 스택은 진희님 맥에서 관리해 노션을 거쳐 윈도우
AI 서버에 적용한다(`.gitignore:50-51` 이 `docker-compose*.yml` 을 막는다). ⇒ **정의에는
검사가 못 닿는다.** 그래서 **조건은 여기 적는다** — `agents/checkpointer.py` 가 배포 단계를
자기 docstring 에 적은 선례와 같은 형태다.

  ① 🔴 **`migrate` 성공을 기다린다**(`condition: service_completed_successfully`).
     체크포인트 테이블이 없으면 드레인이 **8/12 와 같은 형태로 죽는다** — 기동은 정상이고
     잡만 전부 `worker_internal_error` 다(99 #39 ⓖ).
  ② 🔴 **`restart: unless-stopped`** — 죽으면 자동 복구. ⚠ 그래도 `DrainHeartbeat` 가
     필요하다: **재기동을 반복하며 아무 잡도 못 도는 상태**는 restart 로 안 잡힌다.
  ③ 🔴 **커넥션 몫이 앱과 갈린다**(99 #127). 드레인 동시성 × 1(체크포인터)이 이 워커의
     몫이고, 앱 몫과 합쳐 PG `max_connections` 안에 들어와야 한다.
     ⇒ `tests/ai/contract/test_timeout_budget_relations.py` 가 그 식을 든다.
  ④ **`env_file` 이 앱과 같다** — `CHECKON_ALLOW_REAL_LLM`·`OPENAI_*` 가 없으면
     기동에 실패하거나 **조용히 fake 로 돈다**(후자가 더 나쁘다 · 99 #37 선례).

⚠ **맥에서는 compose 를 안 쓴다** — 로컬은 `db` 하나만 띄우고 앱은 호스트에서 돈다.
⇒ 드레인도 호스트 프로세스다: `uv run --frozen python -m ai.composition.counsel.drain`.

⚠ **드레인은 recovery 도 겸한다.** 실측(8/20 · №21 작업 0-4): 로컬 PG 에 `running` 7 ·
`leased` 6 이 방치돼 있었다 — lease 는 만료됐는데 **회수 주체가 없었다**(`recover_expired`
는 누가 lease 를 시도할 때만 돈다). 드레인이 그 자리다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import distinct, select

from ai.agents.supervisor import Supervisor, system_utc_now
from ai.composition.counsel.settings import CounselSettings, get_counsel_settings
from ai.contracts.agents import JobPhase, WorkerKind
from ai.db.models import AgentRun
from ai.db.session import get_sessionmaker

logger = logging.getLogger(__name__)

#: 아직 안 끝난 잡의 phase — 이 phase 가 남은 테넌트만 훑는다.
#: ⚠ `paused` 는 뺀다 — 배압으로 **의도적으로** 멈춘 것이라 드레인이 되살리면 서킷이
#: 무의미해진다(`worker.py` 의 `LlmCircuitOpenError` 경로).
PENDING_PHASES: frozenset[JobPhase] = frozenset(
    {JobPhase.QUEUED, JobPhase.LEASED, JobPhase.RUNNING}
)


@dataclass
class DrainHeartbeat:
    """🔴 **드레인이 멈추면 알려 주는 자리** (§A-2 ④).

    ⚠ 이 클래스가 없으면 드레인은 **조용히** 멈춘다 — 기동은 정상이고 `/v1/ready` 도 200 이며
    잡만 안 돈다. 이 시리즈가 하루에 세 번 겪은 형태다.
    """

    now: Callable[[], datetime] = system_utc_now
    last_sweep_at: datetime | None = None
    sweeps: int = 0
    jobs_run: int = 0
    consecutive_errors: int = 0
    last_error: str | None = None
    started_at: datetime = field(default_factory=system_utc_now)

    def sweep_ok(self, ran: int) -> None:
        self.last_sweep_at = self.now()
        self.sweeps += 1
        self.jobs_run += ran
        self.consecutive_errors = 0
        self.last_error = None

    def sweep_failed(self, error: BaseException) -> None:
        self.consecutive_errors += 1
        self.last_error = type(error).__name__

    def is_stale(self, *, settings: CounselSettings | None = None) -> bool:
        """마지막 성공 이후 상한을 넘었나 — **한 번도 못 돌았으면 기동 시각 기준**이다."""
        resolved = settings or get_counsel_settings()
        reference = self.last_sweep_at or self.started_at
        elapsed = (self.now() - reference).total_seconds()
        return elapsed > resolved.counsel_drain_stale_after_seconds

    def snapshot(self) -> dict[str, object]:
        """🔴 본문·job_id·tenant_id 를 안 싣는다 — 집계만이다(불변식 3)."""
        return {
            "sweeps": self.sweeps,
            "jobs_run": self.jobs_run,
            "consecutive_errors": self.consecutive_errors,
            "last_error": self.last_error,
            "last_sweep_at": self.last_sweep_at.isoformat() if self.last_sweep_at else None,
            "stale": self.is_stale(),
        }


async def pending_tenants(limit: int) -> Sequence[str]:
    """아직 안 끝난 counsel 잡이 있는 테넌트 — **한 바퀴 상한만큼만**(불변식 6).

    ⚠ `db/models.py` 는 **읽기만** 한다(양자 승인 파일 — 스키마를 안 바꾼다).
    ⚠ 정렬은 `tenant_id` 다 — 안 정하면 순서가 실행마다 달라져 굶는 테넌트가 생긴다.
    """
    statement = (
        select(distinct(AgentRun.tenant_id))
        .where(
            AgentRun.agent_kind == WorkerKind.COUNSEL_PACK.value,
            AgentRun.status.in_([phase.value for phase in PENDING_PHASES]),
        )
        .order_by(AgentRun.tenant_id)
        .limit(limit)
    )
    async with get_sessionmaker()() as session:
        result = await session.execute(statement)
        return [row[0] for row in result.all()]


async def sweep_once(
    *,
    run_job: Callable[[str], object],
    settings: CounselSettings | None = None,
) -> int:
    """한 바퀴 — 테넌트를 돌며 각 테넌트에서 잡을 최대 `jobs_per_tenant` 개 돌린다.

    🔴 **테넌트별로 독립이다** — 한 테넌트가 상한을 다 써도 다음 테넌트가 자기 몫을 받는다.
    ⚠ 한 테넌트가 터져도 **다음 테넌트로 간다** — 남의 실패가 전체를 멈추면 안 된다
    (`except: pass` 가 아니라 로그를 남긴다 · 03 §1).
    """
    resolved = settings or get_counsel_settings()
    tenants = await pending_tenants(resolved.counsel_drain_tenants_per_sweep)
    ran_total = 0
    for tenant_id in tenants:
        for _ in range(resolved.counsel_drain_jobs_per_tenant):
            try:
                ran = await run_job(tenant_id)  # type: ignore[misc]
            except Exception:  # noqa: BLE001 — 한 테넌트의 실패가 한 바퀴를 죽이면 안 된다
                logger.warning(
                    "counsel 드레인: 테넌트 처리 실패 — 다음 테넌트로 간다", exc_info=True
                )
                break
            if ran is None:
                break  # 🔴 큐가 비었다 — 남은 회전을 낭비하지 않는다.
            ran_total += 1
    return ran_total


async def drain_forever(
    *,
    run_job: Callable[[str], object],
    heartbeat: DrainHeartbeat | None = None,
    settings: CounselSettings | None = None,
    stop: asyncio.Event | None = None,
    max_sweeps: int | None = None,
) -> DrainHeartbeat:
    """드레인 루프 — 🔴 **상한이 전부 설정이다**(불변식 6).

    ⚠ `max_sweeps` 는 **검사용**이다. 운영에서는 `stop` 이벤트로 멈춘다 —
    무한 루프 자체는 프로세스 수명과 같고, 그것이 이 워커의 존재 이유다.
    """
    resolved = settings or get_counsel_settings()
    beat = heartbeat or DrainHeartbeat()
    sweeps = 0
    #: 🔴 **밖에서 볼 수 있게 주기로 찍는다.** `stale` 은 프로세스 안의 판정이고, 이 모듈
    #: 밖에서 `DrainHeartbeat` 를 읽는 자리가 없다 — 정상 동작 중엔 기동 로그 한 줄이
    #: 전부였다(2026-08-20 실측). 그러면 「할 일이 없다」와 「멈췄다」가 안 갈린다.
    #: ⚠ 주기는 설정이다(03 §1). 시계는 `beat.now` 를 쓴다 — 검사가 구동할 수 있어야 한다.
    last_beat_log_at = beat.now()
    while not (stop is not None and stop.is_set()):
        if max_sweeps is not None and sweeps >= max_sweeps:
            break
        sweeps += 1
        try:
            ran = await sweep_once(run_job=run_job, settings=resolved)
        except Exception as error:  # noqa: BLE001 — 루프는 살아 있어야 한다
            beat.sweep_failed(error)
            logger.warning(
                "counsel 드레인: 한 바퀴 실패 연속=%d — 후퇴한다",
                beat.consecutive_errors,
                exc_info=True,
            )
            await asyncio.sleep(resolved.counsel_drain_error_backoff_seconds)
            continue
        beat.sweep_ok(ran)
        if (beat.now() - last_beat_log_at).total_seconds() >= (
            resolved.counsel_drain_heartbeat_seconds
        ):
            #: 🔴 `sweeps` 와 `jobs_run` 이 **둘 다** 실린다(`snapshot()`) — 하나만으로는
            #: 「돌고 있는데 할 일이 없다」와 「멈췄다」가 안 갈린다.
            logger.info("counsel 드레인 하트비트 %s", beat.snapshot())
            last_beat_log_at = beat.now()
        if beat.is_stale(settings=resolved):
            #: 🔴 성공했는데 stale 이면 **한 바퀴가 상한보다 오래 걸린다**는 뜻이다.
            logger.warning("counsel 드레인: 한 바퀴가 stale 상한을 넘었다 %s", beat.snapshot())
        await asyncio.sleep(resolved.counsel_drain_interval_seconds)
    return beat


async def _run_forever_with_real_stores() -> None:  # pragma: no cover — 프로세스 진입점
    """실 저장소로 드레인을 돈다 — `python -m ai.composition.counsel.drain`.

    🔴 **`api/routers/counsel.py` 를 import 하지 않는다** — composition 이 api 를 당기면
    계층이 뒤집힌다(02 §2). 저장소는 **라우터와 같은 팩토리**로 따로 만든다.
    ⚠ 그래서 라우터의 인메모리 캐시와는 공유되지 않는다 — `store_backend=pg` 에서는
    **DB 가 공유 지점**이라 문제가 없고, `memory` 에서는 애초에 별도 프로세스가 뜻이 없다.
    """
    from ai.composition.counsel.assembly import (  # noqa: PLC0415
        build_counsel_llm_provider,
        build_counsel_provider,
        open_counsel_pack_runner,
    )
    from ai.db.settings import get_db_settings  # noqa: PLC0415
    from ai.db.store_factory import (  # noqa: PLC0415
        build_context_store,
        build_counsel_agent_step_sink,
        build_draft_result_store,
        build_pack_result_store,
        build_run_store,
    )

    settings = get_counsel_settings()
    if get_db_settings().store_backend != "pg":
        raise SystemExit(
            "배경 드레인은 STORE_BACKEND=pg 에서만 뜻이 있다 — memory 는 프로세스 지역이라 "
            "다른 프로세스의 잡을 볼 수 없다"
        )
    provider = build_counsel_provider(build_counsel_llm_provider())
    supervisor = _build_supervisor()
    async with open_counsel_pack_runner(
        supervisor=supervisor,
        context_store=build_context_store(),
        step_sink=build_counsel_agent_step_sink(),
        draft_store=build_draft_result_store(),
        pack_store=build_pack_result_store(),
        planner=provider,
        writer=provider,
        regen_max=_regen_max(),
        lease_owner="counsel-drain",
        run_store=build_run_store(),
    ) as runner:
        semaphore = asyncio.Semaphore(settings.counsel_drain_concurrency)

        async def run_job(tenant_id: str) -> object:
            #: 🔴 동시성이 곧 커넥션 수다 — 세마포어가 그 상한이다(§B 의 입력).
            async with semaphore:
                return await runner.run_next(tenant_id=tenant_id)

        beat = DrainHeartbeat()
        logger.info("counsel 배경 드레인 기동 %s", beat.snapshot())
        await drain_forever(run_job=run_job, heartbeat=beat, settings=settings)


def _build_supervisor() -> Supervisor:  # pragma: no cover — 진입점 보조
    """🔴 라우터의 `_build_supervisor` 와 **같은 인자**로 만든다 — lease·aging 이 갈리면
    드레인과 라우터가 서로 다른 규칙으로 같은 큐를 만진다."""
    from datetime import timedelta  # noqa: PLC0415

    from ai.db.store_factory import build_agent_job_store  # noqa: PLC0415

    settings = get_counsel_settings()
    return Supervisor(
        store=build_agent_job_store(),
        lease_duration=timedelta(seconds=settings.counsel_lease_seconds),
        priority_aging_interval=timedelta(seconds=settings.counsel_priority_aging_seconds),
    )


def _regen_max() -> int:  # pragma: no cover — 진입점 보조
    """🔴 라우터와 **같은 상한**을 쓴다 — 여기 리터럴을 박으면 두 경로가 갈린다."""
    from ai.composition.counsel.assembly import DEFAULT_REGEN_MAX  # noqa: PLC0415

    return DEFAULT_REGEN_MAX


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run_forever_with_real_stores())
