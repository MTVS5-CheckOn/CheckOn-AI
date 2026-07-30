"""완충 사전 로더 — `buffer_lexicon.yaml`의 금칙(A군)·치환(B군)을 읽는다.

정본: `docs/part_a/05_tone_mapping.md` §4. 어휘는 전부 yaml이 원본이며 이 모듈은 읽기·검출만
한다(코드에 어휘 하드코딩 금지 — 03_coding_rules §1). 로더 패턴은 `runtime/redaction.py`를
따른다.

**⚠ 문서-구현 불일치(99 D ⑰):** 05 §5는 A군 검출을 **형태소 접두 매칭(활용형 대응)**으로
규정하지만, 현 구현은 **부분 문자열 포함**이다 — 프로덕션 중인 브리핑 게이트의 동작을
그대로 승계하기 위해서다(`briefing_gate.py`의 기존 `word in text`와 판정 결과 100% 동일).
따라서 어간이 변형되는 활용형은 미탐이다: `게으르`는 "게으르다"·"게으른"을 잡지만
"게을러서"(르 불규칙)는 못 잡고, `산만하`는 "산만해서"·"산만한"을 못 잡는다.
미탐 사례는 `tests/.../test_buffer_lexicon.py`에 **xfail로 남겨** 알고리즘을 바꾸면
xpass로 자동 드러나게 했다. 사전 보강 vs 코드 정규화는 A가 별도 판단한다(99 D ⑰).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

import yaml  # type: ignore[import-untyped]

_LEXICON_PATH: Final = Path(__file__).parent / "buffer_lexicon.yaml"

#: 05 §4 표제 "금칙·치환 50항" — 사전이 문서와 어긋나면 로드가 실패한다(정합 가드).
EXPECTED_TERM_COUNT: Final = 50


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


def find_forbidden(text: str, forbidden: tuple[str, ...]) -> tuple[str, ...]:
    """본문에서 걸린 A군 어간을 **등록 순서대로** 돌려준다 — 순수 함수(결정론).

    판정은 `briefing_gate`의 기존 검출(`word in text`)과 **완전히 동일**하다 — 이 PR은
    목록을 단일화할 뿐 게이트 동작을 바꾸지 않는다(99 #15 ⓒ). 어간이 유지되는 활용형은
    함께 걸리지만(`게으르` → "게으르다"·"게으른"), 어간이 변형되면 미탐이다
    ("게을러서"·"산만해서" — 99 D ⑰). 빈 결과가 통과를 뜻한다.
    """
    return tuple(stem for stem in forbidden if stem in text)


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
