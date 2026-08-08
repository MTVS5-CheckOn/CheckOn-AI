"""팩 결과의 파생 투영이 스냅숏과 갈리지 않는다 — ㉕ 승인 조건 ⓑ (99 #04 · #23).

🔴 **이 파일이 승인 조건이다.** 조건 ⓐ(한 함수가 유도)만으로는 다음 사람이 그 함수를
우회해 컬럼을 직접 채울 수 있다. 경계를 말했으면 그 자리에 red를 남긴다.

검사는 셋이다:

1. **유도값 == 컬럼 후보** — `pack_result_projection()`이 낸 키·값이 ORM 컬럼과 맞는다.
2. 🔴 **덮이지 않은 컬럼이 없다** — ORM 컬럼 전수에서 「파생이 아니라고 선언한 것」을 빼면
   투영 키 집합과 **정확히 같다.** 행 대조만 두면 **컬럼을 늘리고 유도를 안 붙였을 때
   통과한다**(대조 목록에 그 컬럼이 안 들어가니까) — **「무엇을 대조할지」를 ORM에서
   역산**하게 하는 것이 이 검사의 요점이다(선례: `test_problem_store_projection.py`).
3. **투영하지 않는 것** — `results`·`emphasis_points`·`summary`·`plan_dropped`는 스냅숏에만
   있다(설계 §1 — 학생별 조회 축은 `DRAFT`가 갖는다).

⚠ 실 PG 왕복은 `tests/ai/integration/test_pack_result_pg_roundtrip.py`가 본다.
여기는 DB 없이 도는 순수 함수 검사다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Final

from ai.db.repositories.pack_store import (
    NON_PROJECTED_COLUMNS,
    pack_result_projection,
)

from ai.composition.counsel.stores import CounselPackResultRecord
from ai.contracts.composition import DraftStatus, PlanOutcome, StudentResult
from ai.db.models import CounselPackResult as CounselPackResultRow

#: 스냅숏에만 사는 필드 — 투영 키에 나타나면 안 된다(설계 §1).
_SNAPSHOT_ONLY: Final = ("summary", "results", "plan_dropped", "emphasis_points")


def _record() -> CounselPackResultRecord:
    return CounselPackResultRecord(
        id=uuid.uuid4(),
        tenant_id="t-a",
        class_ref="cl_a1",
        summary="2명 중 1명 생성",
        results=(
            StudentResult(
                student_ref="st_1", draft_id=uuid.uuid4(), status=DraftStatus.GENERATED
            ),
            StudentResult(
                student_ref="st_2",
                status=DraftStatus.REJECTED_INSUFFICIENT,
                fail_reason="evidence_missing",
            ),
        ),
        created_at=datetime(2026, 8, 8, 9, 30, tzinfo=UTC),
        plan_outcome=PlanOutcome.ALL_DROPPED,
        plan_dropped=3,
        emphasis_points={"st_1": ("어휘 정확도",)},
    )


def test_the_projection_derives_the_four_columns() -> None:
    projection = pack_result_projection(_record())
    record = _record()
    assert projection["tenant_id"] == record.tenant_id
    assert projection["class_ref"] == record.class_ref
    assert projection["created_at"] == record.created_at
    #: ⚠ enum이 아니라 **값**이다 — 컬럼이 `String`이고 ERD 값목록 대조가 문자열을 본다.
    assert projection["plan_outcome"] == record.plan_outcome.value


def test_no_column_escapes_the_projection() -> None:
    """🔴 ORM 컬럼 전수 − 비파생 선언 == 투영 키. 새 컬럼에 유도를 안 붙이면 red다."""
    orm_columns = {column.name for column in CounselPackResultRow.__table__.columns}
    derived = orm_columns - NON_PROJECTED_COLUMNS
    assert derived == set(pack_result_projection(_record())), (
        "파생 컬럼과 투영 키가 다르다 — 컬럼을 늘렸으면 `pack_result_projection()`에 "
        "유도를 붙이거나 `NON_PROJECTED_COLUMNS`에 **이유와 함께** 등재해야 한다(조건 ⓐ)"
    )


def test_the_scan_sees_the_real_orm_table() -> None:
    """🔴 검사 경로가 끊기면 통과가 아니라 실패다 — 컬럼 0개면 아무것도 안 본 것이다."""
    columns = {column.name for column in CounselPackResultRow.__table__.columns}
    assert len(columns) >= 5, f"ORM 컬럼을 못 읽었다: {columns}"


def test_snapshot_only_fields_are_not_projected() -> None:
    """⚠ 스냅숏에만 사는 것을 투영하면 **같은 사실이 두 자리**에 앉는다(99 #02)."""
    projection = pack_result_projection(_record())
    leaked = [name for name in _SNAPSHOT_ONLY if name in projection]
    assert not leaked, (
        f"스냅숏 전용 필드가 투영에 샜다: {leaked} — 학생별 조회 축은 `DRAFT`가 갖는다"
    )
