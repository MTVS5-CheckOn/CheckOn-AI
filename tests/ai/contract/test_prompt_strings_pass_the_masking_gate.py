"""🔴 **프롬프트에 실리는 저장소 문자열이 마스킹 문지기를 지난다** (99 #196·#198 · №68).

⚠ 🔴 **왜 이 파일이 있나** — 우리가 쓴 문장이 우리 규칙에 걸리면, 그 축이 `uncertain` 만
보고 막을 때 **아무도 안 막히고 문면만 바뀐 채 나간다**(`redacted.masked_text` 로 전송).
실측(8/24): `briefing.txt`·`counsel_plan.txt` 가 그 상태로 **147콜을 나갔다**(#198).

🔴 **템플릿만이 아니다.** №67 이 `templates/*.txt` 열한 개를 쟀는데, 프롬프트에 실리는
저장소 문자열은 **셋 더** 있다:

    `tone_map.yaml` `axis_rules`        → `counsel/prompt.py` (🔴 4축 × 조합 · 자주 고친다)
    `gate_feedback.yaml` 지시 문구       → `briefing.py` · `counsel/graph.py`
                                          🔴 **게이트 실패 때만 실린다 ⇒ 정상 경로에 안 나온다**
    `buffer_lexicon.yaml` 완충 어휘      → `counsel/prompt.py`

⚠ 🔴 **조립해서 잰다 — yaml 전문을 넣지 않는다.** 전문을 넣으면 주석·키 이름이 걸려
**거짓 양성**이 난다. 실제로 프롬프트에 실리는 **값**만 본다.

⚠ 🔴 **`findings` 와 `uncertain` 을 둘 다 본다** — `uncertain` 만 보면 이번 결함이 안 보인다
(#198 의 넷은 전부 `findings` 만 냈다).

⚠ 🔴 **목록을 손으로 적지 않는다** — enum·키를 **순회**한다. 손으로 적으면 새 사유·새
조합이 생겨도 안 걸린다(`/v1/meta/versions` 의 labels 누락이 그 형태였다).
"""

from __future__ import annotations

import pathlib
from typing import Final

import pytest

from ai.composition.buffer_lexicon import load_buffer_lexicon
from ai.composition.gate_feedback import instruction_for, load_gate_feedback
from ai.composition.tone import load_tone_map
from ai.runtime.redaction import redact

_TEMPLATES_ROOT: Final = pathlib.Path("src/ai/llm/prompts/templates")

#: 🔴 **A 소유 템플릿 축** — `problem_generation/` 은 **B 소유**라 뺀다.
#: ⚠ 🔴 **왜 예외인가**: 그 축의 프롬프트는 B 가 소유하고 우리가 못 고친다(CLAUDE.md §2).
#: 🔴 **언제 걷나**: **준영님이 그 축을 맡는 회차** — 실측(8/24)에서 `items.txt` 하나가
#: 걸린다(«사람 이름을 쓰지 **않고 학생** A·갑·을» → `⟪이름1⟫`). **통보 대상**이다.
_FOREIGN_TEMPLATE_PREFIX: Final = "problem_generation/"


def _a_owned_templates() -> list[pathlib.Path]:
    """🔴 **디렉터리를 순회한다 — 목록을 손으로 적지 않는다.**"""
    return sorted(
        path
        for path in _TEMPLATES_ROOT.rglob("*.txt")
        if not path.relative_to(_TEMPLATES_ROOT)
        .as_posix()
        .startswith(_FOREIGN_TEMPLATE_PREFIX)
    )


def _axis_rule_strings() -> list[tuple[str, str]]:
    """`tone_map.yaml` 의 축 규칙 **값** — 키를 순회한다."""
    tone_map = load_tone_map()
    return [
        (f"tone_map.axis_rules.{axis}.{value}", text)
        for axis, values in tone_map.axis_rules.items()
        for value, text in values.items()
    ]


def _gate_feedback_strings() -> list[tuple[str, str]]:
    """게이트 사유 **전수** — 🔴 하나만 재면 나머지를 모른다."""
    feedback = load_gate_feedback()
    reasons = sorted(feedback.instructions)
    return [(f"gate_feedback.{reason}", instruction_for(reason)) for reason in reasons]


def _buffer_lexicon_strings() -> list[tuple[str, str]]:
    """완충 어휘 — A군 금칙어와 B군 치환 쌍 양쪽."""
    lexicon = load_buffer_lexicon()
    items = [(f"buffer.forbidden[{i}]", t) for i, t in enumerate(lexicon.forbidden)]
    for index, replacement in enumerate(lexicon.replacements):
        items.append((f"buffer.replacements[{index}].source", replacement.source))
        if replacement.target:
            items.append((f"buffer.replacements[{index}].target", replacement.target))
    return items


