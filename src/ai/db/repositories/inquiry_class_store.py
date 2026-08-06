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
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai.db.models import InquiryClass

logger = logging.getLogger(__name__)

#: 키 스코프 = 유니크 제약. 인메모리 dict 키로도 그대로 쓴다.
_Scope = tuple[str, str]

#: 3축 이름 — 예측·정정 컬럼 접두를 만드는 단일 출처(축을 코드에 흩뿌리지 않는다).
AXES = ("topic", "sentiment", "urgency")


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

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
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
        async with self._sessionmaker() as session:
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
            await session.commit()

    async def apply_confirmation(
        self,
        *,
        tenant_id: str,
        inquiry_ref: str,
        corrections: dict[str, str],
    ) -> ConfirmationOutcome:
        async with self._sessionmaker() as session:
            row = await self._select(session, tenant_id, inquiry_ref)
            if row is None:
                return ConfirmationOutcome(found=False)
            outcome = _plan_corrections(_to_record(row), corrections)
            for axis, value in outcome.applied.items():
                setattr(row, f"corrected_{axis}", value)
            for axis in outcome.cleared:  # 되돌리기 — NULL 복원
                setattr(row, f"corrected_{axis}", None)
            row.reviewed_at = self._clock()
            await session.commit()
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
