"""분류 평가셋 저장소 — `INQUIRY_CLASS` 적재·조회·확정 회신 (P2-c · 99 ⓑ).

사양: `docs/06_erd.md` INQUIRY_CLASS · `04` §3.3(confirmations)·§3.5(classify) ·
`part_a/03` §C7. 소유: 박진희 (db.repositories). 인터페이스로 API 계층과 저장 계층을
분리하는 것은 `idempotency.py`·`detection_store.py`와 같은 규약이다.

**키는 자연키 `(tenant_id, inquiry_ref)`다** — 문의 1건 = 분류 1건. 새 UUID를 응답에
노출하지 않고 BE가 이미 아는 값을 참조 키로 쓴다(P0의 `draft_id` 미노출과 같은 함정을
피한다). 마이그레이션 0006의 `uq_inquiry_class_scope`가 이를 강제한다.

🔴 **이 모듈이 지키는 규약 3건** — 하나라도 어기면 평가셋이 깨진다.

① **예측은 덮어쓰지 않는다.** `reviewed_at`이 선 행은 예측 3축·confidence 3축이
   **갱신 대상에서 빠진다**. 나중 예측이 검토된 예측을 덮으면 (예측·정답) 쌍이 어긋난다.
② **같은 값 정정은 정정이 아니다.** `corrected_value`의 값이 예측과 같으면
   `corrected_*`에 쓰지 않고 NULL을 유지한다(P2-b 기록 규약 ①). 강사가 드롭다운을 열어
   같은 값을 다시 골라도 정정으로 세면 **재분류율이 부풀려지고**, `reviewed_at`으로
   "검토함"과 "정정함"을 분리한 의미가 통째로 무너진다.

   🔴 **다만 "이미 정정된 축"에 예측값이 다시 오면 그건 되돌리기다** — `corrected_*`를
   NULL로 복원한다. 종전 구현은 예측하고만 비교해서 이 경우를 ②의 "무시"로 흡수했고,
   강사가 실수를 알아채고 원래 값으로 회신해도 정정이 **영구히 남았다.**
   ⇒ ②가 막으려던 왜곡(재분류율 부풀리기)을 ②의 구현이 만들고 있었다. 되돌리기 허용은
   ②를 깨는 게 아니라 **같은 방향**이다 — 둘 다 분자에서 가짜 정정을 뺀다.
③ **`classified=False`는 적재하지 않는다.** 판정이 없는 건에 enum 값을 채우면 불변식 2
   위반이고 평가셋이 오염된다(P2-b 조건 4). 호출자가 거른다.

⚠ **적재 실패는 fail-closed다** — 멱등 캐시(`idempotency.py`)의 fail-open과 반대다.
저장이 이 기능의 목적인데 실패를 삼키면 "평가셋이 쌓이는 줄 알았는데 비어 있다"가 된다.
캐시 덕에 재시도가 LLM 0회라 500 후 재시도 비용도 없다.

🔴 `llm_call_id`는 **호출부가 넘긴다**(8/5 · 99 ㊻ⓕ 해소). 종전에는 `LlmCallRecord`가
어디에도 적재되지 않아 참조할 값이 없어 NULL 고정이었다 — 이제 LLM_CALL이 적재되므로
**그 판정을 만든 마지막 성공 호출**을 가리킨다. `inquiry_class.llm_call_id`는 실제 FK라
호출부가 **LLM_CALL을 먼저 적재한 뒤** 이 저장소를 불러야 한다(순서가 계약이다).
값이 없으면(캐시 히트·수집 실패) None을 넘긴다 — 없는 행을 가리키는 FK보다 NULL이 낫다.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

#: 🔴 **세 번째 정의를 만들지 않는다**(99 #02) — `ledger_audit.py`가 이미 같은 자리에서
#: 이 alias를 가져다 쓴다. ⚠ 이름의 거처가 counsel 모듈인 것은 어색하지만, 그걸 옮기는
#: 것은 이 PR의 축이 아니다(옮기면 소비자 셋을 같이 건드린다).
from ai.db.counsel_read_model import SessionFactory
from ai.db.models import InquiryClass

logger = logging.getLogger(__name__)

#: 키 스코프 = 유니크 제약. 인메모리 dict 키로도 그대로 쓴다.
_Scope = tuple[str, str]

#: 3축 이름 — 예측·정정 컬럼 접두를 만드는 단일 출처(축을 코드에 흩뿌리지 않는다).
AXES = ("topic", "sentiment", "urgency")

#: 🔴 자문 잠금 키의 **구분자** — `\x00`이 아니다. PG는 문자열에 NUL 바이트를 못 넣는다
#: (counsel 실측: `invalid byte sequence for encoding "UTF8": 0x00`).
_KEY_SEP: Final = "\x1f"
#: 🔴 **테이블 네임스페이스를 앞에 붙인다** — 안 붙이면 다른 테이블이 같은 자연키를 쓸 때
#: 서로를 막는다(정확성 문제가 아니라 **조용히 느려지는** 형태라 원인을 못 찾는다).
_LOCK_NAMESPACE: Final = "inquiry_class"


def lock_key(tenant_id: str, inquiry_ref: str) -> str:
    """자연키 전체를 담은 자문 잠금 키 — 🔴 **정본은 이 함수 하나다** (99 #41 · #42).

    🔴 **자연키의 두 축이 다 들어간다** — 하나라도 빠지면 무관한 요청이 같은 줄에 선다.
    🔴 **쓰기 경로 둘이 같은 함수를 부른다**(`insert_prediction`·`apply_confirmation`).
    키를 각자 조립하면 **두 경로가 서로 다른 줄에 서고**, 그때 나는 사고는 오류가 아니라
    **조용한 의미 갈림**이다(99 #42 — 규약 ①이 동시성에서 깨졌다).
    ⚠ `get()`은 **읽기 전용이라 참여하지 않는다** — 잠금은 쓰기 직렬화용이다.
    ⚠ **공개 함수인 이유는 검사가 이 규칙을 값으로 볼 수 있게 하려는 것**이다 —
    두 경로가 실제로 같은 줄에 서는지는 **실 PG 동시 실행 검사**가 든다(구조가 아니라 행동).
    """
    return _KEY_SEP.join((_LOCK_NAMESPACE, tenant_id, inquiry_ref))


def system_utc_now() -> datetime:
    """주입용 기본 시계 — 이 한 곳만 벽시계를 읽는다(03 §3)."""
    return datetime.now(UTC)


class InquiryClassRecord(BaseModel):
    """저장된 분류 1건 — 예측(고정) + 정정(nullable) + 검토 시각."""

    model_config = ConfigDict(frozen=True)

    topic: str
    sentiment: str
    urgency: str
    confidence_topic: Decimal
    confidence_sentiment: Decimal
    confidence_urgency: Decimal
    corrected_topic: str | None = None
    corrected_sentiment: str | None = None
    corrected_urgency: str | None = None
    reviewed_at: datetime | None = None
    llm_call_id: uuid.UUID | None = None
    """이 판정을 만든 **마지막 성공 LLM 호출**(LLM_CALL 행). 재현 추적의 간선(불변식 8).

    파싱 재시도로 3번 불렀다면 앞의 둘은 버려진 시도이고 판정을 낸 것은 마지막 성공
    호출이다. 시도 전량은 LLM_CALL 행으로 따로 남으니 정보가 사라지지 않는다.
    """


@dataclass(frozen=True)
class ConfirmationOutcome:
    """확정 회신 1건의 결과 — 🔴 **bool로는 세 가지를 구분할 수 없었다.**

    종전 반환 타입이 `bool`이라 호출부가 ⓐ 행이 없다(404) ⓑ 적용됐다 ⓒ **받았지만 한 축도
    적용되지 않았다**를 구분할 방법이 없었다. ⓒ가 성공으로 나가면 BE는 정정이 저장됐다고
    믿는데 `reviewed_at`만 찍히고, 그 행은 규약 ①에 의해 **이후 재예측이 영구 차단**된다.

    ⚠ **일부러 tuple이 아니라 dataclass다.** NamedTuple이면 항상 truthy라 종전 호출부의
    `if not applied:`(404 분기)가 조용히 죽는다 — 반환 타입을 바꾸면서 그 함정을 남기지
    않는다. 호출부는 `outcome.found`를 명시적으로 봐야 한다.
    """

    found: bool
    """대상 행이 존재했는가. `False`면 404(폴백이라 미적재이거나 분류한 적 없음)."""

    applied: Mapping[str, str] = field(default_factory=dict)
    """`corrected_*`에 쓴 축 → 값. 저장 구현이 이걸 그대로 쓴다."""

    cleared: frozenset[str] = frozenset()
    """이전 정정을 취소해 `corrected_*`를 NULL로 되돌린 축."""

    unknown: frozenset[str] = frozenset()
    """3축 밖이라 저장소가 버린 축 — 조용히 버리지 않고 사실을 돌려준다."""

    @property
    def touched(self) -> int:
        """실제로 바뀐 축 수 — 로그가 이 값을 쓴다(요청받은 축 수가 아니라)."""
        return len(self.applied) + len(self.cleared)


@runtime_checkable
class InquiryClassStore(Protocol):
    """분류 평가셋 저장소 인터페이스 — 라우터는 이 타입에만 의존한다."""

    async def get(
        self, *, tenant_id: str, inquiry_ref: str
    ) -> InquiryClassRecord | None: ...

    async def insert_prediction(
        self,
        *,
        tenant_id: str,
        inquiry_ref: str,
        record: InquiryClassRecord,
    ) -> None: ...

    async def apply_confirmation(
        self,
        *,
        tenant_id: str,
        inquiry_ref: str,
        corrections: dict[str, str],
    ) -> ConfirmationOutcome:
        """확정 회신 반영. ⚠ 반환은 `bool`이 아니다 — `ConfirmationOutcome` 참조."""
        ...


def _plan_corrections(
    record: InquiryClassRecord, corrections: dict[str, str]
) -> ConfirmationOutcome:
    """축별로 **세 상태**를 가른다 — 규약 ②(P2-b 기록 규약 ①)를 지키면서 되돌리기를 허용한다.

    | 회신 값 | 현재 `corrected_*` | 판정 |
    | --- | --- | --- |
    | 예측과 같음 | NULL | **무시** — 같은 값 정정은 정정이 아니다(규약 ②) |
    | 예측과 같음 | 값 있음 | 🔴 **되돌리기** — NULL로 복원 |
    | 예측과 다름 | 무관 | **정정** |

    🔴 종전에는 예측하고만 비교해서 두 번째 줄이 첫 줄에 흡수됐다 — 강사가 정정을 실수로
    알아채고 원래 값으로 회신해도 `corrected_*`가 **영구히 남았다.** 규약 ②가 막으려던
    왜곡(재분류율 부풀리기)을 규약 ② 구현이 만들고 있었다.

    ⚠ 되돌리기는 규약 ②와 **같은 방향**이다 — 둘 다 분자(정정된 행)에서 가짜를 뺀다.
    """
    applied: dict[str, str] = {}
    cleared: set[str] = set()
    unknown: set[str] = set()
    for axis, value in corrections.items():
        if axis not in AXES:
            unknown.add(axis)
            continue
        if value != getattr(record, axis):
            applied[axis] = value
        elif getattr(record, f"corrected_{axis}") is not None:
            cleared.add(axis)  # 이전 정정 취소
    if unknown:  # 조용히 버리지 않는다 — 계약이 막고 있지만 저장소는 계약을 믿지 않는다
        logger.warning(
            "inquiry_class.unknown_axes_dropped axes=%s", sorted(unknown)
        )
    return ConfirmationOutcome(
        found=True,
        applied=applied,
        cleared=frozenset(cleared),
        unknown=frozenset(unknown),
    )


class InMemoryInquiryClassStore:
    """프로세스 인메모리 — CI 기본. 재시작 소실·멀티워커 비공유."""

    def __init__(self, clock: Callable[[], datetime] = system_utc_now) -> None:
        self._rows: dict[_Scope, InquiryClassRecord] = {}
        self._clock = clock

    async def get(
        self, *, tenant_id: str, inquiry_ref: str
    ) -> InquiryClassRecord | None:
        return self._rows.get((tenant_id, inquiry_ref))

    async def insert_prediction(
        self,
        *,
        tenant_id: str,
        inquiry_ref: str,
        record: InquiryClassRecord,
    ) -> None:
        scope = (tenant_id, inquiry_ref)
        existing = self._rows.get(scope)
        if existing is not None and existing.reviewed_at is not None:
            # 규약 ① — 검토된 예측은 덮지 않는다.
            logger.info(
                "inquiry_class.prediction_skipped_reviewed inquiry_ref=%s", inquiry_ref
            )
            return
        self._rows[scope] = record

    async def apply_confirmation(
        self,
        *,
        tenant_id: str,
        inquiry_ref: str,
        corrections: dict[str, str],
    ) -> ConfirmationOutcome:
        scope = (tenant_id, inquiry_ref)
        record = self._rows.get(scope)
        if record is None:
            return ConfirmationOutcome(found=False)
        outcome = _plan_corrections(record, corrections)
        self._rows[scope] = record.model_copy(
            update={
                **{f"corrected_{axis}": value for axis, value in outcome.applied.items()},
                **{f"corrected_{axis}": None for axis in outcome.cleared},
                "reviewed_at": self._clock(),
            }
        )
        return outcome

    def clear(self) -> None:
        self._rows.clear()


class PgInquiryClassStore:
    """PG 영속 — 자연키 upsert. 적재 실패는 **fail-closed**(모듈 docstring 참조)."""

    #: 🔴 **행이 없어도 같은 키에 줄을 세운다** (99 #41) — `SELECT … FOR UPDATE`로는
    #: 최초 저장을 직렬화할 수 없다(잠글 행이 아직 없다).
    #: ⚠ **`IntegrityError`를 삼켜 성공으로 치는 것이 아니다** — 충돌 **자체가 안 난다.**
    #:   `ON CONFLICT DO NOTHING`도 아니다: 그러면 규약 ①(검토된 예측 보호)과
    #:   「미검토 행은 갱신한다」가 둘 다 저장소 밖으로 나가 버린다.
    #: ⚠ 잠금은 트랜잭션과 함께 풀린다(`_xact_`) — 세션에 남지 않는다.
    #: ⚠ 해시 충돌이면 **무관한 키가 잠깐 줄을 설 뿐** 정확성은 그대로다.
    #: ⚠ **이 SQL 문면이 저장소 셋에 있다**(`counsel_read_model`·`problem_store`·여기).
    #:   공용으로 올릴지는 별건이다 — 그러려면 B 소유 파일을 같이 건드려야 한다.
    _LOCK_SQL = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")

    def __init__(
        self,
        *,
        sessionmaker: SessionFactory,
        clock: Callable[[], datetime] = system_utc_now,
        new_id: Callable[[], uuid.UUID] = uuid.uuid4,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock
        self._new_id = new_id

    async def get(
        self, *, tenant_id: str, inquiry_ref: str
    ) -> InquiryClassRecord | None:
        async with self._sessionmaker() as session:
            row = await self._select(session, tenant_id, inquiry_ref)
            return _to_record(row) if row is not None else None

    async def insert_prediction(
        self,
        *,
        tenant_id: str,
        inquiry_ref: str,
        record: InquiryClassRecord,
    ) -> None:
        async with self._sessionmaker() as session, session.begin():
            #: 🔴 **읽기 전에 잠근다** — 행이 없어도 키에 줄을 세우려면 이 순서여야 한다.
            #:   순서가 계약이다: 트랜잭션 → 잠금 → SELECT → 판정 → INSERT/UPDATE → COMMIT.
            await session.execute(
                self._LOCK_SQL, {"key": lock_key(tenant_id, inquiry_ref)}
            )
            existing = await self._select(session, tenant_id, inquiry_ref)
            if existing is not None:
                if existing.reviewed_at is not None:
                    # 규약 ① — 검토된 예측은 덮지 않는다.
                    logger.info(
                        "inquiry_class.prediction_skipped_reviewed inquiry_ref=%s",
                        inquiry_ref,
                    )
                    return
                for field, value in record.model_dump().items():
                    setattr(existing, field, value)
            else:
                session.add(
                    InquiryClass(
                        id=self._new_id(),
                        tenant_id=tenant_id,
                        inquiry_ref=inquiry_ref,
                        # `llm_call_id`는 record가 들고 온다 — 호출부가 LLM_CALL을 먼저
                        # 적재했다는 전제다(FK · 모듈 docstring 참조).
                        **record.model_dump(),
                    )
                )
            #: ⚠ **명시 `commit()`을 뺐다** — `session.begin()`이 블록 끝에서 커밋한다.
            #:   둘을 같이 두면 *"이미 트랜잭션이 있다"* 로 죽는다.

    async def apply_confirmation(
        self,
        *,
        tenant_id: str,
        inquiry_ref: str,
        corrections: dict[str, str],
    ) -> ConfirmationOutcome:
        async with self._sessionmaker() as session, session.begin():
            #: 🔴 **예측 갱신과 같은 줄에 선다**(99 #42) — 같은 자연키, 같은 `lock_key()`.
            #:   빠지면 `_plan_corrections()`가 **낡은 예측**과 비교하고, 늦은 예측이
            #:   **검토된 행을 덮는다**(규약 ① 위반). 자문 잠금은 **협력형**이라
            #:   **참여하지 않는 경로는 막히지 않는다** — 한쪽만 잠그는 것은 안 잠근 것이다.
            #: ⚠ **행을 새로 만들지 않는다** — 없으면 아래에서 `found=False`다.
            await session.execute(
                self._LOCK_SQL, {"key": lock_key(tenant_id, inquiry_ref)}
            )
            row = await self._select(session, tenant_id, inquiry_ref)
            if row is None:
                return ConfirmationOutcome(found=False)
            outcome = _plan_corrections(_to_record(row), corrections)
            for axis, value in outcome.applied.items():
                setattr(row, f"corrected_{axis}", value)
            for axis in outcome.cleared:  # 되돌리기 — NULL 복원
                setattr(row, f"corrected_{axis}", None)
            row.reviewed_at = self._clock()
            #: ⚠ 명시 `commit()`은 없다 — `session.begin()`이 블록 끝에서 커밋한다.
            return outcome

    @staticmethod
    async def _select(
        session: AsyncSession, tenant_id: str, inquiry_ref: str
    ) -> InquiryClass | None:
        result = await session.execute(
            select(InquiryClass).where(
                InquiryClass.tenant_id == tenant_id,
                InquiryClass.inquiry_ref == inquiry_ref,
            )
        )
        return result.scalar_one_or_none()


def _to_record(row: InquiryClass) -> InquiryClassRecord:
    return InquiryClassRecord(
        topic=row.topic,
        sentiment=row.sentiment,
        urgency=row.urgency,
        confidence_topic=row.confidence_topic,
        confidence_sentiment=row.confidence_sentiment,
        confidence_urgency=row.confidence_urgency,
        corrected_topic=row.corrected_topic,
        corrected_sentiment=row.corrected_sentiment,
        corrected_urgency=row.corrected_urgency,
        reviewed_at=row.reviewed_at,
        llm_call_id=row.llm_call_id,
    )


__all__ = [
    "AXES",
    "ConfirmationOutcome",
    "InMemoryInquiryClassStore",
    "InquiryClassRecord",
    "InquiryClassStore",
    "PgInquiryClassStore",
    "system_utc_now",
]
