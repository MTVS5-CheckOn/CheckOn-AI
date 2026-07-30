"""톤 매핑 로더 — `tone_map.yaml`을 읽어 조합별 톤 규칙을 돌려준다.

정본: `docs/part_a/05_tone_mapping.md` §1·§2·§3. 규칙 값은 전부 yaml이 원본이며 이 모듈은
읽기·검증만 한다(코드에 톤 규칙 하드코딩 금지 — 03_coding_rules §1).
로더 패턴은 `runtime/redaction.py`(`redaction_patterns.yaml`)를 그대로 따른다.

**fail-closed:** 조합 어휘(§1 `axis_rules`)의 데카르트곱과 `combinations` 키 집합이 정확히
일치하지 않으면 로드 시점에 실패한다(05 §5 "24键 전부 존재하는지 로드 시 검증").
축 어휘를 코드에 박지 않고 yaml에서 읽으므로 정본이 한 곳에 유지된다.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

import yaml  # type: ignore[import-untyped]

_TONE_MAP_PATH: Final = Path(__file__).parent / "tone_map.yaml"

#: 05 §2 표의 조합 수 — 축 어휘가 바뀌면 이 상수와 어긋나 로드가 실패한다(문서 정합 가드).
EXPECTED_COMBINATION_COUNT: Final = 24

#: 조합 키 축 순서 — 05 §5 규정 `comm.sens.interest.freq`. yaml `axis_rules`의 축 이름과
#: 대응한다(`sens`는 키 표기, `sensitivity`는 축 이름 — 05 §2 표 머리와 동일).
_AXIS_ORDER: Final = ("comm", "sensitivity", "interest", "frequency")


class ToneMapError(ValueError):
    """톤 맵 데이터가 계약을 위반했다 — 기동 실패(fail-closed)."""


@dataclass(frozen=True)
class ToneRule:
    """조합 1건의 톤 규칙 — 05 §2 한 행."""

    blocks: tuple[str, ...]
    """블록 순서(§2 '구성'). 프롬프트가 이 순서대로 조립한다."""

    sentences_per_block: int
    """블록당 문장 수(§2 '길이')."""

    buffer_level: int
    """§4 완충 사전 적용 단계 — 0=A군만 · 1=A군+B군 기본 · 2=전체+부정문 후치."""

    note: str | None = None
    """§2 '비고' — 없는 행은 None."""


@dataclass(frozen=True)
class ToneMap:
    """`tone_map.yaml` 전체 — 순수 데이터."""

    version: str
    axis_rules: dict[str, dict[str, str]]
    combinations: dict[str, ToneRule]
    percentile_softening: dict[str, Any]

    def rule(self, comm: str, sensitivity: str, interest: str, frequency: str) -> ToneRule:
        """조합 키로 규칙을 찾는다. 없는 조합은 `ToneMapError`(조용한 폴백 금지)."""
        key = combination_key(comm, sensitivity, interest, frequency)
        try:
            return self.combinations[key]
        except KeyError as exc:
            raise ToneMapError(f"등록되지 않은 톤 조합: {key}") from exc


def combination_key(comm: str, sensitivity: str, interest: str, frequency: str) -> str:
    """05 §5 규정 조합 키 — `comm.sens.interest.freq`."""
    return f"{comm}.{sensitivity}.{interest}.{frequency}"


def expected_keys(axis_rules: dict[str, dict[str, str]]) -> tuple[str, ...]:
    """축 어휘의 데카르트곱 — 순수 함수(계산·I/O 분리, 03 §2)."""
    try:
        vocab = [tuple(axis_rules[axis]) for axis in _AXIS_ORDER]
    except KeyError as exc:
        raise ToneMapError(f"axis_rules에 축이 없다: {exc.args[0]}") from exc
    return tuple(".".join(combo) for combo in itertools.product(*vocab))


def parse_tone_map(raw: dict[str, Any]) -> ToneMap:
    """원시 dict → `ToneMap`. 검증 실패는 전부 `ToneMapError`(순수 함수 — 파일 접근 없음)."""
    for section in ("axis_rules", "combinations", "percentile_softening"):
        if section not in raw:
            raise ToneMapError(f"tone_map.yaml에 {section} 섹션이 없다")

    axis_rules = {
        str(axis): {str(k): str(v) for k, v in values.items()}
        for axis, values in raw["axis_rules"].items()
    }
    expected = expected_keys(axis_rules)
    if len(expected) != EXPECTED_COMBINATION_COUNT:
        raise ToneMapError(
            f"축 어휘의 조합 수가 {len(expected)}이다 — 05 §2는 "
            f"{EXPECTED_COMBINATION_COUNT}조합이다"
        )

    found = set(raw["combinations"])
    missing = sorted(set(expected) - found)
    unknown = sorted(found - set(expected))
    if missing or unknown:
        raise ToneMapError(
            f"combinations가 24조합과 일치하지 않는다 — 누락={missing} 미등록={unknown}"
        )

    combinations: dict[str, ToneRule] = {}
    for key in expected:  # 정렬 순서를 데카르트곱으로 고정 — 결정론
        item = raw["combinations"][key]
        blocks = tuple(str(block) for block in item["blocks"])
        if not blocks:
            raise ToneMapError(f"{key}: blocks가 비었다")
        note = item.get("note")
        combinations[key] = ToneRule(
            blocks=blocks,
            sentences_per_block=int(item["sentences_per_block"]),
            buffer_level=int(item["buffer_level"]),
            note=None if note is None else str(note),
        )

    return ToneMap(
        version=str(raw["version"]),
        axis_rules=axis_rules,
        combinations=combinations,
        percentile_softening=dict(raw["percentile_softening"]),
    )


@lru_cache
def load_tone_map() -> ToneMap:
    """`tone_map.yaml`을 읽어 검증된 `ToneMap`을 돌려준다(프로세스 1회 파싱)."""
    raw = yaml.safe_load(_TONE_MAP_PATH.read_text(encoding="utf-8"))
    return parse_tone_map(raw)


__all__ = [
    "EXPECTED_COMBINATION_COUNT",
    "ToneMap",
    "ToneMapError",
    "ToneRule",
    "combination_key",
    "expected_keys",
    "load_tone_map",
    "parse_tone_map",
]
