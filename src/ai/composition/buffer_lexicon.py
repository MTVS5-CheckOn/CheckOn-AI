"""완충 사전 로더 — `buffer_lexicon.yaml`의 금칙(A군)·치환(B군)을 읽는다.

정본: `docs/part_a/05_tone_mapping.md` §4. 어휘는 전부 yaml이 원본이며 이 모듈은 읽기·검출만
한다(코드에 어휘 하드코딩 금지 — 03_coding_rules §1). 로더 패턴은 `runtime/redaction.py`를
따른다.

**A군 활용형 검출 — 99 D ⑰ 해소 완료(8/5).** 채택안은 **①+② 조합**이다:
- **② 코드가 종성 결합을 처리한다** — 어간 끝 음절에 종성이 없을 때만 -ㄴ·-ㄹ·-ㅁ·-ㅂ·-ㅆ를
  붙여 함께 본다(`게으른`·`게으름`·**`산만합니다`**). 접두 매칭은 하지 않는다.
- **① 코드로 만들 수 없는 불규칙만 사전에 등재**한다 — `게을러`·`산만해`·`머리가 나빠` 3항.
  A군 중 활용이 있는 어간이 이 셋뿐이다(나머지는 명사).

🔴 **판정도 단일 참조가 됐다** — 브리핑 게이트가 자기 루프를 버리고 `find_forbidden`을
부른다. 종전에는 어휘만 단일화되고(99 #15 ⓒ) 판정은 둘로 갈려 있었고, 그게 ⑰ 사고의
구조적 원인이었다.

**수용된 비용:** 문맥을 보지 않으므로 `산만한 분위기`(환경 서술)도 걸린다 — A군은 치환
불가·문장 재생성이라 LLM이 다시 쓰면 되고, 문맥 판정을 넣으면 결정론 게이트가 아니게
된다(불변식 1). 판단 경위는 99 D ⑰이 정본이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

import yaml  # type: ignore[import-untyped]

_LEXICON_PATH: Final = Path(__file__).parent / "buffer_lexicon.yaml"

#: 05 §4 표제 "금칙·치환 53항" — 사전이 문서와 어긋나면 로드가 실패한다(정합 가드).
#: A군 23(금칙 20 + **불규칙 활용 3** — 8/5 ⑰ 해소) + B군 30.
EXPECTED_TERM_COUNT: Final = 53


class BufferLexiconError(ValueError):
    """완충 사전 데이터가 계약을 위반했다 — 기동 실패(fail-closed)."""


@dataclass(frozen=True)
class Replacement:
    """B군 치환 1건 — 단정 표현을 관찰·상태 서술로."""

    source: str
    target: str
    """빈 문자열이면 삭제(§4의 비교 함의 제거 항목)."""


@dataclass(frozen=True)
class BufferLexicon:
    """`buffer_lexicon.yaml` 전체 — 순수 데이터."""

    version: str
    forbidden: tuple[str, ...]
    """A군 — 치환 불가. 검출 시 해당 블록 재생성(§4)."""

    replacements: tuple[Replacement, ...]
    """B군 — 단정 → 관찰·상태 서술."""


_HANGUL_BASE: Final = 0xAC00
_HANGUL_LAST: Final = 0xD7A3
_JONGSEONG_COUNT: Final = 28

#: 어간 끝 음절에 **결합**해 활용형을 만드는 종성 코드 — 이것만 처리한다(99 D ⑰ 해소).
#: ㄴ(4) 관형형 현재 `게으른` · ㄹ(8) 관형형 미래 `게으를` · ㅁ(16) 명사형 `게으름` ·
#: ㅂ(17) **하십시오체 `산만합니다`** · ㅆ(20) 과거 `산만했다`(불규칙 어간에 결합).
#:
#: 🔴 ㅂ이 실무상 가장 중요하다 — 상담·브리핑 문면은 전부 하십시오체라 `산만합니다`가
#: 지배적 형태인데 종전에는 이게 통째로 미탐이었다(`산만하` ⊄ `산만합니다`).
#: 조합 결과가 한국어가 아니면(`꼴찐`·`문제앐`) 어디에도 나타나지 않으므로 무해하다 —
#: 형태소 분석 없이 기계적으로 만들어도 오탐이 생기지 않는 이유다.
_COMBINING_FINALS: Final = (4, 8, 16, 17, 20)


def _lacks_final(syllable: str) -> bool:
    """한글 음절이고 종성이 없으면 True — 종성이 이미 있으면 결합이 성립하지 않는다."""
    code = ord(syllable)
    if not (_HANGUL_BASE <= code <= _HANGUL_LAST):
        return False
    return (code - _HANGUL_BASE) % _JONGSEONG_COUNT == 0


def _conjugated_variants(stem: str) -> tuple[str, ...]:
    """어간 끝 음절에 종성을 결합한 형태들. 종성이 이미 있으면 빈 튜플."""
    if not stem or not _lacks_final(stem[-1]):
        return ()
    head, last = stem[:-1], ord(stem[-1])
    return tuple(head + chr(last + final) for final in _COMBINING_FINALS)


def _contains(text: str, stem: str) -> bool:
    """어간 또는 그 **종성 결합형**이 본문에 있는가.

    🔴 **접두 매칭은 하지 않는다.** `문제아`를 `문제`로 줄여 찾으면 "문제 풀이 시간"·
    "문제를 새로 시작"이 걸린다 — 8/4 실서버 코퍼스 26건 중 7건이 그렇게 오탐했다.
    어간 끝 음절을 **버리지 않고 종성만 더한다**(`문제아` → `문제안`)는 것이 차이다.
    """
    return stem in text or any(v in text for v in _conjugated_variants(stem))


def find_forbidden(text: str, forbidden: tuple[str, ...]) -> tuple[str, ...]:
    """본문에서 걸린 A군 어간을 **등록 순서대로** 돌려준다 — 순수 함수(결정론).

    **A군 판정의 단일 정본이다**(8/5 · 99 D ⑰ 해소). 브리핑 게이트도 자기 루프를 버리고
    이 함수를 부른다 — 어휘 단일 참조(99 #15 ⓒ)에 이어 **판정도 단일 참조**다.
    빈 결과가 통과를 뜻한다.

    **잡는 것 2종:** ⓐ 어간 그대로(`게으르다`) ⓑ 어간 끝 음절에 **종성이 결합**한 활용형
    (`게으른`·`게으름`·`산만합니다`). 어간 자체가 변형되는 불규칙(`게을러`·`산만해`)은
    코드로 만들 수 없어 **yaml에 어간으로 등재**했고, 그것들도 ⓑ를 함께 탄다
    (`산만해` + ㅆ → `산만했습니다`).
    """
    return tuple(stem for stem in forbidden if _contains(text, stem))


def parse_buffer_lexicon(raw: dict[str, Any]) -> BufferLexicon:
    """원시 dict → `BufferLexicon`. 검증 실패는 `BufferLexiconError`(파일 접근 없음)."""
    for section in ("forbidden", "replace"):
        if section not in raw:
            raise BufferLexiconError(f"buffer_lexicon.yaml에 {section} 섹션이 없다")

    forbidden = tuple(str(word) for word in raw["forbidden"])
    if len(set(forbidden)) != len(forbidden):
        raise BufferLexiconError("forbidden에 중복 어간이 있다")
    if any(not word for word in forbidden):
        raise BufferLexiconError("forbidden에 빈 어간이 있다")

    replacements: list[Replacement] = []
    for item in raw["replace"]:
        source = str(item["from"])
        if not source:
            raise BufferLexiconError("replace의 from이 비었다")
        replacements.append(Replacement(source=source, target=str(item.get("to", ""))))

    total = len(forbidden) + len(replacements)
    if total != EXPECTED_TERM_COUNT:
        raise BufferLexiconError(
            f"어휘 수가 {total}항이다 — 05 §4는 {EXPECTED_TERM_COUNT}항이다"
        )
    return BufferLexicon(
        version=str(raw["version"]),
        forbidden=forbidden,
        replacements=tuple(replacements),
    )


@lru_cache
def load_buffer_lexicon() -> BufferLexicon:
    """`buffer_lexicon.yaml`을 읽어 검증된 사전을 돌려준다(프로세스 1회 파싱)."""
    raw = yaml.safe_load(_LEXICON_PATH.read_text(encoding="utf-8"))
    return parse_buffer_lexicon(raw)


def forbidden_terms() -> tuple[str, ...]:
    """A군 어간 — 브리핑 게이트 등 소비자의 단일 참조점(99 #15 ⓒ)."""
    return load_buffer_lexicon().forbidden


__all__ = [
    "EXPECTED_TERM_COUNT",
    "BufferLexicon",
    "BufferLexiconError",
    "Replacement",
    "find_forbidden",
    "forbidden_terms",
    "load_buffer_lexicon",
    "parse_buffer_lexicon",
]
