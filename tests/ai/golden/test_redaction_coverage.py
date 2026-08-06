"""형태 커버리지 매트릭스 — **축의 곱집합을 생성해 빈 칸을 CI가 찾는다.**

🔴 **왜 이 파일이 생겼나 — 코퍼스 편중이 세 번 났다.**

| | 코퍼스가 빠뜨린 축 | 결과 |
| --- | --- | --- |
| ⓒ (8/5) | 별명형만 있고 **정식 성명형**이 없었다 | 미탐 7종 |
| ⓛ (8/6) | 조사 없는 형태만 있고 **조사 결합형**이 없었다 | 트립와이어 오차단 |
| 이번 (8/6) | 관계어 앞 성명·**학원 빈출어**가 없었다 | 미탐 14 · 오탐 9 |

세 번 다 **"미탐 0" 게이트가 통과하는 동안** 샜다. 코퍼스는 사람이 고른 표본이라
**안 넣은 형태는 안 재진다** — 게이트가 통과한 방식 자체가 구멍이 안 보인 이유였다.

⚠ **성질(멱등성)로는 미탐을 못 잡는다**(ⓛ에서 한 겹 올렸지만) — 안 잡힌 건 두 번 돌려도
안 잡힌다. 그래서 형태 축이 계속 샜다.

**이 파일이 하는 것:** 이름꼴 × 뒤따르는 말의 **곱집합을 코드로 생성**하고 각 칸에
`redact`를 돌린다. 코퍼스에 그 형태가 있든 없든 **엔진이 실제로 잡는지**를 잰다 —
코퍼스 커버리지가 아니라 **엔진 커버리지**다. 빈 칸은 `_KNOWN_GAPS`에 사유와 함께
적어야 하고, 목록에 없는 빈 칸이 생기면 red다. 반대로 **메워졌는데 목록에 남아 있으면**
그것도 red다(목록이 실제보다 크면 새 구멍을 숨긴다).
"""

from __future__ import annotations

import itertools

import pytest

from ai.runtime.redaction import redact

#: 이름꼴 축 — 실제로 들어오는 표기 형태.
_NAME_FORMS: dict[str, str] = {
    "정식성명3자": "박서연",
    "별명형(성생략+이)": "서연이",
    "영문명": "Sarah",
    "성명2자": "김철",  # ⚠ 알려진 한계(99) — 성+이름 1자
}

#: 뒤따르는 말 축 — 이름 뒤에 실제로 오는 것.
_FOLLOWERS: dict[str, str] = {
    "조사(가)": "가 결석했어요",
    "조사(은)": "은 성실합니다",
    "조사(의)": "의 성적표입니다",
    "호칭(어머니)": " 어머니입니다",
    "호칭(학생)": " 학생의 성적",
    "호칭(양)": " 양이 결석",
    "관계어(엄마)": " 엄마입니다",
    "관계어(아버지)": " 아버지입니다",
    "관계어(할머니)": " 할머니가 오세요",
    "관계어(보호자)": " 보호자입니다",
    "관계어(형)": " 형입니다",
}

