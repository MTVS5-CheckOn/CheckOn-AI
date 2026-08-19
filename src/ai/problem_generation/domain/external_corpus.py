"""R-8 외부 대조 코퍼스의 순수 지문 색인과 포함도 계산 — LLM·I/O 없음.

🔴 **왜 R-6의 3-gram Jaccard를 그대로 쓰지 않나.** R-6은 **발문**을 본다. 발문은 짧고
같은 세트 안에서만 비교하므로 Jaccard가 맞다. R-8이 보는 것은 **지문**이고(05 §8.3 —
*"기출·시중 교재·타사 지문과의 유사도"*), 여기서 위험한 것은 「전체가 닮았는가」가 아니라
**「생성 지문 안에 외부 지문 조각이 그대로 들어왔는가」**다. 길이가 크게 다른 두 글에
Jaccard를 쓰면 **긴 쪽이 분모를 키워 전재를 희석한다.** 그래서 분모를 질의 쪽으로만 두는
**포함도(containment)** 를 쓴다.

⚠ **shingle 은 8자다.** 한국어에서 8자는 어절 두셋 폭이라 우연 일치가 드물고, 문장을
그대로 옮기면 반드시 걸린다. 3자로 내리면 조사·어미 때문에 무관한 글끼리도 높게 나온다.

⚠ **해시는 `zlib.crc32` 다 — `hash()` 금지.** 파이썬 내장 `hash()`는 프로세스마다 소금이
달라 **같은 입력이 실행마다 다른 표본**을 만든다(불변식 8 재현성 위반).
"""

from __future__ import annotations

import hashlib
import re
import zlib
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field

#: 지문 조각 판정 단위. 근거는 모듈 머리말.
SHINGLE_SIZE = 8
#: 표본 비율 1/4 — 색인 메모리를 4분의 1로 줄인다. 포함도는 표본 위에서 추정한다.
SAMPLE_MODULUS = 4

_NORMALIZE_PATTERN = re.compile(r"[\W_]+", flags=re.UNICODE)


def normalize(text: str) -> str:
    """비교 축을 문자열로 고정한다 — 공백·문장부호·대소문자를 지운다."""

    return _NORMALIZE_PATTERN.sub("", text).casefold()


def shingles(text: str) -> frozenset[int]:
    """정규화 본문의 표본 shingle 지문."""

    normalized = normalize(text)
    if len(normalized) < SHINGLE_SIZE:
        return frozenset()
    marks = (
        zlib.crc32(normalized[index : index + SHINGLE_SIZE].encode("utf-8"))
        for index in range(len(normalized) - SHINGLE_SIZE + 1)
    )
    return frozenset(mark for mark in marks if mark % SAMPLE_MODULUS == 0)


class ExternalMatch(BaseModel):
    """질의 본문과 가장 가까운 외부 자료."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_ref: str = Field(min_length=1)
    containment: float = Field(ge=0.0, le=1.0)


class ExternalCorpusIndex:
    """외부 대조 코퍼스의 shingle 색인. 생성 뒤 불변이며 조회는 순수 함수다."""

    def __init__(self, documents: Iterable[tuple[str, str]]) -> None:
        fingerprints: list[tuple[str, frozenset[int]]] = []
        for source_ref, text in documents:
            marks = shingles(text)
            if marks:
                fingerprints.append((source_ref, marks))
        self._fingerprints = tuple(sorted(fingerprints, key=lambda item: item[0]))
        digest = hashlib.sha256()
        for source_ref, marks in self._fingerprints:
            digest.update(f"{source_ref}:{len(marks)}\n".encode())
        self._fingerprint = f"sha256:{digest.hexdigest()}"

    @property
    def size(self) -> int:
        return len(self._fingerprints)

    @property
    def fingerprint(self) -> str:
        """AI_RUN에 적을 코퍼스 버전 — 같은 코퍼스면 같은 값이다(불변식 8)."""

        return self._fingerprint

    def closest(self, text: str) -> ExternalMatch | None:
        """질의 본문이 가장 많이 겹치는 외부 자료와 그 포함도."""

        query = shingles(text)
        if not query:
            return None
        best_ref = ""
        best_score = 0.0
        for source_ref, marks in self._fingerprints:
            overlap = len(query & marks)
            if not overlap:
                continue
            score = overlap / len(query)
            if score > best_score:
                best_score, best_ref = score, source_ref
        if not best_ref:
            return None
        return ExternalMatch(source_ref=best_ref, containment=best_score)


__all__ = [
    "SAMPLE_MODULUS",
    "SHINGLE_SIZE",
    "ExternalCorpusIndex",
    "ExternalMatch",
    "normalize",
    "shingles",
]
