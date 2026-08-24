"""라벨 확정 **집계** 저장소 — 인터페이스와 인메모리 구현(2026-08-25 · 99 #239).

🔴 **왜 인터페이스인가** — 구현이 **둘이 될 것이 이미 정해졌다**: 지금은 프로세스 안
카운터이고, 누적이 필요해지면 **PG** 다. 실측(#239)이 그 필요를 확정했다 —
🔴 **재배포마다 0 이 되는데 「군집의 착수 근거」는 한 달 단위**라 메모리로는 못 산다.
⚠ 🔴 **「미리 추상화」가 아니다** — 두 구현만 견디면 되고, 이 저장소에 같은 형태가
이미 여럿 있다(`InquiryClassStore` · `ContextStore` · `DraftResultStore`).

🔴 **`guardian_ref` 는 키에도 값에도 없다** — №92 판정의 전부다. 키는 넷뿐이다:
`(axis, 제안값, 확정값, action)`. ⚠ 🔴 `tenant_id` 도 **안 담는다** — «가르면 무엇이
좋아지나» 를 못 적었고 **테넌트가 적으면 그 자체가 식별 축**이다(99 #233).

⚠ 🔴 **PG 구현은 아직 없다** — `db/models.py` 와 마이그레이션이 **양자 승인**이라
준영님 승인 전에는 못 연다. 🔴 승인이 나면 **구현 하나를 더하는 것으로 끝난다** —
라우터도 검사도 안 바뀐다(그게 이 자리의 값이다).
"""

from __future__ import annotations

from collections import Counter
from typing import Protocol

#: 🔴 집계 키 — `(axis, 제안값, 확정값, action)`. **개인 참조가 없다.**
LabelConfirmationKey = tuple[str, str, str, str]


class LabelConfirmationStore(Protocol):
    """라벨 확정 집계 — **더하고 읽는다.** 그 둘뿐이다."""

    def add(self, key: LabelConfirmationKey) -> None:
        """확정 하나를 집계에 더한다. 🔴 400 으로 거절된 것은 안 부른다."""
        ...

    def snapshot(self) -> dict[LabelConfirmationKey, int]:
        """지금까지의 집계 — 🔴 **읽는 쪽도 개인 참조를 못 담는다**(키가 넷뿐이다)."""
        ...

    def clear(self) -> None:
        """비운다 — 검사 격리용(모듈 규약 · 99 #237)."""
        ...


class InMemoryLabelConfirmationStore:
    """프로세스 안 카운터 — 🔴 **재배포하면 0 이 된다.**

    선례: `db/repositories/counsel_context_store.py` 의 `self.miss_absent`
    (프로세스 안 정수 · 개인 데이터 미보존 · 99 #23).
    """

    def __init__(self) -> None:
        self._counts: Counter[LabelConfirmationKey] = Counter()

    def add(self, key: LabelConfirmationKey) -> None:
        self._counts[key] += 1

    def snapshot(self) -> dict[LabelConfirmationKey, int]:
        return dict(self._counts)

    def clear(self) -> None:
        self._counts.clear()


_default: LabelConfirmationStore | None = None


def build_label_confirmation_store() -> LabelConfirmationStore:
    """공용 저장소 — 🔴 **지금은 인메모리 하나뿐**이다(PG 는 승인 전).

    ⚠ `STORE_BACKEND` 로 안 가른다 — **고를 것이 하나**라 분기가 거짓이 된다.
    승인이 나면 그때 이 함수가 가른다(`build_inquiry_class_store` 선례).
    """
    global _default
    if _default is None:
        _default = InMemoryLabelConfirmationStore()
    return _default


def reset_default_label_confirmation_store() -> None:
    """공용 저장소를 비운다 — 검사 격리용."""
    if _default is not None:
        _default.clear()


__all__ = [
    "InMemoryLabelConfirmationStore",
    "LabelConfirmationKey",
    "LabelConfirmationStore",
    "build_label_confirmation_store",
    "reset_default_label_confirmation_store",
]