#: 🔴 **메우지 못한 칸 — 사유와 등재처를 적는다.** 여기 없는 빈 칸이 생기면 red.
#: 목록이 줄어드는 방향으로만 움직여야 한다(늘리려면 99에 근거가 있어야 한다).
#: ⚠ 목록을 손으로 짐작하지 않았다 — 매트릭스를 먼저 돌려 **실제로 빈 칸만** 적었다.
#: (초안에서 "성명2자는 전부 구멍"으로 넓게 적었다가 `test_known_gaps_are_still_gaps`가
#:  잡았다 — `김철 어머니`는 긴 호칭 갈래가 2~3자를 허용해 **이미 잡힌다**.)
_KNOWN_GAPS: frozenset[tuple[str, str]] = frozenset(
    {
        # ── 성+이름 1자(`김철`) — 조사·짧은 관계어 갈래가 `[$surnames][가-힣]{2}`로
        #    3자를 요구한다. 긴 호칭(어머니·학생)은 2~3자를 허용해 잡힌다. 99 등재분.
        ("성명2자", "조사(가)"),
        ("성명2자", "조사(은)"),
        ("성명2자", "조사(의)"),
        ("성명2자", "호칭(양)"),
        ("성명2자", "관계어(엄마)"),
        ("성명2자", "관계어(아버지)"),
        ("성명2자", "관계어(할머니)"),
        ("성명2자", "관계어(보호자)"),
        ("성명2자", "관계어(형)"),
        # ── 영문명: **라틴 조사 목록을 넓히지 않는다** — `의`·`을`을 더하면
        #    `ProblemPack의` 같은 도메인 용어가 걸린다(8/5 실측). 호칭·관계어 결합은
        #    한국어 화자가 영문명에 붙이는 형태가 아니라 축을 열지 않았다.
        ("영문명", "조사(은)"),
        ("영문명", "조사(의)"),
        *(
            ("영문명", follower)
            for follower in _FOLLOWERS
            if follower.startswith(("호칭", "관계어"))
        ),
    }
)


def _is_masked(name: str, sentence: str) -> bool:
    """이름 조각이 산출물에서 사라졌는가 — 토큰 종류는 묻지 않는다.

    ⚠ `⟪이름N⟫`이든 `⟪확인필요⟫`든 **원문이 남지 않으면 통과**다. 확신 등급은 이
    매트릭스의 관심사가 아니고(그건 골든 `expected`가 본다), 여기서 재는 건 **유출 여부**다.
    """
    return name not in redact(sentence).masked_text


@pytest.mark.parametrize(
    ("form", "follower"),
    sorted(itertools.product(_NAME_FORMS, _FOLLOWERS)),
)
def test_every_cell_is_covered_or_a_known_gap(form: str, follower: str) -> None:
    """🔴 곱집합의 모든 칸 — 마스킹되거나, 사유가 적힌 알려진 구멍이거나."""
    name = _NAME_FORMS[form]
    masked = _is_masked(name, name + _FOLLOWERS[follower])
    if (form, follower) in _KNOWN_GAPS:
        pytest.skip(f"알려진 구멍: {form} × {follower}")
    got = redact(name + _FOLLOWERS[follower]).masked_text
    assert masked, (
        f"새 구멍: {form}({name}) × {follower} → {got!r} — "
        "메우든지 _KNOWN_GAPS에 사유와 99 등재처를 적어라"
    )


@pytest.mark.parametrize(("form", "follower"), sorted(_KNOWN_GAPS))
def test_known_gaps_are_still_gaps(form: str, follower: str) -> None:
    """🔴 반대 방향 — 메워졌는데 목록에 남아 있으면 red.

    목록이 실제보다 크면 **새 구멍을 그 그늘에 숨긴다.** 줄어드는 방향으로만 움직인다.
    """
    name = _NAME_FORMS[form]
    assert not _is_masked(name, name + _FOLLOWERS[follower]), (
        f"{form} × {follower}이 이제 마스킹된다 — _KNOWN_GAPS에서 빼라"
    )


def test_the_matrix_actually_spans_the_axes() -> None:
    """⚠ 검사 경로 절단 검출 — 축이 비면 위 대조가 공허하게 통과한다.

    `test_gate_feedback_coverage`의 `_MIN_CODES`와 같은 자리다.
    """
    assert len(_NAME_FORMS) >= 4
    assert len(_FOLLOWERS) >= 10
    covered = len(_NAME_FORMS) * len(_FOLLOWERS) - len(_KNOWN_GAPS)
    assert covered >= 24, f"실제로 재는 칸이 {covered}개뿐이다"


def test_relation_terms_are_the_axis_that_leaked() -> None:
    """이번 결함의 축이 매트릭스에 실제로 들어 있다 — 표만 늘리고 안 재면 소용없다."""
    relations = [f for f in _FOLLOWERS if f.startswith("관계어")]
    assert len(relations) >= 5
    for follower in relations:
        assert _is_masked("박서연", "박서연" + _FOLLOWERS[follower]), follower
