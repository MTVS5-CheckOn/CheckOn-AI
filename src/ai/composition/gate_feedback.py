"""게이트 사유 → 다음 시도 수정 지시 문구 로더 — 05 §6-2.

재생성 루프(counsel 그래프·브리핑)가 직전 게이트 사유를 이 모듈로 문구화해 다음 시도
프롬프트에 넣는다. 문구 원본은 `gate_feedback.yaml`이고 이 모듈은 **읽기·조회만** 한다
(문구 하드코딩 금지 — 03_coding_rules §1). 매핑은 결정론이며 LLM을 쓰지 않는다(불변식 1).

로더 패턴은 같은 패키지의 `tone.py`(`tone_map.yaml`)를 그대로 따른다.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final

import yaml  # type: ignore[import-untyped]

_PATH: Final = Path(__file__).parent / "gate_feedback.yaml"

#: `{detail}` 자리표가 없는 꼬리말은 가변부를 삼켜 버린다 — 데이터 실수를 기동에서 잡는다.
_DETAIL_SLOT: Final = "{detail}"


class GateFeedbackError(ValueError):
    """수정 지시 데이터가 계약을 위반했다 — 기동 실패(fail-closed)."""


@dataclass(frozen=True)
class GateFeedbackMap:
    """`gate_feedback.yaml` 전체 — 순수 데이터."""

    version: str
    instructions: dict[str, str]
    default: str
    detail_suffix: str


@lru_cache
def load_gate_feedback() -> GateFeedbackMap:
    """yaml을 읽어 검증한다. 캐시는 프로세스 수명 — 파일은 배포 단위로 고정이다."""
    raw = yaml.safe_load(_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise GateFeedbackError("gate_feedback.yaml이 매핑이 아니다")
    instructions = raw.get("instructions")
    if not isinstance(instructions, dict) or not instructions:
        raise GateFeedbackError("gate_feedback.yaml에 instructions 섹션이 없다")
    default = raw.get("default")
    suffix = raw.get("detail_suffix")
    if not isinstance(default, str) or not default.strip():
        raise GateFeedbackError("gate_feedback.yaml의 default가 비었다")
    if not isinstance(suffix, str) or _DETAIL_SLOT not in suffix:
        raise GateFeedbackError("detail_suffix에 {detail} 자리표가 없다")
    for code, text in instructions.items():
        if not isinstance(text, str) or not text.strip():
            raise GateFeedbackError(f"instructions.{code} 문구가 비었다")
    return GateFeedbackMap(
        version=str(raw.get("version", "")),
        instructions=dict(instructions),
        default=default,
        detail_suffix=suffix,
    )


def instruction_for(reason: str) -> str:
    """게이트 사유 코드를 수정 지시 문구로 바꾼다. 순수 함수(같은 사유 → 같은 문구).

    빈 사유는 **빈 문자열**을 돌려준다 — 1회차에는 지시가 붙지 않아 프롬프트가 종전과
    바이트 동일해야 하기 때문이다(05 §6-2 · 24조합 골든 보호).

    사유는 `접두` 또는 `접두:가변부` 형태다(`ungrounded_number:83`). 접두로 문구를 찾고
    가변부는 꼬리말로 덧붙인다 — 코드마다 가변부 유무가 달라 문구를 쪼개지 않는다.
    """
    if not reason:
        return ""
    table = load_gate_feedback()
    prefix, _, detail = reason.partition(":")
    text = table.instructions.get(prefix, table.default)
    if not detail:
        return text
    return text + table.detail_suffix.format(detail=detail)


def render_feedback_block(instruction: str) -> str:
    """수정 지시를 프롬프트 문면으로 — **빈 경우 빈 문자열**(바이트 동일 보장).

    counsel·briefing 두 경로가 **같은 렌더러**를 쓴다. 문면을 각자 두면 두 곳에서 따로
    늙는다(`EvidenceFact` 이중 정의 · 99 D ㉚와 같은 패턴). 호출자는 이 블록을 **근거
    블록 뒤·완성 신호 앞**에 붙인다 — 두 템플릿 모두 `{evidence_block}` 뒤에 완성 신호가
    온다(`counsel_pack.txt` → "상담 초안:" · `briefing.txt` → "한 문장:").
    """
    if not instruction:
        return ""
    return f"\n\n직전 시도 수정 지시(반드시 반영):\n- {instruction}"


__all__ = [
    "GateFeedbackError",
    "GateFeedbackMap",
    "instruction_for",
    "load_gate_feedback",
    "render_feedback_block",
]
