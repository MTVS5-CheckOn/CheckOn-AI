"""라벨 제안 프롬프트 조립 — 순수 함수(계산과 I/O 분리 · 03 §2).

`composition/counsel/prompt.py` 선례를 따른다 — **템플릿 파일 직접 읽기 + `PROMPT_ID`·
`PROMPT_VERSION`**. ⚠ `llm/prompts/registry.yaml` 은 B 소유이고 counsel·detect 도 거기
없다(CLAUDE.md §3) — 같은 축이다.

🔴 **`redact()` 는 조립부(`provider.py`)가 부른다 — 여기가 아니다.** 이 모듈은 **순수
함수**라 문면만 만들고, 마스킹 판정은 **전송을 아는 층**의 일이다.
⚠ 🔴 **(8/22 정정) 종전 이 문단은 «여기서도 트립와이어에서도 부르지 않는다» 는 뜻으로
읽혔고 그건 틀렸다** — 저장소 규율은 **호출부가 전송 전에 선검사(fail-closed)** 하고
트립와이어는 **마지막 관문**이다(`classify/classifier.py` 가 같은 형태). 계약 검사
`test_composition_redaction.py` 가 그 규율을 물어 이 결함을 잡았다.
🔴 그리고 선검사는 **`findings` 도 본다** — `uncertain` 만 보면 «조립은 통과인데 전송이
죽는다» 가 된다(99 #83 실측 · 트립와이어는 `findings or uncertain`).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from string import Template
from typing import Final

from ai.contracts.labels import HistoryItem

_PROMPT_PATH: Final = (
    Path(__file__).resolve().parents[2]
    / "llm"
    / "prompts"
    / "templates"
    / "labels"
    / "suggest.txt"
)

PROMPT_ID: Final = "composition/labels_suggest"
PROMPT_VERSION: Final = "0.1"
"""🔴 **최초(2026-08-22 · 99 #191).**

⚠ 템플릿 **파일이 바뀌면** 올린다. 컨텍스트 파생 문면(이력 블록)만 달라지는 것은
버전과 무관하다(05 §6-3 · counsel 선례).
"""


def render_history_block(history: Sequence[HistoryItem]) -> str:
    """이력을 프롬프트 블록으로 — 🔴 `record_id` 를 **같은 줄에** 둔다.

    ⚠ 모델이 인용에 `record_id` 를 붙이려면 **어느 문장이 어느 id 인지**가 한눈에 보여야
    한다. 줄을 나누면 붙는 확률이 떨어진다(counsel plan 이 같은 형태를 쓴다).
    ⚠ 🔴 **`at`·`direction` 은 싣지 않는다** — 축 판정에 안 쓰이고, 시각은 개인 식별을
    좁히는 축이다(불변식 3 의 방향). `frequency` 는 **이력 건수**로 읽는다.
    """
    return "\n".join(
        f"- [{item.record_id}] {item.text}" for item in history
    )


def assemble_prompt(history: Sequence[HistoryItem]) -> str:
    """템플릿 + 이력 블록 — 결정론(같은 입력 → 같은 문자열)."""
    template = Template(_PROMPT_PATH.read_text(encoding="utf-8").strip())
    return template.safe_substitute(history_block=render_history_block(history))


__all__ = ["PROMPT_ID", "PROMPT_VERSION", "assemble_prompt", "render_history_block"]
