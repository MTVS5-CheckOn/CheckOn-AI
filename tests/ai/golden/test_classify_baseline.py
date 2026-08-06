"""🔴 축별 **다수 클래스 baseline** — 기준의 검증력을 재는 분모 (99 ㉡).

`urgency` 정확도 87.5%가 기준 ≥85%를 통과했는데, 같은 표본에서 `immediate` 재현율은
**33.3%**(5/15)다 — **긴급 문의의 2/3를 놓치면서 합격**했다.

🔴 **원인은 모델이 아니라 기준이다.** 코퍼스 분포가 `normal` 65 / `immediate` 15라
**"전부 normal"이라고 답하는 상수 분류기의 정확도가 81.25%** 이고, 기준 85%는 그보다
**3.75%p** 높을 뿐이다. 같은 85%가 `topic`에서는 +60%p를 요구한다 — **같은 숫자가 축마다
다른 것을 요구한다.**

⇒ 정확도 기준은 **baseline과 짝으로만** 의미가 있다. 이 파일은 그 baseline이 **코퍼스에서
계산되는 값**임을 못 박는다 — 사람이 문서에 적어 두면 코퍼스가 바뀔 때 갱신을 잊고,
그게 ㉡을 만든 형태다(기준 85%가 분포와 무관하게 굳어 있었다).

⚠ **여기서 임계값을 정하지 않는다.** 08 §7과 99 ㉡이 *"표본 15건이라 기준을 세울 근거가
얇다"* 로 판정해 뒀고 이 파일은 그 판정을 뒤집지 않는다. 잠그는 것은 **분모**다.
"""

from __future__ import annotations

from collections import Counter
from typing import Final

from ai.contracts.counsel import (
    InquirySentiment,
    InquiryTopic,
    InquiryUrgency,
)
from ai.evaluation.golden.classify.corpus import CASES

#: 축 이름 → 그 축의 소수 클래스(놓치면 피해가 큰 쪽). `topic`은 없다 — 판정 근거는
#: `08_evaluation_plan.md` §7 참조(4종 모두 놓쳐도 후속 경로가 같다).
_MINORITY: Final[dict[str, str | None]] = {
    "topic": None,
    "sentiment": InquirySentiment.COMPLAINT.value,
    "urgency": InquiryUrgency.IMMEDIATE.value,
}


def majority_baseline(axis: str) -> float:
    """그 축에서 **"항상 다수 클래스"라고 답하는 상수 분류기**의 정확도.

    정확도 기준이 이 값보다 얼마나 위인가가 **그 기준의 검증력**이다.
    """
    dist = Counter(getattr(case, axis).value for case in CASES)
    return max(dist.values()) / len(CASES)


def test_baselines_match_the_documented_values() -> None:
    """🔴 문서(08 §7 · 99 ㉡)에 적힌 baseline이 **코퍼스에서 실제로 나오는 값**이다.

    ⚠ 이 단정이 red가 되면 **코퍼스가 바뀐 것**이고, 그러면 §7의 요구 개선폭과 2·3차
    회차 비교가 함께 흔들린다 — 문서를 고치기 전에 *"표본이 바뀐 것"* 을 먼저 기록해야
    한다(§7의 비교 단절 경고).
    """
    assert majority_baseline("topic") == 0.25
    assert majority_baseline("sentiment") == 0.625
    assert majority_baseline("urgency") == 0.8125


def test_the_urgency_criterion_has_almost_no_power() -> None:
    """🔴 **이 파일이 존재하는 이유** — `urgency` 기준 85%는 baseline보다 3.75%p 위다.

    상수 분류기가 81.25%를 내는 축에서 85%를 요구하는 것은 *"거의 아무것도 요구하지
    않는다"* 와 같다. 이 단정은 **그 사실이 지워지지 않게** 잠근다 — 값을 올리라는
    뜻이 아니다(표본 15건이라 지금은 못 정한다 · 99 ㉡).

    ⚠ 기준을 올려 이 테스트를 초록으로 만들지 마라. 그건 **표본 확보 뒤**의 일이고,
    그때는 이 단정의 기대값(`< 0.10`)을 근거와 함께 바꾸면 된다.
    """
    criterion = 0.85
    margin = criterion - majority_baseline("urgency")
    assert margin < 0.10, (
        f"urgency 기준의 요구 개선폭이 {margin:.2%}p로 커졌다 — 기준이나 코퍼스 분포가 "
        "바뀐 것이다. 08 §7의 검증력 표와 99 ㉡을 함께 갱신하라"
    )


def test_every_axis_declares_whether_it_has_a_minority_class() -> None:
    """⚠ **판정 없이 넘어가지 않는다** — 축마다 소수 클래스 유무를 명시한다.

    §7이 `sentiment`만 재현율 기준을 챙긴 것이 이 공백이었다. `urgency`도 같은 종류의
    실패(긴급을 인박스에 못 올린다)인데 기준이 없었다.
    """
    axes = {"topic", "sentiment", "urgency"}
    assert set(_MINORITY) == axes, "축이 늘었는데 소수 클래스 판정이 없다"
    for axis, minority in _MINORITY.items():
        if minority is None:
            continue
        labels = {getattr(case, axis).value for case in CASES}
        assert minority in labels, f"{axis}의 소수 클래스 {minority}가 코퍼스에 없다"


def test_topic_is_balanced_so_its_baseline_is_the_uniform_share() -> None:
    """⚠ `topic` baseline 25%는 **균등 분포일 때만** 성립한다 — 그 전제를 잠근다.

    4종이라고 자동으로 25%가 아니다. 분포가 기울면 baseline이 올라가고 §7의 요구
    개선폭(+60%p)이 거짓이 된다.
    """
    dist = Counter(case.topic.value for case in CASES)
    assert len(dist) == len(InquiryTopic), "topic 4종이 코퍼스에 다 있지 않다"
    assert len(set(dist.values())) == 1, f"topic 분포가 균등하지 않다: {dict(dist)}"
