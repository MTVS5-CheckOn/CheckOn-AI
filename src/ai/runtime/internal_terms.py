"""내부 용어 사전 — 학부모 문면에 나오면 안 되는 구(句).

정본은 `internal_terms.yaml`이다(코드에 어휘를 박지 않는다 — 03 §1).
`buffer_lexicon.py`와 같은 규약이며, 판정도 여기 한 곳이 한다.

⚠ **`composition/`에서 `runtime/`으로 옮겼다(8/7 · B 권고).** 성격이 옆에 있는
`redaction_patterns.yaml`과 같다 — **경계 밖으로 나가는 텍스트를 어휘로 검사**하는 사전이지
상담 도메인 로직이 아니다. 소비처는 지금 `composition/counsel/gate.py` 하나지만, 같은 검사가
필요한 다음 경계(리포트·문항 해설)가 `composition/` 밑을 import하게 만들 이유가 없다.
🔴 **이동뿐이다** — 어휘 1건, 로더 규약 1줄도 바뀌지 않았다(게이트 동작 무변화가 머지 조건).

🔴 **활용형을 만들지 않는다** — `buffer_lexicon`의 A군은 용언 어간이라 종성 결합이
필요했지만, 여기 등재하는 건 **명사구·지시문 조각**이라 그대로 나온다. 활용을 붙이면
오탐만 는다("초안을 작성" + 종성 → 무의미).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Final

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict

_PATH: Final = Path(__file__).resolve().parent / "internal_terms.yaml"


class InternalTermsError(ValueError):
    """사전 파일이 규약을 어겼다 — 기동 시점에 터진다(조용한 통과 금지)."""


class InternalTerms(BaseModel):
    """파싱된 사전 — 순서가 곧 검출 우선순위다(결정론)."""

    model_config = ConfigDict(frozen=True)

    version: str
    terms: tuple[str, ...]


def parse_internal_terms(raw: dict[str, Any]) -> InternalTerms:
    """원시 dict → `InternalTerms`. 파일 접근 없음(순수 함수)."""
    if "internal_terms" not in raw:
        raise InternalTermsError("internal_terms.yaml에 internal_terms 섹션이 없다")
    terms = raw["internal_terms"]
    if not isinstance(terms, list) or not terms:
        raise InternalTermsError("internal_terms는 비어 있지 않은 목록이어야 한다")
    cleaned: list[str] = []
    for term in terms:
        if not isinstance(term, str) or not term.strip():
            raise InternalTermsError(f"내부 용어가 비어 있거나 문자열이 아니다: {term!r}")
        # 🔴 단어 하나는 등재하지 않는다 — '상담'을 잡으면 "상담을 드립니다"가 막힌다.
        if " " not in term.strip() and len(term.strip()) <= 3:
            raise InternalTermsError(
                f"짧은 단일 단어는 오탐이 크다 — 구로 등재하라: {term!r}"
            )
        cleaned.append(term.strip())
    return InternalTerms(version=str(raw.get("version", "0")), terms=tuple(cleaned))


@lru_cache
def load_internal_terms() -> InternalTerms:
    return parse_internal_terms(yaml.safe_load(_PATH.read_text(encoding="utf-8")))


def internal_terms() -> tuple[str, ...]:
    """검사 대상 구 목록 — 게이트가 부르는 유일한 진입점."""
    return load_internal_terms().terms


def find_internal_terms(text: str, terms: tuple[str, ...] | None = None) -> tuple[str, ...]:
    """본문에서 걸린 내부 용어를 **등록 순서대로** 돌려준다 — 순수 함수(결정론).

    빈 결과가 통과를 뜻한다. 부분 문자열 검사이며 활용·접두 확장을 하지 않는다.
    """
    active = internal_terms() if terms is None else terms
    return tuple(term for term in active if term in text)


__all__ = [
    "InternalTerms",
    "InternalTermsError",
    "find_internal_terms",
    "internal_terms",
    "load_internal_terms",
    "parse_internal_terms",
]
