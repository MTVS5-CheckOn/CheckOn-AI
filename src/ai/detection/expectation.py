"""기대치 입력 층 — 지문 × 유형 (04 §1 "기대치 입력 층" · 13 §4-3-4).

**순수 함수다.** 같은 입력 → 같은 기대치이며 시계·난수·LLM을 쓰지 않는다(불변식 1·8).

**무엇을 푸는가.** 판정 입력을 실제 정답률에서 **잔차(실제 − 기대)** 로 바꿔 "어려운
지문이 걸린 주라 떨어진 것"과 "진짜 무너진 것"을 구분한다. 실측상 **지문 × `type_tag`가
문항 단위 효과의 66%를 회수**하고 영역×유형 같은 속성 단위는 3%뿐이다(13 §4-3).

**규칙 셋**
1. 조합 표본이 `min_n` 이상이면 **그 조합의 실측 누적 평균**을 기대치로 쓴다.
2. 미달이거나 `passage_ref`·`type_tag`가 없으면 **전체 평균 폴백** — 보정 없음과
   **동치**라 콜드 스타트 회귀 위험이 0이다(전량 폴백이면 잔차 ≈ 기존 하락폭).
3. **`type_tag`가 없는 이벤트는 기대·실제 모수 양쪽에서 제외**한다(13 §5-5 ③).
   B 진단의 `tag_confirmed=False` 취급과 **대칭**이며, 한쪽만 빼면 baseline이 오염돼
   전체가 계통적으로 밀린다.

⚠ **강사 난이도 라벨을 기대치로 쓰지 않는다** — 라벨이 비단조라는 실측이 같은 검증에서
나왔다(13 §4-2: NORMAL 34.6% < HARD 49.2%).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import NamedTuple


class PassageTypeKey(NamedTuple):
    """기대치 조합 키 — 지문/자료 묶음 × 유형.

    **학생 식별자를 담지 않는다** — 조합의 속성이지 학생 데이터가 아니다(개인정보 최소 수집).
    """

    passage_ref: str
    type_tag: str


@dataclass(frozen=True)
class ExpectationStats:
    """조합별 실측 누적 스냅숏 — 저장소에서 읽어 온 순수 데이터.

    `combos`의 값은 `(응답 수, 정답 수)`다. 비율이 아니라 원시 카운트를 들고 있어야
    표본 수 판정(`min_n`)과 누적 갱신이 같은 값에서 나온다.
    """

    combos: dict[PassageTypeKey, tuple[int, int]] = field(default_factory=dict)
    overall_responses: int = 0
    overall_corrects: int = 0

    def combo_accuracy(self, key: PassageTypeKey, min_n: int) -> float | None:
        """조합 실측 평균 — 표본 미달이면 None(폴백 신호)."""
        stat = self.combos.get(key)
        if stat is None:
            return None
        responses, corrects = stat
        if responses < min_n:
            return None
        return corrects / responses

    def overall_accuracy(self) -> float | None:
        """누적 전체 평균 — 통계가 비면 None(호출자가 이번 스냅숏 평균을 쓴다)."""
        if self.overall_responses <= 0:
            return None
        return self.overall_corrects / self.overall_responses


@dataclass(frozen=True)
class ExpectationResult:
    """한 주(또는 한 묶음)의 기대치 산출 결과 — `FEATURE_WEEK.metrics`로 나간다."""

    expected: float | None
    """기대 정답률(문항 수 가중 평균). 모수가 0이면 None — 판정 자체가 성립하지 않는다."""

    counted_events: int
    """모수에 든 이벤트 수(태그 있는 것만)."""

    combo_events: int
    """조합 실측을 쓴 이벤트 수."""

    fallback_events: int
    """전체 평균 폴백을 쓴 이벤트 수."""

    excluded_untagged: int
    """`type_tag` 부재로 **양쪽에서 제외**된 이벤트 수 — 관측용(13 §5-5 ③)."""


def expected_accuracy(
    events: Sequence[tuple[str | None, str | None, bool]],
    *,
    stats: ExpectationStats,
    min_n: int,
    session_overall: float,
) -> ExpectationResult:
    """이벤트 묶음의 기대 정답률을 산출한다. 순수 함수.

    `events`는 `(passage_ref, type_tag, correct)` 튜플이다 — 계약 타입을 직접 받지 않아
    저장소·계약 변화에 덜 물린다(계산과 I/O 분리 · 03 §2).

    `session_overall`은 누적 통계가 비었을 때 쓸 **이번 스냅숏의 전체 평균**이다.
    콜드 스타트에서 기대 = 실제 전체 평균이 되어 **잔차 ≈ 기존 하락폭**이 된다.
    """
    overall = stats.overall_accuracy()
    fallback = overall if overall is not None else session_overall

    total = 0.0
    counted = 0
    combo_events = 0
    fallback_events = 0
    excluded = 0
    for passage_ref, type_tag, _correct in events:
        if type_tag is None:
            # 🔴 태그 없는 이벤트는 기대·실제 **양쪽**에서 뺀다(13 §5-5 ③).
            # 한쪽만 빼면 baseline이 오염돼 전 판정이 계통적으로 밀린다.
            excluded += 1
            continue
        counted += 1
        combo = (
            stats.combo_accuracy(PassageTypeKey(passage_ref, type_tag), min_n)
            if passage_ref is not None
            else None
        )
        if combo is None:
            total += fallback
            fallback_events += 1
        else:
            total += combo
            combo_events += 1

    return ExpectationResult(
        expected=(total / counted) if counted else None,
        counted_events=counted,
        combo_events=combo_events,
        fallback_events=fallback_events,
        excluded_untagged=excluded,
    )


__all__ = [
    "ExpectationResult",
    "ExpectationStats",
    "PassageTypeKey",
    "expected_accuracy",
]