def _prompt_strings() -> list[tuple[str, str]]:
    return [*_axis_rule_strings(), *_gate_feedback_strings(), *_buffer_lexicon_strings()]


def test_the_sweeps_actually_find_something() -> None:
    """🔴 순회가 **0건이면** 아래 검사들이 조용히 통과한다 — 그 상태를 red 로 만든다.

    ⚠ 세 축을 **따로** 센다. 하나가 비어도 합계로는 안 보인다.
    """
    assert len(_a_owned_templates()) >= 4, "A 소유 템플릿 순회가 깨졌다"
    assert len(_axis_rule_strings()) >= 4, "`tone_map.axis_rules` 순회가 깨졌다"
    assert len(_gate_feedback_strings()) >= 4, "게이트 사유 순회가 깨졌다"
    assert len(_buffer_lexicon_strings()) >= 20, "완충 어휘 순회가 깨졌다"
    #: 🔴 **합친 목록도 센다** — 세 축을 따로만 세면 `_prompt_strings()` 가 비어도 안 걸린다.
    #: ⚠ 실측(8/24 뒤집기 ③): 그 함수를 `return []` 로 바꿔도 **green 이었다** —
    #: `parametrize` 가 빈 목록이면 그 검사들이 **collect 조차 안 되고 조용히 사라진다.**
    #: 🔴 «검사가 0건이 되는 것» 은 red 여야 한다 — 그게 앵커 폭이다.
    combined = _prompt_strings()
    assert len(combined) == (
        len(_axis_rule_strings())
        + len(_gate_feedback_strings())
        + len(_buffer_lexicon_strings())
    ), f"합친 목록이 세 축의 합과 다르다 — 어느 축이 빠졌다: {len(combined)}"


@pytest.mark.parametrize(
    "template", _a_owned_templates(), ids=lambda path: path.name
)
def test_every_a_owned_template_passes_the_masking_gate(template: pathlib.Path) -> None:
    """🔴 **A 소유 템플릿 전부가 `redact()` 를 지난다** (99 #196·#198).

    ⚠ 🔴 **`uncertain` 만 보면 안 된다** — `briefing.txt`·`counsel_plan.txt` 는 실측(8/24)에서
    **`findings=2 · uncertain=False`** 였고, 그 둘은 `redacted.masked_text` 를 보내므로
    **fail-closed 검사를 통과하면서 문면이 바뀐 채로** 나갔다. 🔴 «통과했다» 가 아니라
    «**마스킹된 채 나갔다**» 였다.
    """
    outcome = redact(template.read_text(encoding="utf-8"))
    assert not outcome.findings and not outcome.uncertain, (
        f"{template.name} 이 마스킹 문지기에 걸린다 — 이 템플릿을 쓰는 축은 "
        f"**지시문이 바뀐 채로** 나가거나 항상 막힌다. "
        f"걸린 유형: {[f.type for f in outcome.findings]}"
    )


@pytest.mark.parametrize(
    ("key", "text"), _prompt_strings(), ids=lambda value: str(value)[:48]
)
def test_every_prompt_string_passes_the_masking_gate(key: str, text: str) -> None:
    """🔴 **템플릿 밖의 프롬프트 문자열도 지난다** (99 #198 · №68).

    ⚠ 🔴 **`gate_feedback` 이 가장 안 보이는 자리다** — 게이트가 **실패했을 때만** 실려서
    정상 경로 검사에 안 나타난다. 그리고 그때 문면이 깨지면 **재생성 지시가 깨진 채로**
    가서 **재생성이 더 잘 실패한다.**
    ⚠ 🔴 **`tone_map` 은 자주 고치는 자리다** — 실측(8/24)에서 `interest.admission` 의
    «백분위**는**» 이 걸렸다(`백`+`분위`+조사). №46 이 쓴 문면이고, 뜻을 안 바꾸고
    조사만 떼어 고쳤다.
    """
    outcome = redact(text)
    assert not outcome.findings and not outcome.uncertain, (
        f"{key} 가 마스킹 문지기에 걸린다 — 이 문자열은 프롬프트에 실린다. "
        f"걸린 유형: {[f.type for f in outcome.findings]} · "
        f"나가는 문면: {outcome.masked_text[:70]!r}"
    )
