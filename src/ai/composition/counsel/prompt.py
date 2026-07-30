"""상담 초안 프롬프트 조립 — `tone_map.yaml` 조회 결과를 문면에 반영한다.

정본: `docs/part_a/05_tone_mapping.md` §1·§2 · 템플릿
`llm/prompts/templates/composition/counsel_pack.txt`.
같은 패키지의 `briefing.py` 선례를 따른다(템플릿 파일 직접 읽기 + PROMPT_ID·PROMPT_VERSION).

**값을 코드에 박지 않는다** — 블록 순서·문장 수·완충 단계·축별 규칙은 전부 tone_map에서 읽는다.
조합을 바꾸면 프롬프트가 달라져야 하고, 그 회귀는 24조합 스냅숏 골든(08 §3)이 고정한다.
조립 결과는 **LLM 전송 전 redaction을 거쳐야 한다**(호출자 책임 — `provider.py` 참조).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final

from ai.composition.tone import ToneRule, combination_key, load_tone_map
from ai.contracts.composition import DraftContext

_PROMPT_PATH: Final = (
    Path(__file__).resolve().parents[2]
    / "llm"
    / "prompts"
    / "templates"
    / "composition"
    / "counsel_pack.txt"
)

PROMPT_ID: Final = "composition/counsel_pack"
PROMPT_VERSION: Final = "0.1"

#: 완충 단계(0~2) 문면 — 05 §4 "적용 단계". 값은 tone_map의 buffer_level 이 고른다.
_BUFFER_TEXT: Final = {
    0: "완충 0 — 금칙어(A군)만 피하고 사실을 그대로 전하세요.",
    1: "완충 1 — 금칙어를 피하고 단정 표현을 관찰·상태 서술로 바꾸세요.",
    2: (
        "완충 2 — 금칙어를 피하고 단정 표현을 관찰·상태 서술로 바꾸며, "
        "부정적인 내용은 반드시 대응 계획과 같은 문장 안에 두고 뒤쪽에 배치하세요."
    ),
}


@lru_cache
def _template() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def render_evidence_block(context: DraftContext) -> str:
    """근거 데이터 블록 — 제공된 수치만 나열한다(게이트 허용집합과 같은 출처).

    근거가 없으면 그 사실을 명시한다 — 빈 블록으로 두면 LLM이 사실을 지어낸다.
    """
    lines = [f"- {fact.label}: {fact.value}" for fact in context.facts]
    lines += [f"- {summary}" for summary in context.evidence_summaries]
    return "\n".join(lines) if lines else "- (제공된 수치 없음 — 숫자를 쓰지 마세요)"


def render_tone_rules(rule: ToneRule, context: DraftContext) -> str:
    """축별 1차 규칙(05 §1) + 완충 단계(05 §4)를 문면으로."""
    axis_rules = load_tone_map().axis_rules
    axes = context.label_snapshot.as_axes()
    lines = [f"- {axis_rules[axis][value]}" for axis, value in axes.items()]
    lines.append(f"- {_BUFFER_TEXT[rule.buffer_level]}")
    if rule.note:
        lines.append(f"- {rule.note}")
    return "\n".join(lines)


def render_block_plan(rule: ToneRule) -> str:
    """블록 순서(05 §2 '구성') — tone_map의 blocks 리스트를 번호 목록으로."""
    return "\n".join(f"{i}. {block}" for i, block in enumerate(rule.blocks, start=1))


def tone_rule_for(context: DraftContext) -> ToneRule:
    """label_snapshot 4축으로 tone_map 규칙을 조회한다(변환 코드 없음)."""
    return load_tone_map().combinations[combination_key(**context.label_snapshot.as_axes())]


def assemble_prompt(context: DraftContext) -> str:
    """조합별 상담 초안 프롬프트 — 결정론(같은 컨텍스트 → 같은 문자열).

    LLM을 호출하지 않는다. 24조합 스냅숏 골든이 이 함수의 출력을 고정한다.
    """
    rule = tone_rule_for(context)
    return _template().format(
        period_label=context.period_label,
        block_plan=render_block_plan(rule),
        sentences_per_block=rule.sentences_per_block,
        tone_key=combination_key(**context.label_snapshot.as_axes()),
        tone_rules=render_tone_rules(rule, context),
        evidence_block=render_evidence_block(context),
    )


__all__ = [
    "PROMPT_ID",
    "PROMPT_VERSION",
    "assemble_prompt",
    "render_block_plan",
    "render_evidence_block",
    "render_tone_rules",
    "tone_rule_for",
]
