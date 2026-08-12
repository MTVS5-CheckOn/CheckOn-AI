"""**같은 자연키에 최초 저장이 겹치면** 어떻게 되는가 (99 #41).

🔴 **없는 행은 `SELECT`로 잠글 수 없다.** `insert_prediction()`은 「읽고-판정하고-쓴다」인데
최초 저장에는 **잠글 대상이 없다** ⇒ 두 트랜잭션이 나란히 *"행이 없다"* 를 보고 **둘 다
INSERT**한다. `uq_inquiry_class_scope`가 하나를 죽이고, 그 `UniqueViolationError`가
**저장소 계약 밖으로 샌다.**

⚠ **현재 서비스에서 이미 경합이 났다고 주장하지 않는다** — classify 경로는 재사용 캐시가
창을 줄인다. 다만 **크래시 후 재진입 · 독립 요청 · 다중 프로세스 소비자**에서도 저장소는
무결성을 지켜야 한다. 저장소의 계약은 배포 형상에 기대지 않는다.

🔴 **이 파일의 본체는 「행이 몇 개 남았는가」가 아니다.** 종전 코드도 최종 행은 하나일 수
있다(제약이 두 번째를 죽이므로). 재는 것은 **호출 결과에 DB 드라이버 오류가 새는가**다.

**결정론적으로 겹치게 한다** — `asyncio.gather()`만으로는 네 트랜잭션이 **같은 순간에 문
앞에** 선다는 보장이 없다. 공용 `first_sql_barrier`가 각 writer를 **첫 SQL 앞**에 세운다.
⚠ **프로덕션 코드에 시험용 갈고리를 안 넣는다** — 세션메이커를 감쌌다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from decimal import Decimal
from typing import Any, Final

import pytest
from first_sql_barrier import barrier_sessionmaker
from pg_hint import PG_UNAVAILABLE
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ai.db.models import InquiryClass as InquiryClassRow
from ai.db.repositories.inquiry_class_store import (
    InquiryClassRecord,
    PgInquiryClassStore,
)
from ai.db.settings import get_db_settings

pytestmark = pytest.mark.integration

_TENANT: Final = "t_inquiry_race"
_OTHER_TENANT: Final = "t_inquiry_race_other"
_REF: Final = "iq_race_1"

#: 🔴 각 방향을 여러 번 돈다 — 경합은 한 번에 안 드러날 수 있고, **한 번이라도** 오류가
#: 나면 그건 결함이다(간헐이 아니라 **가능**이 판정 기준이다).
_ROUNDS: Final = 3
#: 동시에 세우는 writer 수 — 지시서가 정한 값이다(둘보다 넷이 창을 넓게 연다).
_WRITERS: Final = 4

#: hang 방지 상한. ⚠ **동시성 판정 기준이 아니다** — 판정은 예외 유무와 행 내용이 한다.
#: 근거: 이 파일의 한 라운드 실측 최장이 **0.1초 미만**(네 writer · 로컬 PG)이고,
#: 자문 잠금은 앞 트랜잭션 커밋까지만 기다린다. 실측의 **100배 이상**으로 잡아
#: 느린 기기에서도 오탐이 안 나게 하되, **잠금이 안 풀리는 결함은 유한 시간에 잡힌다.**
_TIMEOUT_S: Final = 20.0


def _record(topic: str, sentiment: str, urgency: str, confidence: str) -> InquiryClassRecord:
    """예측 3축·confidence 3축이 **서로 구분되는** 레코드 — 혼합 행을 잡으려면 필요하다."""
    value = Decimal(confidence)
    return InquiryClassRecord(
        topic=topic,
        sentiment=sentiment,
        urgency=urgency,
        confidence_topic=value,
        confidence_sentiment=value,
        confidence_urgency=value,
    )


#: 🔴 **여섯 축이 전부 다르다** — 필드별로 섞인 행이 나오면 어느 입력과도 안 맞는다.
_DISTINCT: Final[tuple[InquiryClassRecord, ...]] = (
    _record("grade", "anxious", "immediate", "0.91"),
    _record("schedule", "neutral", "normal", "0.72"),
    _record("attendance", "angry", "urgent", "0.63"),
    _record("payment", "positive", "low", "0.54"),
)
_SAME: Final = _record("grade", "anxious", "immediate", "0.91")


def _dsn() -> str:
    return get_db_settings().database_url


async def _clean(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as session, session.begin():
        await session.execute(
            text("DELETE FROM inquiry_class WHERE tenant_id = ANY(:t)"),
            {"t": [_TENANT, _OTHER_TENANT]},
        )


async def _rows_of(
    sessions: async_sessionmaker[AsyncSession], tenant: str
) -> list[InquiryClassRow]:
    async with sessions() as session:
        return list(
            (
                await session.execute(
                    select(InquiryClassRow).where(InquiryClassRow.tenant_id == tenant)
                )
            )
            .scalars()
            .all()
        )


def _as_tuple(row: InquiryClassRow | InquiryClassRecord) -> tuple[Any, ...]:
    """예측 3축 + confidence 3축 + `llm_call_id` — **전문 대조**용 튜플."""
    return (
        row.topic,
        row.sentiment,
        row.urgency,
        Decimal(row.confidence_topic),
        Decimal(row.confidence_sentiment),
        Decimal(row.confidence_urgency),
        row.llm_call_id,
    )


type _Outcome = tuple[list[BaseException], list[InquiryClassRow]]


async def _race(records: tuple[InquiryClassRecord, ...], *, tenant: str) -> _Outcome:
    """같은 자연키에 `records`를 **동시에 최초 저장**한다 — 예외와 최종 행을 돌려준다.

    🔴 **예외를 삼키지 않고 모아서 돌려준다** — 이 파일이 재는 것이 그것이다.
    """
    engine = create_async_engine(_dsn(), poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _clean(sessions)
        barrier = asyncio.Barrier(len(records))
        stores = [
            PgInquiryClassStore(sessionmaker=barrier_sessionmaker(sessions, barrier))
            for _ in records
        ]
        async with asyncio.timeout(_TIMEOUT_S):
            results = await asyncio.gather(
                *(
                    store.insert_prediction(
                        tenant_id=tenant, inquiry_ref=_REF, record=record
                    )
                    for store, record in zip(stores, records, strict=True)
                ),
                return_exceptions=True,
            )
        failures = [r for r in results if isinstance(r, BaseException)]
        return failures, await _rows_of(sessions, tenant)
    finally:
        await engine.dispose()


def _run(coro: Callable[[], Coroutine[Any, Any, _Outcome]]) -> _Outcome:
    try:
        return asyncio.run(coro())
    except Exception as exc:  # noqa: BLE001 — 접속 실패만 skip으로 가른다
        if "connect" in str(exc).lower() or "refused" in str(exc).lower():
            pytest.skip(PG_UNAVAILABLE)
        raise


def _describe(failures: list[BaseException]) -> str:
    """오류 **타입과 제약명**을 남긴다 — *"뭔가 터졌다"* 로 적지 않는다."""
    return " · ".join(f"{type(e).__name__}: {str(e)[:200]}" for e in failures)


# ───────────────────────── 1-1 · 서로 다른 예측 넷 ─────────────────────────


def test_four_different_first_writes_do_not_leak_a_driver_error() -> None:
    """🔴 **저장소 밖으로 DB 드라이버 오류가 새면 안 된다.**

    ⚠ **행 수만 보면 종전 코드도 통과한다** — 유니크 제약이 패자를 죽이므로 최종 행은
    하나다. 재는 것은 **호출 결과**다.
    """
    for round_index in range(_ROUNDS):
        failures, rows = _run(lambda: _race(_DISTINCT, tenant=_TENANT))
        assert not failures, (
            f"{round_index}회차에 저장소 밖으로 오류가 샜다 — {_describe(failures)}"
        )
        assert len(rows) == 1, f"{round_index}회차 행 수가 {len(rows)}다"


def test_the_surviving_row_is_exactly_one_of_the_inputs() -> None:
    """🔴 **필드별로 섞인 행이 나오면 안 된다** — 어느 입력의 **전문**과 같아야 한다.

    ⚠ **승자는 고정하지 않는다** — 미검토 행의 순차 갱신 규약을 그대로 따르고,
    동시 승자 정책을 여기서 새로 발명하지 않는다.
    """
    for round_index in range(_ROUNDS):
        failures, rows = _run(lambda: _race(_DISTINCT, tenant=_TENANT))
        assert not failures, _describe(failures)
        assert len(rows) == 1
        survivor = _as_tuple(rows[0])
        candidates = [_as_tuple(record) for record in _DISTINCT]
        assert survivor in candidates, (
            f"{round_index}회차 최종 행이 어느 입력과도 안 맞는다(혼합 행): {survivor}"
        )
        assert rows[0].reviewed_at is None, "최초 저장인데 검토 시각이 찍혔다"


# ───────────────────────── 1-2 · 같은 예측 넷 ─────────────────────────


def test_four_identical_first_writes_all_succeed() -> None:
    """🔴 **같은 값 재시도를 충돌로 취급하지 않는다** — 넷 다 성공하고 행은 하나다."""
    same = tuple(_SAME for _ in range(_WRITERS))
    for round_index in range(_ROUNDS):
        failures, rows = _run(lambda: _race(same, tenant=_TENANT))
        assert not failures, (
            f"{round_index}회차: 같은 값 재시도가 실패했다 — {_describe(failures)}"
        )
        assert len(rows) == 1, f"{round_index}회차 행 수가 {len(rows)}다"
        assert _as_tuple(rows[0]) == _as_tuple(_SAME), "저장 전문이 입력과 다르다"


# ───────────────────────── 1-3 · 테넌트 분리 ─────────────────────────


async def _race_across_tenants() -> tuple[list[BaseException], list[Any]]:
    """같은 `inquiry_ref`를 **두 테넌트가 동시에** 최초 저장한다 — 서로 다른 자연키다."""
    engine = create_async_engine(_dsn(), poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _clean(sessions)
        barrier = asyncio.Barrier(2)
        pairs = ((_TENANT, _DISTINCT[0]), (_OTHER_TENANT, _DISTINCT[1]))
        async with asyncio.timeout(_TIMEOUT_S):
            results = await asyncio.gather(
                *(
                    PgInquiryClassStore(
                        sessionmaker=barrier_sessionmaker(sessions, barrier)
                    ).insert_prediction(
                        tenant_id=tenant, inquiry_ref=_REF, record=record
                    )
                    for tenant, record in pairs
                ),
                return_exceptions=True,
            )
        failures = [r for r in results if isinstance(r, BaseException)]
        both = [
            await _rows_of(sessions, _TENANT),
            await _rows_of(sessions, _OTHER_TENANT),
        ]
        return failures, both
    finally:
        await engine.dispose()


def test_two_tenants_with_the_same_ref_are_independent() -> None:
    """🔴 **같은 `inquiry_ref`라도 테넌트가 다르면 다른 자연키다** — 둘 다 저장된다."""
    try:
        failures, (mine, theirs) = asyncio.run(_race_across_tenants())
    except Exception as exc:  # noqa: BLE001
        if "connect" in str(exc).lower() or "refused" in str(exc).lower():
            pytest.skip(PG_UNAVAILABLE)
        raise
    assert not failures, _describe(failures)
    assert len(mine) == 1 and len(theirs) == 1, "테넌트별로 한 행씩 서지 않았다"
    assert _as_tuple(mine[0]) == _as_tuple(_DISTINCT[0])
    assert _as_tuple(theirs[0]) == _as_tuple(_DISTINCT[1]), (
        "다른 테넌트의 행이 내 값으로 덮였다 — 잠금 키나 조회 술어에 테넌트가 빠졌다"
    )


def test_the_lock_key_separates_tenants_and_refs() -> None:
    """🔴 **잠금 키가 자연키 전체를 담는가** — 안 그러면 무관한 문의가 같은 줄에 선다.

    ⚠ 잠금은 **정확성**이 아니라 **동시성 폭**의 문제다 — 키가 좁으면 무관한 요청까지
    직렬화되고, 그건 조용히 느려지는 형태라 나중에 원인을 못 찾는다.
    ⚠ **실행 없이 키 함수만** 본다 — 잠금이 실제로 서는지는 위의 실 PG 검사가 든다.
    """
    from ai.db.repositories.inquiry_class_store import lock_key  # noqa: PLC0415

    assert lock_key("t1", "iq_1") != lock_key("t2", "iq_1"), (
        "테넌트가 다른데 같은 잠금 축이다"
    )
    assert lock_key("t1", "iq_1") != lock_key("t1", "iq_2"), (
        "문의가 다른데 같은 잠금 축이다"
    )
    #: 🔴 **다른 테이블과도 섞이지 않는다** — 네임스페이스가 앞에 붙는다.
    assert lock_key("t1", "iq_1").startswith("inquiry_class\x1f")
    #: ⚠ 구분자는 `\x00`이 아니다 — PG가 문자열의 NUL 바이트를 거부한다(counsel 실측).
    assert "\x00" not in lock_key("t1", "iq_1")


# ───────────────────────── 절단 가드 ─────────────────────────


def test_the_barrier_actually_parks_every_writer() -> None:
    """🔴 **네 writer가 정말 첫 SQL 앞에 함께 서는가** — 안 서면 직렬 저장을 네 번 잰 것이다.

    ⚠ 공용 프록시 **자체**의 절단 가드는 `test_counsel_read_model_concurrency`가 든다
    (#198·#200). 여기서는 **이 파일이 그것을 실제로 주입했는지**만 본다.
    """

    async def scenario() -> None:
        engine = create_async_engine(_dsn(), poolclass=NullPool)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await _clean(sessions)
            barrier = asyncio.Barrier(_WRITERS)

            def writer(record: InquiryClassRecord) -> asyncio.Task[None]:
                store = PgInquiryClassStore(
                    sessionmaker=barrier_sessionmaker(sessions, barrier)
                )
                return asyncio.ensure_future(
                    store.insert_prediction(
                        tenant_id=_TENANT, inquiry_ref=_REF, record=record
                    )
                )

            #: 🔴 **셋만 먼저 띄운다.** 넷을 한 번에 띄우면 넷째가 도착하는 순간 전원이
            #:   풀려 `n_waiting`이 **0으로 돌아간 뒤**에 관측된다(실측: 0) — 그러면
            #:   *"아무도 안 섰다"* 와 구분이 안 된다. 창을 **손으로 연다.**
            parked = [writer(record) for record in _DISTINCT[:-1]]
            async with asyncio.timeout(_TIMEOUT_S):
                while barrier.n_waiting < _WRITERS - 1:
                    if any(task.done() for task in parked):
                        break
                    await asyncio.sleep(0)

            assert barrier.n_waiting == _WRITERS - 1, (
                f"배리어 앞에 {barrier.n_waiting}명만 섰다 — 프록시가 안 물렸다"
            )
            assert not any(task.done() for task in parked), (
                "party가 다 오기 전에 끝난 writer가 있다 — 첫 SQL이 안 걸렸다"
            )
            #: 🔴 **아직 아무 행도 없다** — 잠금이 아니라 **배리어**가 잡고 있는 상태다.
            assert await _rows_of(sessions, _TENANT) == [], (
                "party가 다 오기 전에 행이 생겼다"
            )

            last = writer(_DISTINCT[-1])
            async with asyncio.timeout(_TIMEOUT_S):
                await asyncio.gather(*parked, last)
            assert len(await _rows_of(sessions, _TENANT)) == 1
        finally:
            await engine.dispose()

    try:
        asyncio.run(scenario())
    except Exception as exc:  # noqa: BLE001
        if "connect" in str(exc).lower() or "refused" in str(exc).lower():
            pytest.skip(PG_UNAVAILABLE)
        raise


def test_a_row_of_another_tenant_is_not_updated_in_place() -> None:
    """🔴 **조회 술어에서 테넌트가 빠지면 남의 행을 덮는다** — 순차 경로다.

    ⚠ **동시 검사만으로는 이 축이 안 보인다**(실측: 술어를 지워도 **6 passed**): 두 테넌트가
    **동시에** 최초 저장하면 둘 다 *"행이 없다"* 를 보고 각자 INSERT하므로 통과한다.
    결함은 **한쪽이 이미 있을 때** 드러난다 — 그때 SELECT가 남의 행을 물어와 **UPDATE**한다.
    """

    async def scenario() -> tuple[list[InquiryClassRow], list[InquiryClassRow]]:
        engine = create_async_engine(_dsn(), poolclass=NullPool)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await _clean(sessions)
            store = PgInquiryClassStore(sessionmaker=sessions)
            #: ① 남의 테넌트가 먼저 자리를 잡는다.
            await store.insert_prediction(
                tenant_id=_OTHER_TENANT, inquiry_ref=_REF, record=_DISTINCT[1]
            )
            #: ② 내 테넌트가 **같은 `inquiry_ref`** 로 최초 저장한다.
            await store.insert_prediction(
                tenant_id=_TENANT, inquiry_ref=_REF, record=_DISTINCT[0]
            )
            return (
                await _rows_of(sessions, _TENANT),
                await _rows_of(sessions, _OTHER_TENANT),
            )
        finally:
            await engine.dispose()

    try:
        mine, theirs = asyncio.run(scenario())
    except Exception as exc:  # noqa: BLE001
        if "connect" in str(exc).lower() or "refused" in str(exc).lower():
            pytest.skip(PG_UNAVAILABLE)
        raise

    assert len(mine) == 1, f"내 테넌트 행이 {len(mine)}개다 — 남의 행을 갱신했을 수 있다"
    assert len(theirs) == 1, "남의 테넌트 행이 사라졌다"
    assert _as_tuple(mine[0]) == _as_tuple(_DISTINCT[0])
    assert _as_tuple(theirs[0]) == _as_tuple(_DISTINCT[1]), (
        "남의 행이 내 값으로 덮였다 — 조회 술어에 테넌트가 빠졌다"
    )


# ── (지시서 69-R) 예측 갱신과 확정 회신이 겹칠 때 (99 #42) ──

#: 초기 행 — `topic=schedule` · 정정 없음 · 미검토.
_SEED: Final = _record("schedule", "neutral", "normal", "0.50")
#: 늦게 도착하는 새 예측 — **topic과 confidence가 둘 다 다르다**(전문 대조를 위해).
_LATE: Final = _record("payment", "neutral", "normal", "0.80")


def _full_state(row: InquiryClassRow) -> tuple[Any, ...]:
    """예측 3축 · confidence 3축 · **정정 3축** · 검토 여부 — 조합 전체를 본다.

    🔴 **`reviewed_at is not None`만 보면 안 된다** — 그 단정은 **두 순차 실행 어디에도
    없는 혼합 상태**를 통과시킨다(예: `topic=payment` + `corrected_topic=None` + 검토됨).
    """
    return (
        row.topic,
        row.sentiment,
        row.urgency,
        Decimal(row.confidence_topic),
        Decimal(row.confidence_sentiment),
        Decimal(row.confidence_urgency),
        row.corrected_topic,
        row.corrected_sentiment,
        row.corrected_urgency,
        row.reviewed_at is not None,
    )


#: 🔴 **가능한 정상 결과는 순차 실행 둘뿐이다.**
#: ⚠ 타입을 명시한다 — 안 하면 mypy가 두 리터럴을 **서로 다른 구체 타입**으로 좁혀
#:   `_PREDICTION_FIRST != _CONFIRMATION_FIRST` 를 *"겹치지 않는 비교"* 로 막는다
#:   (그 단정은 **두 갈래가 같으면 검사가 헛돈다**를 지키는 자리라 지울 수 없다).
type _Corrected = str | None
type _State = tuple[
    str, str, str, Decimal, Decimal, Decimal, _Corrected, _Corrected, _Corrected, bool
]
#:
#:   ⓐ 예측이 먼저 — `payment` 저장 → 확정이 `schedule`을 보고 **예측과 다르므로 정정**
#:   ⓑ 확정이 먼저 — 기존 `schedule`을 검토, **같은 값이라 정정 아님**(규약 ②) →
#:      늦은 예측은 **검토된 행을 보고 skip**(규약 ①)
_PREDICTION_FIRST: Final[_State] = (
    "payment", "neutral", "normal",
    Decimal("0.80"), Decimal("0.80"), Decimal("0.80"),
    "schedule", None, None,
    True,
)
_CONFIRMATION_FIRST: Final[_State] = (
    "schedule", "neutral", "normal",
    Decimal("0.50"), Decimal("0.50"), Decimal("0.50"),
    None, None, None,
    True,
)


async def _race_prediction_against_confirmation() -> tuple[list[BaseException], tuple[Any, ...]]:
    engine = create_async_engine(_dsn(), poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _clean(sessions)
        #: 초기 행은 **경합 밖에서** 만든다 — 재는 것은 「기존 행 갱신 경합」이다.
        await PgInquiryClassStore(sessionmaker=sessions).insert_prediction(
            tenant_id=_TENANT, inquiry_ref=_REF, record=_SEED
        )
        barrier = asyncio.Barrier(2)

        def store() -> PgInquiryClassStore:
            return PgInquiryClassStore(
                sessionmaker=barrier_sessionmaker(sessions, barrier)
            )

        async with asyncio.timeout(_TIMEOUT_S):
            results = await asyncio.gather(
                store().insert_prediction(
                    tenant_id=_TENANT, inquiry_ref=_REF, record=_LATE
                ),
                store().apply_confirmation(
                    tenant_id=_TENANT, inquiry_ref=_REF, corrections={"topic": "schedule"}
                ),
                return_exceptions=True,
            )
        failures = [r for r in results if isinstance(r, BaseException)]
        rows = await _rows_of(sessions, _TENANT)
        assert len(rows) == 1, f"행이 {len(rows)}개다"
        return failures, _full_state(rows[0])
    finally:
        await engine.dispose()


def test_a_prediction_and_a_confirmation_converge_to_one_sequential_result() -> None:
    """🔴 **최종 상태가 가능한 순차 실행 둘 중 하나와 정확히 일치해야 한다** (99 #42).

    ⚠ **「누가 먼저 잠금을 얻는가」는 고정하지 않는다** — 둘 다 정상이다. 고정하는 것은
    *"두 순차 실행 어디에도 없는 상태는 나오면 안 된다"* 이다.

    🔴 **증거가 둘이고 성질이 다르다 — 이 검사가 잡는 것은 둘째뿐이다.**

    **ⓐ 시간 순서 증거(수동 인터리빙 · 이 검사가 못 잡는다).** 규약 ① 위반을 처음 안 것은
    **순서를 직접 통제했기 때문**이다: 예측이 `reviewed_at=None`을 읽고 → 확정이 커밋하고
    → 예측이 낡은 판정으로 덮었다. ⚠ **그때의 최종 스냅숏 `payment` + `corrected=schedule`
    + 검토됨은 「예측 먼저」 정상 결과와 값이 완전히 같다** — **최종 값만으로는 그 사례를
    구분할 수 없다.** 값이 같으면 경로는 갈리지 않는다.

    **ⓑ 상태 증거(자동 뒤집기 · 이 검사가 잡는다).** 잠금을 빼고 돌리면 나오는 상태는
    `payment` + **`corrected` 전부 `None`** + 검토됨이다 — 확정이 커밋됐는데 늦은 예측이
    **정정까지 지운** 조합이라 **순차 실행 두 갈래 어디에도 없다.** 그래서 **값으로 검출된다.**

    ⇒ 아래가 확인하는 것은 *"비직렬 최종 상태가 정상 결과 집합에 들어오지 않는가"* 다.
    ⚠ **이 검사만으로 규약 ①이 전부 지켜진다고 말하지 않는다** — ⓐ 부류(값이 겹치는
    시간 순서 위반)는 **상태 대조의 사각**이고, 그걸 막는 것은 **잠금 자체**다.
    """
    for round_index in range(_ROUNDS):
        try:
            failures, state = asyncio.run(_race_prediction_against_confirmation())
        except Exception as exc:  # noqa: BLE001
            if "connect" in str(exc).lower() or "refused" in str(exc).lower():
                pytest.skip(PG_UNAVAILABLE)
            raise
        assert not failures, f"{round_index}회차 오류 — {_describe(failures)}"
        assert state in (_PREDICTION_FIRST, _CONFIRMATION_FIRST), (
            f"{round_index}회차 최종 상태가 두 순차 실행 어느 쪽도 아니다: {state}\n"
            f"  예측 먼저 = {_PREDICTION_FIRST}\n  확정 먼저 = {_CONFIRMATION_FIRST}"
        )


def test_the_row_count_alone_cannot_tell_the_two_apart() -> None:
    """🔴 **행 수로 줄이면 이 축을 놓친다** — 위반 상태도 행은 하나다.

    ⚠ 판정을 `len(rows) == 1`로 축소하면 **혼합 상태 전부**가 통과한다. 그 사실을
    여기서 못 박는다 — 두 정상 결과가 **행 수로는 구분되지 않는다.**
    """
    assert _PREDICTION_FIRST != _CONFIRMATION_FIRST, "두 갈래가 같으면 이 검사가 헛돈다"
    #: 🔴 **값으로 검출 가능한** 위반 상태 — 확정이 커밋됐는데 예측이 정정까지 지웠다.
    #: ⚠ 위반이 **전부** 이렇게 생기지는 않는다 — 값이 정상 갈래와 겹치는 시간 순서
    #:   위반은 상태 대조의 **사각**이다(위 검사 docstring ⓐ).
    violating: _State = (
        "payment", "neutral", "normal",
        Decimal("0.80"), Decimal("0.80"), Decimal("0.80"),
        None, None, None,
        True,
    )
    assert violating not in (_PREDICTION_FIRST, _CONFIRMATION_FIRST), (
        "위반 상태가 정상 갈래에 들어 있다 — 기대값이 너무 넓다"
    )
