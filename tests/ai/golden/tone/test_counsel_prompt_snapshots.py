"""24조합 프롬프트 스냅숏 골든 — 08 §3 (LLM 호출 없음·결정론).

방식(08 §3): **동일 입력**(학생 1명·문의 1건 고정) × **24 라벨 조합** → 조립된 프롬프트를
검증한다. 두 가지를 본다.
  ① 조합별 `tone_map` 파라미터가 프롬프트에 반영됐는지
  ② **인접 조합끼리 diff가 존재하는지** — 조합을 바꿨는데 출력이 같으면 매핑 미적용 버그

**기대값은 엔진 산출을 붙여넣지 않았다.** 아래 `_DOC_EXPECTED`는
`docs/part_a/05_tone_mapping.md` §2 표와 PR #42의 `composition/tone_map.yaml`을 읽고
손으로 옮긴 값이다(7/23 redaction 감사 규칙 — 스냅숏 자동 생성 금지).
"""

from __future__ import annotations

import itertools

import pytest

from ai.composition.counsel.prompt import assemble_prompt
from ai.contracts.composition import (
    CommStyle,
    DraftContext,
    EvidenceFact,
    Frequency,
    Interest,
    LabelSnapshot,
    Sensitivity,
)

#: 05 §2 표에서 손으로 옮긴 24행 — (blocks 첫 항목, 문장 수, 완충 단계).
#: 완충은 anxious=2 · direct=1(표 전 행 공통), 문장 수는 각 행의 '길이' 열이다.
_DOC_EXPECTED: dict[str, tuple[str, int, int]] = {
    # data · anxious (1~6) — 전부 인사로 시작, 완충 2
    "data.anxious.grade.frequent": ("인사", 2, 2),
    "data.anxious.grade.monthly": ("인사", 4, 2),
    "data.anxious.attitude.frequent": ("인사", 2, 2),
    "data.anxious.attitude.monthly": ("인사", 4, 2),
    "data.anxious.admission.frequent": ("인사", 2, 2),
    "data.anxious.admission.monthly": ("인사", 4, 2),
    # data · direct (7~12) — 결론 선행, 완충 1
    "data.direct.grade.frequent": ("결론(수치)", 2, 1),
    "data.direct.grade.monthly": ("결론", 3, 1),
    "data.direct.attitude.frequent": ("결론(성실도)", 2, 1),
    "data.direct.attitude.monthly": ("결론", 3, 1),
    "data.direct.admission.frequent": ("결론(위치)", 2, 1),
    "data.direct.admission.monthly": ("결론", 3, 1),
    # narrative · anxious (13~18)
    "narrative.anxious.grade.frequent": ("인사", 3, 2),
    "narrative.anxious.grade.monthly": ("인사", 5, 2),
    "narrative.anxious.attitude.frequent": ("인사", 3, 2),
    "narrative.anxious.attitude.monthly": ("인사", 5, 2),
    "narrative.anxious.admission.frequent": ("인사", 3, 2),
    "narrative.anxious.admission.monthly": ("인사", 5, 2),
    # narrative · direct (19~24) — 핵심 선행
    "narrative.direct.grade.frequent": ("핵심 서사", 3, 1),
    "narrative.direct.grade.monthly": ("핵심", 4, 1),
    "narrative.direct.attitude.frequent": ("핵심(태도)", 3, 1),
    "narrative.direct.attitude.monthly": ("핵심", 4, 1),
    "narrative.direct.admission.frequent": ("핵심(위치)", 3, 1),
    "narrative.direct.admission.monthly": ("핵심", 4, 1),
}

#: 완충 단계별 문면 앞부분 — prompt.py `_BUFFER_TEXT`가 문서 §4 "적용 단계"를 옮긴 것.
_BUFFER_MARK = {1: "완충 1 —", 2: "완충 2 —"}


def _context(key: str) -> DraftContext:
    """08 §3 "동일 입력" — 라벨만 바뀌고 학생·근거는 고정한다."""
    comm, sensitivity, interest, frequency = key.split(".")
    return DraftContext(
        student_ref="st_fixed",
        guardian_ref="gd_fixed",
        label_snapshot=LabelSnapshot(
            comm=CommStyle(comm),
            sensitivity=Sensitivity(sensitivity),
            interest=Interest(interest),
            frequency=Frequency(frequency),
        ),
        facts=(EvidenceFact(label="이번 주 정답률", value="62%"),),
        evidence_summaries=("지난주 과제 2건 미제출",),
        period_label="2026년 7월",
        fallback_text="이번 주 학습 상황을 정리해 보내드립니다.",
    )


