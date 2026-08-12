"""실 PG 왕복 — 상담 **입력 묶음**과 **초안 본문** (㉻ · 지시서 73 §3·§4).

🔴 **㉻의 결손 둘은 「저장할 곳이 없다」였다.** `ContextBundleRecord`는 대응 테이블이 없어
인메모리 dict에만 살았고(다른 프로세스 워커가 `context_bundle_missing`으로 죽는다),
`DraftRecord.content`는 `draft` 테이블에 컬럼이 없어 본문이 프로세스와 함께 사라졌다
(늦게 성공한 잡의 GET이 `result=None`).

이 파일은 **두 저장소의 실 PG 계약**을 잰다:

| 축 | 요구 |
| --- | --- |
| 테넌트 격리 | 부재와 남의 테넌트가 **똑같이 `None`** · 술어가 **SQL에** 있다 |
| 멱등 | 같은 id + 같은 전문은 행 증가 0 |
| 의미 충돌 | 같은 id + **다른 전문**은 예외 — 조용히 덮지 않는다 |
| 무손실 | JSONB 왕복 뒤 중첩 타입까지 **값이 정확히 같다** |
| FK | 부모 `AI_RUN`이 없으면 실패를 성공으로 번역하지 않는다 |

⚠ **실 LLM은 안 부른다.** ⚠ 자기 테넌트 행만 지운다.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any, Final

import pytest
from pg_hint import PG_UNAVAILABLE
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ai.composition.counsel.stores import (
    CONTEXT_SCHEME,
    DRAFT_SCHEME,
    ContextBundleRecord,
    DraftRecord,
    make_ref,
)
from ai.contracts.composition import (
    CommStyle,
    DraftContext,
    EvidenceFact,
    Frequency,
    Interest,
    LabelSnapshot,
    Sensitivity,
)
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.db.models import Base
from ai.db.repositories.counsel_context_store import (
    ContextBundleConflict,
    PgContextStore,
)
from ai.db.repositories.counsel_draft_store import (
    DraftRecordConflict,
    PgDraftResultStore,
)
from ai.db.repositories.run_store import PgRunStore
from ai.db.settings import get_db_settings

pytestmark = pytest.mark.integration

_TENANT: Final = "t_ctx_draft"
_OTHER: Final = "t_ctx_draft_other"
_T0: Final = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    try:
        return asyncio.run(coro)
    except Exception as exc:  # noqa: BLE001 — 접속 실패만 skip으로 가른다
        if "connect" in str(exc).lower() or "refused" in str(exc).lower():
            pytest.skip(PG_UNAVAILABLE)
        raise


def _context(student_ref: str = "st_a") -> DraftContext:
    """🔴 **중첩 타입을 일부러 다 채운다** — tuple·enum·`None` 필드까지 왕복한다."""
    return DraftContext(
        student_ref=student_ref,
        guardian_ref=f"gd_{student_ref}",
        label_snapshot=LabelSnapshot(
            comm=CommStyle.DATA,
            sensitivity=Sensitivity.ANXIOUS,
            interest=Interest.GRADE,
            frequency=Frequency.FREQUENT,
        ),
        facts=(
            EvidenceFact(label="이번 주 정답률", value="62%", record_id="rec_1"),
            #: ⚠ `record_id=None`도 넣는다 — JSONB가 키를 지우면 여기서 걸린다.
            EvidenceFact(label="제출", value="3건"),
        ),
        evidence_summaries=("지난주 대비 상승", "과제 제출 유지"),
        period_label="2026년 8월",
        fallback_text="이번 주 학습 상황을 정리해 보내드립니다.",
    )


def _bundle(
    bundle_id: uuid.UUID,
    *,
    tenant: str = _TENANT,
    contexts: dict[str, DraftContext] | None = None,
    content_hash: str = "sha256:bundle",
    class_ref: str = "cl_a1",
) -> ContextBundleRecord:
    return ContextBundleRecord(
        id=bundle_id,
        tenant_id=tenant,
        class_ref=class_ref,
        contexts=contexts if contexts is not None else {"st_a": _context()},
        content_hash=content_hash,
        created_at=_T0,
    )


def _draft(
    draft_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    tenant: str = _TENANT,
    content: str = "정답률은 62%였습니다.",
    status: str = "generated",
) -> DraftRecord:
    return DraftRecord(
        id=draft_id,
        run_id=run_id,
        agent_run_id=uuid.UUID(int=7),
        tenant_id=tenant,
        kind="counsel_pack",
        student_ref="st_a",
        guardian_ref="gd_st_a",
        label_snapshot={"comm": "data", "sensitivity": "anxious"},
        status=status,
        fail_reason=None,
        created_at=_T0,
        content=content,
    )


def _run_metadata(execution_id: uuid.UUID, tenant: str = _TENANT) -> Any:  # noqa: ANN401
    """DRAFT의 FK 부모 — 🔴 **손으로 INSERT하지 않고 `begin_run`으로 세운다.**"""
    return ExecutionContext(
        execution_id=execution_id,
        tenant_id=tenant,
        capability=Capability.COMPOSITION,
        input_snapshot_hash="sha256:ctx-draft",
        versions=VersionSet(
            pipeline_version="0.1.0",
            engine_version="counsel-pack-0.1",
            schema_version="0.1",
            contract_version="0.1",
        ),
    ).to_run_metadata(created_at=_T0)


async def _prepare(sessions: async_sessionmaker[AsyncSession], engine: Any) -> None:  # noqa: ANN401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with sessions() as session, session.begin():
        for tenant in (_TENANT, _OTHER):
            await session.execute(
                text("DELETE FROM draft WHERE tenant_id = :t"), {"t": tenant}
            )
            await session.execute(
                text("DELETE FROM counsel_context_bundle WHERE tenant_id = :t"),
                {"t": tenant},
            )
            await session.execute(
                text("DELETE FROM ai_run WHERE tenant_id = :t"), {"t": tenant}
            )


def _sessions() -> tuple[Any, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(get_db_settings().database_url, poolclass=NullPool)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _where_of(statement: str) -> str | None:
    """🔴 **WHERE 절만** 돌려준다 — `SELECT` 목록에도 `tenant_id`가 **있다.**

    ⚠ 처음엔 문장 전체에서 `"tenant_id" in statement`를 봤는데, 그건 **투영 컬럼**을
    보고 통과하는 검사였다 — 술어를 지워도 green이었다(뒤집기 실측 · 로그 85:
    「검사의 이름이 보는 것보다 넓다」).
    ⚠ 공백으로 찾지 않는다 — SQLAlchemy는 `WHERE` 앞에 **개행**을 넣는다.
    """
    match = re.search(r"\bWHERE\b", statement, flags=re.IGNORECASE)
    return None if match is None else statement[match.start() :]


async def _count(
    sessions: async_sessionmaker[AsyncSession], table: str, tenant: str
) -> int:
    async with sessions() as session:
        return int(
            (
                await session.execute(
                    text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"),  # noqa: S608 — 상수다
                    {"t": tenant},
                )
            ).scalar_one()
        )


# ───────────────────────── ContextStore ─────────────────────────


def test_a_context_bundle_survives_the_json_roundtrip() -> None:
    """🔴 **무손실이다** — 중첩 tuple·enum·`None`까지 값이 정확히 같다.

    ⚠ `==`로 레코드 전체를 본다. 필드를 하나씩 세면 **새로 생긴 필드를 안 본다.**
    """

    async def scenario() -> None:
        engine, sessions = _sessions()
        try:
            await _prepare(sessions, engine)
            store = PgContextStore(sessionmaker=sessions)
            record = _bundle(
                uuid.uuid4(), contexts={"st_a": _context("st_a"), "st_b": _context("st_b")}
            )
            ref = await store.put(record)
            assert ref == make_ref(CONTEXT_SCHEME, record.id)

            loaded = await store.get(ref, tenant_id=_TENANT)
            assert loaded == record, (
                "왕복 뒤 값이 달라졌다 — JSONB 직렬화에서 무언가 깎였다"
            )
            #: 🔴 계약값 보존 — 저장 시 재계산해 대체하지 않는다.
            assert loaded is not None and loaded.content_hash == "sha256:bundle"
        finally:
            await engine.dispose()

    _run(scenario())


def test_a_foreign_tenant_cannot_resolve_the_bundle() -> None:
    """🔴 **부재와 남의 테넌트가 똑같이 `None`** — 존재 여부를 알려주지 않는다."""

    async def scenario() -> None:
        engine, sessions = _sessions()
        try:
            await _prepare(sessions, engine)
            store = PgContextStore(sessionmaker=sessions)
            ref = await store.put(_bundle(uuid.uuid4()))

            assert await store.get(ref, tenant_id=_OTHER) is None, "남의 묶음이 나왔다"
            absent = make_ref(CONTEXT_SCHEME, uuid.uuid4())
            assert await store.get(absent, tenant_id=_TENANT) is None
            #: 🔴 **둘을 갈라 센다** — 반환값이 같아 운영에서 «왜 없나»를 못 묻는다.
            assert store.miss_foreign_tenant == 1, store.miss_foreign_tenant
            assert store.miss_absent == 1, store.miss_absent
        finally:
            await engine.dispose()

    _run(scenario())


def test_the_tenant_predicate_is_in_the_sql_not_in_python() -> None:
    """🔴 **남의 행을 PK로 읽은 뒤 파이썬에서 버리는 방식 금지.**

    ⚠ 반환값만 보면 두 구현이 같다 — **발행된 SQL**을 봐야 갈린다. 실행된 문장에
    `tenant_id` 술어가 없으면 red다.
    """

    async def scenario() -> None:
        engine, sessions = _sessions()
        seen: list[str] = []

        from sqlalchemy import event  # noqa: PLC0415

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def _capture(  # noqa: ANN202, PLR0913, PLR0917
            conn: Any,  # noqa: ANN401, ARG001
            cursor: Any,  # noqa: ANN401, ARG001
            statement: str,
            parameters: Any,  # noqa: ANN401, ARG001
            context: Any,  # noqa: ANN401, ARG001
            executemany: bool,  # noqa: ARG001, FBT001
        ) -> None:
            seen.append(statement)

        try:
            await _prepare(sessions, engine)
            store = PgContextStore(sessionmaker=sessions)
            ref = await store.put(_bundle(uuid.uuid4()))
            seen.clear()
            await store.get(ref, tenant_id=_TENANT)

            selects = [s for s in seen if "counsel_context_bundle" in s.lower()]
            assert selects, f"조회 SQL이 안 잡혔다: {seen}"
            wheres = [_where_of(statement) for statement in selects]
            assert all(where is not None for where in wheres), (
                f"WHERE 없는 조회가 있다: {selects}"
            )
            assert all("tenant_id" in (where or "") for where in wheres), (
                f"테넌트 술어가 WHERE에 없다: {wheres}"
            )
        finally:
            await engine.dispose()

    _run(scenario())


def test_the_same_bundle_twice_is_idempotent() -> None:
    """같은 id + 같은 전문 재저장 — 행 증가 0."""

    async def scenario() -> None:
        engine, sessions = _sessions()
        try:
            await _prepare(sessions, engine)
            store = PgContextStore(sessionmaker=sessions)
            record = _bundle(uuid.uuid4())
            first = await store.put(record)
            second = await store.put(record)
            assert first == second
            assert await _count(sessions, "counsel_context_bundle", _TENANT) == 1
        finally:
            await engine.dispose()

    _run(scenario())


def test_a_different_bundle_under_the_same_id_conflicts() -> None:
    """🔴 **같은 id + 다른 전문은 의미 충돌** — 조용히 덮지 않는다.

    ⚠ 덮으면 재개한 워커가 **다른 입력**을 읽고, `content_hash` 대조(불변식 ④)가
    잡기 전까지 판정이 갈린다.
    """

    async def scenario() -> None:
        engine, sessions = _sessions()
        try:
            await _prepare(sessions, engine)
            store = PgContextStore(sessionmaker=sessions)
            bundle_id = uuid.uuid4()
            await store.put(_bundle(bundle_id))
            with pytest.raises(ContextBundleConflict, match=str(bundle_id)):
                await store.put(_bundle(bundle_id, content_hash="sha256:other"))
            #: 🔴 충돌 뒤에도 원래 값 그대로다.
            stored = await store.get(
                make_ref(CONTEXT_SCHEME, bundle_id), tenant_id=_TENANT
            )
            assert stored is not None and stored.content_hash == "sha256:bundle"
        finally:
            await engine.dispose()

    _run(scenario())


def test_two_concurrent_first_writes_do_not_leak_a_driver_error() -> None:
    """🔴 **같은 ref의 동시 최초 저장** — UniqueViolation이 밖으로 새지 않는다.

    ⚠ 같은 값이면 둘 다 성공으로 수렴하고 행은 1개다. 필드가 섞인 혼합 행은 없다.
    """

    async def scenario() -> None:
        engine, sessions = _sessions()
        try:
            await _prepare(sessions, engine)
            store = PgContextStore(sessionmaker=sessions)
            record = _bundle(uuid.uuid4())
            refs = await asyncio.gather(
                store.put(record), store.put(record), store.put(record)
            )
            assert len(set(refs)) == 1, refs
            assert await _count(sessions, "counsel_context_bundle", _TENANT) == 1
            loaded = await store.get(refs[0], tenant_id=_TENANT)
            assert loaded == record, "동시 저장이 혼합 행을 남겼다"
        finally:
            await engine.dispose()

    _run(scenario())


def test_a_malformed_ref_is_refused() -> None:
    """🔴 **ref 파싱은 fail-closed** — 스킴이 다르면 조회 자체가 성립하지 않는다."""

    async def scenario() -> None:
        engine, sessions = _sessions()
        try:
            await _prepare(sessions, engine)
            store = PgContextStore(sessionmaker=sessions)
            with pytest.raises(ValueError, match="context"):
                await store.get(f"pack://{uuid.uuid4()}", tenant_id=_TENANT)
        finally:
            await engine.dispose()

    _run(scenario())


# ───────────────────────── DraftResultStore ─────────────────────────


def test_a_draft_body_survives_the_roundtrip() -> None:
    """🔴 **본문뿐 아니라 전문을 값으로 대조한다** — 컬럼 하나만 보면 나머지가 샌다."""

    async def scenario() -> None:
        engine, sessions = _sessions()
        try:
            await _prepare(sessions, engine)
            execution_id = uuid.uuid4()
            #: 🔴 부모는 `begin_run`이 세운다 — 손으로 INSERT하지 않는다(99 #46).
            await PgRunStore(sessionmaker=sessions).begin_run(_run_metadata(execution_id))
            store = PgDraftResultStore(sessionmaker=sessions)
            record = _draft(uuid.uuid4(), execution_id)

            ref = await store.put(record)
            assert ref == make_ref(DRAFT_SCHEME, record.id)
            loaded = await store.get(ref, tenant_id=_TENANT)
            assert loaded == record, "왕복 뒤 초안 전문이 달라졌다"
        finally:
            await engine.dispose()

    _run(scenario())


def test_a_draft_content_is_stored_verbatim() -> None:
    """🔴 **자르거나 정규화하지 않는다** — 공백·개행·길이 그대로다."""

    async def scenario() -> None:
        engine, sessions = _sessions()
        try:
            await _prepare(sessions, engine)
            execution_id = uuid.uuid4()
            await PgRunStore(sessionmaker=sessions).begin_run(_run_metadata(execution_id))
            store = PgDraftResultStore(sessionmaker=sessions)
            body = "  첫 줄입니다.\n\n둘째 줄입니다. 정답률은 62%였습니다.  "
            record = _draft(uuid.uuid4(), execution_id, content=body)
            ref = await store.put(record)
            loaded = await store.get(ref, tenant_id=_TENANT)
            assert loaded is not None and loaded.content == body, (
                "본문이 손질됐다 — 저장소는 문면을 고치지 않는다"
            )
        finally:
            await engine.dispose()

    _run(scenario())


def test_a_foreign_tenant_cannot_read_the_draft() -> None:
    """🔴 **초안 본문 유출 방어가 마지막 층이다** — 부재와 같은 `None`."""

    async def scenario() -> None:
        engine, sessions = _sessions()
        try:
            await _prepare(sessions, engine)
            execution_id = uuid.uuid4()
            await PgRunStore(sessionmaker=sessions).begin_run(_run_metadata(execution_id))
            store = PgDraftResultStore(sessionmaker=sessions)
            ref = await store.put(_draft(uuid.uuid4(), execution_id))

            assert await store.get(ref, tenant_id=_OTHER) is None, "남의 초안이 나왔다"
            assert (
                await store.get(make_ref(DRAFT_SCHEME, uuid.uuid4()), tenant_id=_TENANT)
                is None
            )
            assert store.miss_foreign_tenant == 1
            assert store.miss_absent == 1
        finally:
            await engine.dispose()

    _run(scenario())


def test_the_draft_tenant_predicate_is_in_the_sql() -> None:
    """🔴 Draft 쪽도 **SQL 술어**다 — 파이썬 후처리 금지."""

    async def scenario() -> None:
        engine, sessions = _sessions()
        seen: list[str] = []

        from sqlalchemy import event  # noqa: PLC0415

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def _capture(  # noqa: ANN202, PLR0913, PLR0917
            conn: Any,  # noqa: ANN401, ARG001
            cursor: Any,  # noqa: ANN401, ARG001
            statement: str,
            parameters: Any,  # noqa: ANN401, ARG001
            context: Any,  # noqa: ANN401, ARG001
            executemany: bool,  # noqa: ARG001, FBT001
        ) -> None:
            seen.append(statement)

        try:
            await _prepare(sessions, engine)
            execution_id = uuid.uuid4()
            await PgRunStore(sessionmaker=sessions).begin_run(_run_metadata(execution_id))
            store = PgDraftResultStore(sessionmaker=sessions)
            ref = await store.put(_draft(uuid.uuid4(), execution_id))
            seen.clear()
            await store.get(ref, tenant_id=_TENANT)

            selects = [s for s in seen if " draft" in s.lower()]
            assert selects, f"조회 SQL이 안 잡혔다: {seen}"
            wheres = [_where_of(statement) for statement in selects]
            assert all(where is not None for where in wheres), (
                f"WHERE 없는 조회가 있다: {selects}"
            )
            assert all("tenant_id" in (where or "") for where in wheres), (
                f"테넌트 술어가 WHERE에 없다: {wheres}"
            )
        finally:
            await engine.dispose()

    _run(scenario())


def test_the_same_draft_twice_is_idempotent() -> None:
    """재개가 같은 초안을 다시 저장해도 행이 안 는다."""

    async def scenario() -> None:
        engine, sessions = _sessions()
        try:
            await _prepare(sessions, engine)
            execution_id = uuid.uuid4()
            await PgRunStore(sessionmaker=sessions).begin_run(_run_metadata(execution_id))
            store = PgDraftResultStore(sessionmaker=sessions)
            record = _draft(uuid.uuid4(), execution_id)
            assert await store.put(record) == await store.put(record)
            assert await _count(sessions, "draft", _TENANT) == 1
        finally:
            await engine.dispose()

    _run(scenario())


@pytest.mark.parametrize(
    ("field", "value"),
    [("content", "다른 본문입니다."), ("status", "template_only")],
)
def test_a_different_draft_under_the_same_id_conflicts(field: str, value: str) -> None:
    """🔴 **본문이든 메타든 다르면 충돌** — 한쪽만 보면 다른 쪽이 조용히 덮인다."""

    async def scenario() -> None:
        engine, sessions = _sessions()
        try:
            await _prepare(sessions, engine)
            execution_id = uuid.uuid4()
            await PgRunStore(sessionmaker=sessions).begin_run(_run_metadata(execution_id))
            store = PgDraftResultStore(sessionmaker=sessions)
            draft_id = uuid.uuid4()
            await store.put(_draft(draft_id, execution_id))
            with pytest.raises(DraftRecordConflict, match=str(draft_id)):
                await store.put(_draft(draft_id, execution_id, **{field: value}))
            stored = await store.get(
                make_ref(DRAFT_SCHEME, draft_id), tenant_id=_TENANT
            )
            assert stored is not None
            assert getattr(stored, field) != value, "충돌인데 값이 덮였다"
        finally:
            await engine.dispose()

    _run(scenario())


def test_a_draft_without_its_parent_run_is_not_translated_into_success() -> None:
    """🔴 **FK 실패를 성공으로 번역하지 않는다** — 부모 AI_RUN이 없으면 예외다.

    ⚠ 여기서 `None`을 돌려주거나 삼키면 *"본문을 저장했다"* 가 거짓이 되고,
    GET은 영영 `result=None`이다(㉻가 정확히 그 형태였다).
    """

    async def scenario() -> None:
        engine, sessions = _sessions()
        try:
            await _prepare(sessions, engine)
            store = PgDraftResultStore(sessionmaker=sessions)
            orphan = _draft(uuid.uuid4(), uuid.uuid4())  # 부모 없음
            with pytest.raises(Exception, match=r"(?i)foreign key|23503|fk_draft"):
                await store.put(orphan)
            assert await _count(sessions, "draft", _TENANT) == 0
        finally:
            await engine.dispose()

    _run(scenario())
