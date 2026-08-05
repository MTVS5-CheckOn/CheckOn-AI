"""redaction 골든 코퍼스 게이트 — 미탐 0 = CI 차단 (masking_redaction §5).

이 테스트가 CLAUDE.md §5의 "redaction 코퍼스 미탐 0건 = CI 게이트"의 실체다.
- 미탐(마스킹됐어야 할 조각이 남음) 1건이라도 실패.
- 오탐(문맥 함정에서 유지돼야 할 말이 마스킹됨)은 23~26 통틀어 ≤2, 초과 시 실패.
- 결정론: 같은 입력 반복 → 같은 출력(토큰 번호 포함).
"""

from __future__ import annotations

import pytest

from ai.evaluation.golden.redaction.corpus import CORPUS, RedactionCase
from ai.runtime.redaction import redact

_FALSE_POSITIVE_BUDGET = 2


def test_corpus_has_expected_cases() -> None:
    """§5 원형 30 + 누적 31~34 + **성명형/잔여/성씨함정 35~49(43 제외)** = 48 (누적만, §6).

    🔴 35~49는 8/5 미탐 실측분이다 — 종전 코퍼스가 별명형에 편중돼 "미탐 0" 게이트가
    통과하는 동안 성명형이 통째로 새고 있었다. 형태 커버리지 표는 코퍼스 상단에 있다.
    """
    assert len(CORPUS) == 48
    assert len({case.id for case in CORPUS}) == 48  # id 중복 없음


def test_no_miss_across_corpus() -> None:
    """미탐 0 — 마스킹됐어야 할 원문 조각이 하나라도 남으면 실패(전 건 수집 후 보고)."""
    misses: list[str] = []
    for case in CORPUS:
        masked = redact(case.text).masked_text
        for pii in case.must_absent:
            if pii in masked:
                misses.append(f"[{case.id} {case.category}] '{pii}' 잔존 → {masked!r}")
    assert not misses, "미탐 발생(배포 차단):\n" + "\n".join(misses)


def test_false_positive_within_budget() -> None:
    """오탐 ≤2 (문맥 함정 23~26) — 유지돼야 할 말이 마스킹되면 오탐. 현재 수 리포트."""
    false_positives: list[str] = []
    for case in CORPUS:
        if not case.context_trap:
            continue
        masked = redact(case.text).masked_text
        for keep in case.must_present:
            if keep not in masked:
                false_positives.append(f"[{case.id}] '{keep}' 오탐 → {masked!r}")
    print(f"\n[redaction] 문맥 함정 오탐 {len(false_positives)}/{_FALSE_POSITIVE_BUDGET} 허용")
    assert len(false_positives) <= _FALSE_POSITIVE_BUDGET, "오탐 허용 초과:\n" + "\n".join(
        false_positives
    )


@pytest.mark.parametrize("case", CORPUS, ids=lambda c: f"{c.id}-{c.category}")
def test_expected_output_exact(case: RedactionCase) -> None:
    """기대 출력 정확 일치 — 회귀·토큰 번호 고정(골든 기대값 임의 수정 금지)."""
    assert redact(case.text).masked_text == case.expected


@pytest.mark.parametrize("case", CORPUS, ids=lambda c: str(c.id))
def test_deterministic(case: RedactionCase) -> None:
    """같은 입력 → 같은 출력(토큰 번호 포함)."""
    first = redact(case.text)
    second = redact(case.text)
    assert first.masked_text == second.masked_text
    assert first.findings == second.findings
    assert first.uncertain == second.uncertain


def test_uncertain_flag_set_when_confirm_token_present() -> None:
    """⟪확인필요⟫가 있으면 uncertain=True(소비자 fail-closed 근거)."""
    for case in CORPUS:
        result = redact(case.text)
        assert result.uncertain == ("⟪확인필요⟫" in result.masked_text)
