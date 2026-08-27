"""국어 문법 어휘가 인명 후보로 오탐돼 출제를 막지 않는지 고정한다.

2026-08-27 실측: `language` 문항 생성 1회차의 3차 시도가 교차 풀이 프롬프트 전송
단계에서 `RedactionBlocked` 로 죽어 `verification_unavailable` 로 종결했다.
문항에 인명은 한 개도 없었다 — 걸린 것은 `서(성씨)+술어+조사` 로 읽힌 「서술어」이고,
한 문장에 두 번 나와 밀도 임계를 넘겼다.

이 파일이 지키는 것은 둘이다.
  ① 대표 문법 문장이 **차단되지 않는다** — 출제 경로가 열려 있다.
  ② 실제 인명은 **여전히 지워진다** — 오탐을 빼느라 미탐을 만들지 않았다.
그리고 `name_exclude` 목록이 추측이 아니라 **측정 결과**임을 재현한다(③).
"""

from __future__ import annotations

import pytest
import yaml  # type: ignore[import-untyped]  # PyYAML은 공식 타입 정보를 제공하지 않는다.

from ai.runtime.redaction import _config, _Redactor, redact

#: 발문·선지에 실제로 쓰이는 형태 그대로 둔다 — 용어만 나열하면 조사 결합형을 못 잡는다.
GRAMMAR_SENTENCES = (
    "㉢의 서술어는 피동 표현이고, ㉣의 서술어는 사동 표현이다.",
    "안은문장의 서술어와 안긴문장의 서술어가 각각 무엇인지 파악해야 한다.",
    "주체 높임이 실현된 서술어와 객체 높임이 실현된 서술어를 구별한 것이다.",
    "㉠은 서술어의 자릿수가 둘이고, ㉡은 목적어를 주었다는 점에서 다르다.",
    "문장성분을 주성분과 부속성분으로 나누어 설명한 것이다.",
    "㉤은 유음화가 일어난 발음이고, ㉥은 비음화가 일어난 발음이다.",
    "안긴문장의 서술절을 설명하였다는 점에서 두 문장은 다르다.",
)

#: 🔴 **일부러 안 뺀 오탐** — 잘린 조각이 그대로 실존 가능한 이름이라 제외하지 않았다.
#: `어문규범`→`문규범`(문 씨 + 규범) · `훈민정음`→`민정음`(민 씨 + 정음).
#: 빼면 그 이름을 가진 학생이 샌다(불변식 3). ⚠ 대신 이 낱말이 든 문장은 **조각이
#: 마스킹된 채** 모델에 간다 — 차단은 아니라 출제가 멈추진 않지만 문면은 훼손된다.
#: 이 트레이드오프를 **테스트로 적어 둔다** — 다음 사람이 「빠뜨렸네」로 읽고 넣지 않도록.
DELIBERATE_EXCEPTIONS = ("문규범", "민정음")

KNOWN_RESIDUAL = (
    "어문규범에 따른 표기와 훈민정음의 표기를 견주어 설명한 것이다.",
)

#: 오탐을 빼도 여기는 반드시 지워져야 한다 — 미탐 감시자.
NAME_SENTENCES = (
    ("김민준이와 박서연은 같은 반입니다.", ("김민준", "박서연")),
    ("서연이 어머니와 민준이 아버지께 연락드렸습니다.", ("서연", "민준")),
    ("이도윤의 성적과 최지우가 받은 점수를 비교했습니다.", ("이도윤", "최지우")),
)

