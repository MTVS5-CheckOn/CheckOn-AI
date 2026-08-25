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
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Final
from zoneinfo import ZoneInfo

from ai.contracts.labels import HistoryItem

#: 🔴 naive `at` 을 읽을 시간대 — 저장소가 이미 이걸 쓴다(`label_confirmation_store`
#: 의 주간 버킷 · `evaluation/fake_snapshot.KST`). 새 기준을 안 만든다.
_SEOUL: Final = ZoneInfo("Asia/Seoul")


def _order_key(item: HistoryItem) -> tuple[datetime, str]:
    """정렬 키 — `at` 오름차순, 같으면 `record_id`.

    🔴 **보조 키가 있어야 한다** — `at` 이 같은 두 건에서 순서가 다시 흔들린다
    (BE 가 초 단위로 내려주면 같은 시각이 흔하다).
    ⚠ 🔴 `HistoryItem.at` 은 **naive 도 aware 도 받는다**(계약이 안 막는다 · 실측).
    섞이면 `sorted` 가 `TypeError: can't compare offset-naive and offset-aware` 로
    죽으므로 여기서 흡수한다 — 🔴 **계약에 aware 를 강제하지 않는다**(BE 부담 · №109
    중단규칙 6: 우리가 정렬하기로 한 것이 그 부담을 없앤 것이다).
    🔴 naive 는 `Asia/Seoul` 로 읽는다 — 한국 학원 운영 시각이고 저장소 선례와 같다.
    ⚠ 🔴 이 값은 **정렬에만** 쓰인다 — 프롬프트에는 안 실린다(`render_history_block`).
    """
    at = item.at if item.at.tzinfo is not None else item.at.replace(tzinfo=_SEOUL)
    return (at, item.record_id)

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

    🔴 **`at` 오름차순으로 정렬한다**(99 #253 · 2026-08-25) — 받은 순서 그대로 넣으면
    같은 10건이라도 순서가 다르면 프롬프트가 다르고 제안이 갈린다. ⇒ 🔴 **재현성이
    BE 의 정렬 구현에 걸린다**(불변식 8). 승우님이 «10건 초과면 최신 10건» 이라
    하셨는데 🔴 **그 10건의 내부 순서는 안 정해졌다.** 선례와 같은 이유·같은 처방이다
    (`counsel/worker.py` 의 `student_refs=sorted(...)  # 처리 순서 고정(재현성)`).
    ⚠ 🔴 **여기가 그 한 곳이다** — 순서가 **바이트가 되는** 자리라, 실 provider 도
    대역도 이 함수를 지난다(전수: labels 의 `assemble_prompt` 호출부는 그 둘뿐).

    🔴 **왜 오름차순인가**(판단이라 이유를 적는다) — ① `at` 을 안 싣기 때문에
    **순서가 시간 정보의 유일한 전달 수단**이고, 「위에서 아래로 = 과거에서 현재」가
    사람이 이력을 읽는 관습이다. ② 모델은 프롬프트 **뒤쪽**을 더 무겁게 다루는
    경향이 있어 최신이 뒤에 오는 편이 낫다. 🔴 어느 쪽이든 결정론은 같으므로
    **바꾸려면 이 두 줄을 반박하면 된다**.

    ⚠ 모델이 인용에 `record_id` 를 붙이려면 **어느 문장이 어느 id 인지**가 한눈에 보여야
    한다. 줄을 나누면 붙는 확률이 떨어진다(counsel plan 이 같은 형태를 쓴다).
    ⚠ 🔴 **`at`·`direction` 은 싣지 않는다** — 축 판정에 안 쓰이고, 시각은 개인 식별을
    좁히는 축이다(불변식 3 의 방향). `frequency` 는 **이력 건수**로 읽는다.
    """
    return "\n".join(
        f"- [{item.record_id}] {item.text}"
        for item in sorted(history, key=_order_key)
    )


def assemble_prompt(history: Sequence[HistoryItem]) -> str:
    """템플릿 + 이력 블록 — 결정론.

    🔴 **같은 10건이면 「받은 순서와 무관하게」 같은 문자열**이다(#253) —
    `render_history_block` 이 `at` 으로 정렬한다.
    """
    template = Template(_PROMPT_PATH.read_text(encoding="utf-8").strip())
    return template.safe_substitute(history_block=render_history_block(history))


__all__ = ["PROMPT_ID", "PROMPT_VERSION", "assemble_prompt", "render_history_block"]