def test_doc_table_covers_24_combinations() -> None:
    """손으로 옮긴 기대표가 4축 데카르트곱 24개를 전부 덮는다."""
    product = {
        ".".join(combo)
        for combo in itertools.product(
            [c.value for c in CommStyle],
            [s.value for s in Sensitivity],
            [i.value for i in Interest],
            [f.value for f in Frequency],
        )
    }
    assert set(_DOC_EXPECTED) == product
    assert len(_DOC_EXPECTED) == 24


# ── ① 조합별 tone_map 파라미터가 프롬프트에 반영됐는가 ─────────────


@pytest.mark.parametrize("key", sorted(_DOC_EXPECTED))
def test_prompt_reflects_doc_parameters(key: str) -> None:
    first_block, sentences, buffer_level = _DOC_EXPECTED[key]
    prompt = assemble_prompt(_context(key))
    assert key in prompt  # 조합 키가 문면에 실린다
    assert f"1. {first_block}" in prompt  # 05 §2 '구성'의 첫 블록
    assert f"{sentences}문장" in prompt  # 05 §2 '길이'
    # ⚠ 지시문 어휘는 세 번 바뀌었다 — "블록당"(0.1) → "문단마다"(0.2) → "…문장 안팎"(0.4).
    # 🔴 **어구를 박지 않는다** — 이 검사가 재는 것은 「tone_map 의 값이 프롬프트에 실리는가」이지
    #   특정 문구가 아니다. 어구를 박으면 프롬프트를 다듬을 때마다 이 검사가 이유 없이 red 다.
    assert _BUFFER_MARK[buffer_level] in prompt  # 05 §4 적용 단계


@pytest.mark.parametrize("key", sorted(_DOC_EXPECTED))
def test_prompt_is_deterministic(key: str) -> None:
    assert assemble_prompt(_context(key)) == assemble_prompt(_context(key))


# ── ② 인접 조합끼리 diff가 존재하는가 (매핑 미적용 버그 검출) ───────


def test_all_24_prompts_are_distinct() -> None:
    """같은 입력에 라벨만 바꿨을 때 24개 프롬프트가 전부 달라야 한다."""
    prompts = {key: assemble_prompt(_context(key)) for key in _DOC_EXPECTED}
    duplicates = [
        (a, b)
        for a, b in itertools.combinations(sorted(prompts), 2)
        if prompts[a] == prompts[b]
    ]
    assert not duplicates, f"조합이 다른데 프롬프트가 같다(매핑 미적용): {duplicates[:3]}"


@pytest.mark.parametrize(
    ("left", "right", "axis"),
    [
        ("data.anxious.grade.frequent", "narrative.anxious.grade.frequent", "comm"),
        ("data.anxious.grade.frequent", "data.direct.grade.frequent", "sensitivity"),
        ("data.anxious.grade.frequent", "data.anxious.attitude.frequent", "interest"),
        ("data.anxious.grade.frequent", "data.anxious.grade.monthly", "frequency"),
    ],
)
def test_single_axis_change_changes_prompt(left: str, right: str, axis: str) -> None:
    """축 하나만 바꿔도 프롬프트가 달라진다 — 축별 규칙이 실제로 반영된다는 증거."""
    assert assemble_prompt(_context(left)) != assemble_prompt(_context(right)), axis


def test_frequency_axis_changes_sentence_count() -> None:
    """frequent(짧게) vs monthly(충실하게) — 05 §1 frequency 축 효과."""
    frequent = _DOC_EXPECTED["data.anxious.grade.frequent"][1]
    monthly = _DOC_EXPECTED["data.anxious.grade.monthly"][1]
    assert frequent < monthly
    assert f"{frequent}문장" in assemble_prompt(_context("data.anxious.grade.frequent"))
    assert f"{monthly}문장" in assemble_prompt(_context("data.anxious.grade.monthly"))


def test_sensitivity_axis_changes_buffer_level() -> None:
    """anxious는 완충 2, direct는 완충 1 — 05 §2 완충 열."""
    assert _BUFFER_MARK[2] in assemble_prompt(_context("data.anxious.grade.frequent"))
    assert _BUFFER_MARK[1] in assemble_prompt(_context("data.direct.grade.frequent"))


def test_direct_leads_with_conclusion() -> None:
    """direct는 결론 선행(05 §1) — 첫 블록이 인사가 아니다."""
    for key, (first_block, _s, _b) in _DOC_EXPECTED.items():
        if ".direct." in key:
            assert first_block != "인사", key
        else:
            assert first_block == "인사", key
