"""🔴 인명 후보 패턴의 **정탐**을 기계로 만든 코퍼스로 못 박는다 (99 #123).

⚠ **왜 새로 세우나** — `test_redaction_coverage.py` 의 이름 매트릭스는 **손으로 고른**
이름 몇 개 × 뒤따르는 말 몇 개다(≥24칸). 그건 «우리가 아는 정탐»만 잰다.
#123 의 처방 후보(패턴 좁히기·사전 배제·문맥 가중)는 **전부 정탐을 깎을 수 있는** 형태라,
고치기 전에 **「무엇이 안 깎여야 하는가」를 기계로 정의**해 둔다.

🔴 **이 파일은 오탐을 재지 않는다.** 오탐(#123)은 이 회차에서 **안 고쳤고**, 그 규모는
실측으로 99 에 적혀 있다(docs 산문 1200문장: 문면 훼손 7.9% · 문장 차단 0.2%).
여기서 오탐 기대값을 못 박으면 **처방이 오탐을 줄일 때 red 가 된다** — 그건 거꾸로다.

⚠ 코퍼스는 **설정에서 만든다**(성씨·조사 목록을 다시 타이핑하지 않는다) — 목록이 늘면
검사도 자동으로 넓어진다.
"""

from __future__ import annotations

import itertools
import pathlib
from typing import Final

import pytest
import yaml  # type: ignore[import-untyped]

from ai.runtime.redaction import redact

_PATTERNS: Final = pathlib.Path("src/ai/runtime/redaction_patterns.yaml")

#: 흔한 2음절 이름 — 🔴 **성씨·조사는 설정에서 온다**(여기 리터럴은 이름뿐이다).
_GIVEN_NAMES: Final = (
    "서연", "민준", "도윤", "지우", "하은",
    "시우", "예준", "수아", "지호", "윤서",
)

#: 학부모 문의처럼 보이는 운반 문장 — 이름 자리만 갈아 끼운다.
_CARRIER: Final = "선생님 안녕하세요. {} 요즘 어떤지 여쭙고 싶습니다."


@pytest.fixture(scope="module")
def config() -> dict[str, object]:
    loaded: dict[str, object] = yaml.safe_load(_PATTERNS.read_text(encoding="utf-8"))
    return loaded


def _corpus(config: dict[str, object]) -> list[tuple[str, str]]:
    surnames = config["scoring"]["surnames"]  # type: ignore[index]
    particles = config["particles"]["korean"]  # type: ignore[index]
    return [
        (f"{sur}{given}", particle)
        for sur, given, particle in itertools.product(surnames, _GIVEN_NAMES, particles)
    ]


def test_every_generated_name_is_masked(config: dict[str, object]) -> None:
    """🔴 **성씨 × 이름 × 조사 전수에서 실명이 원문으로 남지 않는다** (불변식 3).

    실측(2026-08-20 · 6,000조합): 미탐 **0**. 이 값이 기준선이고, #123 을 고칠 때
    **하나라도 늘면 오탐을 줄이려다 미탐을 만든 것**이다 — 그때는 즉시 중단이다.
    """
    leaked = [
        (name, particle)
        for name, particle in _corpus(config)
        if name in redact(_CARRIER.format(f"{name}{particle}")).masked_text
    ]
    assert not leaked, (
        f"실명이 마스킹을 통과했다: {leaked[:10]} (총 {len(leaked)}건) — "
        "오탐을 줄이려다 미탐을 만들었다면 불변식 3 위반이다"
    )


def test_the_corpus_is_not_truncated(config: dict[str, object]) -> None:
    """🔴 **재는 칸이 실제로 넓은가** — 축이 비면 위 단언이 공허하게 통과한다.

    ⚠ 이 저장소가 반복해서 밟은 형태다(#108·#123 의 「대조군이 좁았다」). 상한을
    「몇 개가 존재하나」가 아니라 **「몇 개를 실제로 훑었나」**로 센다.
    """
    corpus = _corpus(config)
    assert len(corpus) >= 4_000, f"코퍼스가 {len(corpus)}조합뿐이다"
    assert len({name for name, _ in corpus}) >= 300
    assert len({particle for _, particle in corpus}) >= 10
