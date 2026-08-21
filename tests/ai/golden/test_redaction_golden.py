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
    """§5 원형 30 + 누적 31~34 + 성명형/잔여/성씨함정 35~49(43 제외) + **산출물 재입력
    50~55** + **관계어/양·군 56~64** = 63 (누적만, §6).

    🔴 35~49는 8/5 미탐 실측분이다 — 종전 코퍼스가 별명형에 편중돼 "미탐 0" 게이트가
    통과하는 동안 성명형이 통째로 새고 있었다. 형태 커버리지 표는 코퍼스 상단에 있다.
    🔴 50~55는 8/6 실측분이다 — 이번엔 **조사 없는 형태에 편중**돼 `학생의`가 우회했다.
    형태 축을 조사 유무까지 갈라 적었고, 개별 형태 대신 **멱등성이라는 성질**을 코퍼스
    전건에 걸었다(`test_redaction_idempotence.py`).
    🔴 **65~72는 8/21 오탐 실측분이다**(99 #104·#123 · №53). 상담 fact·완충 사전 빈출어가
    「3음절 한자어 + 조사」 형태로 인명 후보에 걸려 **초안이 통째로 안 나왔다.**
    `whitelists.name_exclude` 5어간이 처방이고, **이 여덟이 그 목록의 감시자**다 — 어간을
    빼면 여기가 red 다. ⚠ 종전 이 자리의 편중 셋과 달리 이번은 **미탐이 아니라 오탐** 축이다.
    🔴 56~64는 8/6 실측분이다 — 이번엔 **관계어 앞 정식 성명이 한 건도 없었다**(미탐 14종)
    그리고 **학원 빈출어가 없었다**(오탐 9종). 편중 세 번째라, 형태 커버리지를 사람이
    표로 관리하는 대신 **곱집합을 생성해 CI가 재게** 했다(`test_redaction_coverage.py`).
    """
    # 🔴 손 유지 총수 — 올릴 때는 위 docstring 에 **왜 늘었는지**를 같이 적는다(결정 로그 145).
    assert len(CORPUS) == 71
    assert len({case.id for case in CORPUS}) == 71  # id 중복 없음


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


def test_uncertain_flag_never_stands_without_its_token() -> None:
    """`uncertain=True`면 반드시 `⟪확인필요⟫`가 있다 — **역은 성립하지 않는다.**

    🔴 **토큰과 트립와이어는 다른 축이다.** `policies/masking_redaction.md` §2
    [A 확정 7/23]은 후보가 **1개면 그 토큰만**, **2개 이상이면 문장 통째 + `uncertain=True`**
    로 갈랐다. 그러므로 토큰이 있어도 `uncertain`이 False인 경우가 **정상**이다.

    ⚠ 종전 단정은 `uncertain == (토큰 존재)`였다 — 그건 **구현을 베낀 것**이고, 그 구현은
    후보 1개에도 플래그를 세워 스펙보다 넓게 막았다. 그 결과 한국어 산문으로 도는 출제
    경로가 사실상 닫혀 있었다(실측: 문학 풀 문장의 9.4%가 후보 1개).

    🔴 **마스킹이 줄지 않는다는 보증은 이 검사가 아니라 `must_absent` 계열이 든다**
    (미탐 0 게이트) — 여기는 「플래그가 근거 없이 서지 않는가」만 본다.
    """
    for case in CORPUS:
        result = redact(case.text)
        if result.uncertain:
            assert "⟪확인필요⟫" in result.masked_text, (
                f"uncertain인데 토큰이 없다: {case.text!r}"
            )