#: ③ 측정 재현용 — 수능 국어 문법 용어. 새 용어를 넣으면 오탐 여부가 여기서 드러난다.
GRAMMAR_TERMS = (
    "음운 음절 자음 모음 받침 초성 중성 종성 교체 탈락 축약 첨가 비음화 유음화 "
    "구개음화 된소리되기 자음군단순화 두음법칙 사이시옷 연음 "
    "형태소 어근 어간 어미 접사 접두사 접미사 파생어 합성어 단일어 "
    "품사 체언 용언 수식언 관계언 독립언 명사 대명사 수사 동사 형용사 관형사 부사 "
    "조사 감탄사 서술어 주어 목적어 보어 부사어 관형어 독립어 "
    "문장성분 주성분 부속성분 홑문장 겹문장 안은문장 안긴문장 이어진문장 "
    "명사절 관형절 부사절 서술절 인용절 "
    "높임법 주체높임 객체높임 상대높임 시제 진행상 완료상 "
    "피동 사동 피동접사 사동접사 부정표현 능동 주동 "
    "담화 발화 지시표현 대용표현 접속표현 맥락 상황맥락 화자 청자 전제 함축 "
    "중세국어 근대국어 훈민정음 아래아 이어적기 끊어적기 거듭적기 "
    "주격조사 목적격조사 관형격조사 부사격조사 호격조사 "
    "표준어 표준발음 맞춤법 띄어쓰기 어문규범"
).split()


@pytest.mark.parametrize("sentence", GRAMMAR_SENTENCES)
def test_grammar_sentence_is_not_blocked(sentence: str) -> None:
    """① 인명이 없는 문법 문장은 전송을 막지 않는다."""

    assert redact(sentence).uncertain is False


@pytest.mark.parametrize("sentence", GRAMMAR_SENTENCES)
def test_grammar_sentence_survives_intact(sentence: str) -> None:
    """① 차단만 안 하는 게 아니라 **문면이 훼손되지 않는다**.

    통째 치환은 차단으로 드러나지만 부분 치환은 조용하다 — 모델이 훼손된 문항을
    받아 풀고, 그 결과가 게이트를 흔든다(`name_exclude` 의 「않고」 주석과 같은 부류).
    """

    assert redact(sentence).masked_text == sentence


@pytest.mark.parametrize(("sentence", "names"), NAME_SENTENCES)
def test_real_names_are_still_removed(sentence: str, names: tuple[str, ...]) -> None:
    """② 오탐 제외가 미탐을 만들지 않았다."""

    masked = redact(sentence).masked_text
    for name in names:
        assert name not in masked


def test_every_measured_false_positive_is_excluded() -> None:
    """③ 문법 용어 × 조사에서 나오는 인명 후보가 남아 있지 않다.

    목록을 손으로 관리하지 않는다 — 새 용어가 오탐을 내면 여기서 red 가 된다.
    """

    redactor = _Redactor(cfg=_config())
    leftovers: set[str] = set()
    for term in GRAMMAR_TERMS:
        for particle in _korean_particles():
            token = f"{term}{particle}"
            for match in redactor._name_candidates(f"밑줄 친 {token} 무엇인지 고르시오"):
                leftovers.add(match.group(1))

    assert leftovers == set(DELIBERATE_EXCEPTIONS), (
        "제외 목록과 측정 결과가 갈렸다. 새 오탐이면 목록에 넣되 **먼저 「이게 이름일 수"
        " 있나」를 물어라** — 이름일 수 있으면 넣지 말고 DELIBERATE_EXCEPTIONS 에 적는다."
        f" 측정: {sorted(leftovers)}"
    )


@pytest.mark.parametrize("sentence", KNOWN_RESIDUAL)
def test_known_residual_degrades_but_does_not_stop_generation(sentence: str) -> None:
    """일부러 남긴 오탐은 **문면을 훼손하되 출제를 멈추지는 않는다**.

    후보 1개는 토큰만 바꾸고 전송을 막지 않는다는 스펙(§2 [A 확정 7/23])에 기대는 자리다.
    ⚠ 여기가 red 로 바뀌면 「드물다」는 전제가 깨진 것이다 — 그때는 제외가 아니라
    `name_candidates` 패턴 쪽(99 #178)을 봐야 한다.
    """

    assert redact(sentence).uncertain is False


def _korean_particles() -> tuple[str, ...]:
    """조사 목록의 정본은 `redaction_patterns.yaml` 하나다 — 테스트가 사본을 만들지 않는다."""

    from ai.runtime.redaction import _PATTERNS_PATH

    raw = yaml.safe_load(_PATTERNS_PATH.read_text(encoding="utf-8"))
    return tuple(raw["particles"]["korean"])
