"""완충 사전 B군의 배선 — **프롬프트는 섰고 게이트는 안 섰다** (99 #79·#81·#82).

🔴 **B군 30항은 로드·검증만 되고 소비처가 0곳이었다.** 05 §4가 *"치환은 프롬프트 규칙 +
ToneSafety 게이트 검출 **병행**"* 이라고 규정하는데 **양쪽이 다 비어 있었다** —
게이트는 A군만 보고(`gate.py`), 프롬프트의 완충 문면은 추상 지시뿐이었다.

이 파일이 잠그는 것 셋:

    ① 판정 ① — `혼자만`·`반 평균보다`가 **게이트에서 막힌다**(A군으로 옮겼다 · 코드 0줄)
    ② 판정 ② — 나머지 B군은 **프롬프트에만** 싣는다. 🔴 **게이트에서 막으면 red다**
    ③ 판정 ③ — 단계 축은 「전 항」이다(`buffer_level ≥ 1`)

⚠ **②가 뒤집기다.** *"안 넣기로 한 것"* 을 넣으면 red가 나야 그 판정이 코드 주석에만 있는
것이 아니게 된다. 게이트에 넣으려면 **재생성 폭증 여부를 볼 빈도 표본**이 있어야 하는데
그 표본이 구조적으로 없다(99 #80) — `runtime/draft_observation.py`가 그 표본을 만든다.
"""

from __future__ import annotations

import logging
from typing import Final
from uuid import UUID

import pytest

from ai.composition.buffer_lexicon import EXPECTED_TERM_COUNT, load_buffer_lexicon
from ai.composition.counsel.gate import check_counsel_gate
from ai.composition.counsel.prompt import _buffer_replacements, assemble_prompt
from ai.contracts.composition import (
    CommStyle,
    DraftContext,
    EvidenceFact,
    Frequency,
    Interest,
    LabelSnapshot,
    Sensitivity,
)
from ai.runtime.draft_observation import ORIGIN_DRAFT, observe_gated_draft

_TENANT: Final = "t_buffer_b"
_RUN: Final = str(UUID("00000000-0000-4000-8000-0000000000b0"))


def _context(*, sensitivity: Sensitivity = Sensitivity.ANXIOUS) -> DraftContext:
    return DraftContext(
        student_ref="st_1",
        guardian_ref="gd_1",
        label_snapshot=LabelSnapshot(
            comm=CommStyle.DATA,
            sensitivity=sensitivity,
            interest=Interest.GRADE,
            frequency=Frequency.FREQUENT,
        ),
        facts=(EvidenceFact(label="이번 주 정답률", value="62%"),),
        evidence_summaries=("지난주 과제 2건 미제출",),
        period_label="2026년 7월",
        fallback_text="이번 주 학습 상황을 정리해 보내드립니다.",
    )


# ── ① 판정 ① — 비교 2항이 게이트에서 막힌다 ─────────────────────


@pytest.mark.parametrize("term", ["반 평균보다", "혼자만"])
def test_the_moved_comparison_terms_are_blocked_by_the_gate(term: str) -> None:
    """🔴 **판정 ①의 정본** — 코드 **0줄**로 불변식 7의 출력측 방어가 2항 → 4항이 됐다.

    두 항은 `to`가 **빈 문자열**이라 치환이 아니었는데 B군(치환군)에 있었고, B군은
    소비처가 0곳이라 **아무 데서도 안 막혔다.** A군으로 옮기니 `gate.py`가 이미 검출한다.
    """
    body = f"이 학생은 {term} 조금 더 시간이 걸리는 편입니다. " * 6
    result = check_counsel_gate(body, _context(), max_chars=2000, min_chars=10)

    assert result.passed is False, f"{term!r}이 게이트를 통과했다 — 불변식 7의 출력측 구멍"
    assert result.reason.startswith("forbidden:"), (
        f"사유가 A군 형태가 아니다: {result.reason!r} — 사유 코드가 바뀌면 BE 매핑이 낡는다"
    )


def test_the_comparison_axis_still_has_gaps() -> None:
    """⚠ **닫힌 것과 안 닫힌 것을 같이 적는다** — 「비교가 막힌다」로 뭉뚱그리지 않는다.

    🔴 `다른 학생에 비해`는 **여전히 통과한다** — 그건 `to`가 있는 **실제 치환**이라
    B군이 맞고(비교축을 타인→본인 과거로 전환), B군의 게이트 축은 아직 안 섰다(99 #79).
    """
    body = "이 학생은 다른 학생에 비해 시간이 조금 더 걸리는 편입니다. " * 6
    result = check_counsel_gate(body, _context(), max_chars=2000, min_chars=10)

    assert result.passed is True, (
        "`다른 학생에 비해`가 막혔다 — 판정 ②(B군은 게이트에 안 넣는다)를 어긴 것이다"
    )


# ── ② 판정 ③ — 단계 축 ────────────────────────────────────────


def test_the_vocabulary_rides_the_prompt_from_level_one() -> None:
    """🔴 완충 ≥1이면 B군 어휘가 프롬프트에 실린다 — 05 §4의 「프롬프트 규칙」 축.

    ⚠ 종전에는 *"단정 표현을 관찰·상태 서술로 바꾸세요"* 라는 **추상 지시뿐**이라
    정보가 0이었다. 어휘가 실려야 LLM이 무엇을 어떻게 바꿀지 안다.
    """
    line = _buffer_replacements(1)
    assert line, "완충 1인데 어휘 문면이 비었다"
    assert "→" in line, f"치환 쌍(from→to)이 안 보인다: {line[:80]!r}"
    #: 실제 사전 어휘로 확인한다 — 대역 문면을 쓰면 검사가 눈이 먼다.
    assert "심각합니다" in line and "최악" in line, line[:120]

    #: 🔴 **조립된 프롬프트까지 본다 — 함수만 보면 「소비처 0」을 못 잡는다.**
    #: 이 PR이 고치는 병이 정확히 그것이다(만드는 것과 배선하는 것은 다르다).
    #: 실측(8/19): 여기가 없을 때 `render_tone_rules`의 배선을 지워도 검사가 green이었다.
    prompt = assemble_prompt(_context())
    assert "심각합니다" in prompt, "어휘가 조립된 프롬프트에 안 실렸다 — 배선이 끊겼다"


def test_level_zero_carries_no_vocabulary() -> None:
    """완충 0에서는 안 실린다 — 단계 축이 실제로 동작하는가."""
    assert _buffer_replacements(0) == ""


def test_the_two_levels_are_identical_on_this_axis() -> None:
    """⚠ **판정 ③(전 항)의 대가를 검사로 적어 둔다** — 완충 1과 2가 이 축에서 같다.

    🔴 05 §4는 *"1 = B군 **기본** · 2 = B군 **전체**"* 인데 **yaml에 단계 필드가 없다**
    (99 #82). 근거 없이 30항을 가르면 그게 곧 하드코딩된 임의값이라(03 §1) 전 항을 싣는다.
    ⚠ 두 단계는 여전히 `_BUFFER_TEXT`의 **부정문 후치 지시**로 갈린다 — 아래에서 확인한다.
    """
    assert _buffer_replacements(1) == _buffer_replacements(2)
    #: 그래도 프롬프트 전체는 갈린다 — 단계 구분이 통째로 죽은 것이 아니다.
    assert "부정적인 내용은" in assemble_prompt(_context(sensitivity=Sensitivity.ANXIOUS))


def test_the_lexicon_counts_are_still_fail_closed() -> None:
    """어휘 수·분류 불변식이 산다 — 이동은 **합계를 안 바꾼다**."""
    lexicon = load_buffer_lexicon()
    assert len(lexicon.forbidden) + len(lexicon.replacements) == EXPECTED_TERM_COUNT
    assert set(lexicon.conjugating) <= set(lexicon.forbidden)
    #: 🔴 새 2항은 `conjugating`에 없다 — 구·명사구라 종성 결합 대상이 아니다.
    assert "혼자만" not in lexicon.conjugating
    assert "반 평균보다" not in lexicon.conjugating


# ── ③ 판정 ② — 관측만 한다. 🔴 막으면 red ────────────────────────


def test_group_b_vocabulary_is_counted_not_blocked(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """🔴 **판정 ②의 정본** — B군 어휘가 든 본문은 **차단되지 않고 계수만** 된다.

    여기서 막히면 **ⓐ(게이트 검출)를 몰래 넣은 것**이다. 게이트에 넣으려면 재생성 폭증
    여부를 볼 빈도 표본이 있어야 하는데 그게 없다(99 #80) — 이 관측이 그 표본을 만든다.
    """
    body = "이번 결과는 심각합니다. 최악의 주였습니다. " * 6
    gate = check_counsel_gate(body, _context(), max_chars=2000, min_chars=10)
    assert gate.passed is True, (
        f"B군 어휘가 게이트에서 막혔다({gate.reason!r}) — 판정 ②를 어긴 것이다"
    )

    with caplog.at_level(logging.WARNING, logger="ai.runtime.draft_observation"):
        hits = observe_gated_draft(
            body, origin=ORIGIN_DRAFT, tenant_id=_TENANT, execution_id=_RUN
        )

    assert set(hits) >= {"심각합니다", "최악"}, hits
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "심각합니다" in logged, "적중 어휘가 로그에 안 남았다 — 표본이 안 쌓인다"
    #: 🔴 **본문을 로그에 싣지 않는다**(불변식 3) — 길이·식별자·적중 어휘까지다.
    assert "최악의 주였습니다" not in logged, "본문이 로그에 실렸다 — 불변식 3 위반"


def test_both_generation_paths_call_the_observation() -> None:
    """🔴 **자리가 둘이다 — 둘 다 부르는지 본다** (99 #02).

    ⚠ **관측 함수를 직접 부르는 검사만 두면 「만들었지만 아무도 안 부른다」를 못 잡는다** —
    이 PR이 고치는 병이 정확히 그것이다(B군 30항이 로드만 되고 소비처가 0이었다).
    실측(8/19): 여기가 없을 때 두 배선을 다 지워도 검사가 green이었다.

    ⚠ **호출 횟수가 아니라 「그 파일이 부르는가」를 본다** — 구현 단언이 아니라
    *"게이트 통과 경로가 관측을 지나는가"* 라는 배선 사실이다.
    """
    import ast  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    root = Path(__file__).resolve().parents[4] / "src" / "ai" / "composition" / "counsel"
    missing = [
        name
        for name in ("graph.py", "refine.py")
        if not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "observe_gated_draft"
            for node in ast.walk(ast.parse((root / name).read_text(encoding="utf-8")))
        )
    ]
    assert not missing, (
        f"관측을 안 부르는 생성 경로: {missing} — 표본이 그 경로에서만 안 쌓인다 (99 #80)"
    )


def test_an_uncertain_body_is_logged_not_blocked(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """🔴 **결정 ①의 정본** — 출력측 마스킹 불확실은 **차단하지 않는다**.

    HITL이 있고(강사 승인 뒤 발송) `redact()` 오탐이 실측돼 있어(99 #77) 차단·변형 둘 다
    미탐보다 비싸다. ⇒ 계수만 한다. **차단하면 red다.**
    """
    # 🔴 한 문장에 인명 후보 **둘** — 밀도 규칙상 그래야 `uncertain`이 선다
    #   (`policies/masking_redaction.md` §2 [A 확정 7/23]). 종전 문면은 후보 1개짜리라
    #   구현이 스펙보다 넓게 막던 시절에만 불확실로 잡혔다. 검사의 의도(「불확실해도
    #   차단하지 않고 계수만 한다」)는 그대로 두고 불확실을 만드는 조건만 맞췄다.
    body = (
        "민준이가 서연이와 함께 왔습니다. "
        + "가정에서도 같은 방향으로 지켜봐 주시면 좋겠습니다. " * 6
    )
    with caplog.at_level(logging.WARNING, logger="ai.runtime.draft_observation"):
        returned = observe_gated_draft(
            body, origin=ORIGIN_DRAFT, tenant_id=_TENANT, execution_id=_RUN
        )

    assert isinstance(returned, tuple), "관측이 예외를 올렸다 — 관측이 아니라 게이트다"
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "마스킹 불확실" in logged, "출력측 마스킹 관측이 안 남았다 (99 #28의 표본)"
    assert "가정에서도 같은 방향으로" not in logged, "본문이 로그에 실렸다"


# ── 잔여 1 — 쌍을 못 실어도 `from`은 살린다 (99 #83) ────────────────


def test_a_from_only_term_rides_the_avoid_list() -> None:
    """🔴 **쌍을 통째로 버리지 않는다** — `to`만 걸리는 항은 회피 목록으로 살린다.

    ⚠ **조립된 프롬프트를 본다** — 함수를 직접 부르면 배선을 지워도 green이다
    (PR-α의 고의 파괴 ②가 그 병이었다).
    """
    prompt = assemble_prompt(_context())

    assert "쓰지 마세요" in prompt, "회피 목록 줄이 없다 — 조립 배선이 끊겼다"
    assert "실패했습니다" in prompt, "`to`만 걸리는 항이 프롬프트에서 통째로 빠졌다"
    assert "안 했습니다" in prompt


def test_the_two_lists_are_separate_lines() -> None:
    """🔴 치환 목록과 회피 목록을 **한 줄에 섞지 않는다.**

    섞으면 화살표 없는 항을 LLM이 **치환 대상**으로 읽는다.
    """
    line = _buffer_replacements(1)
    replace_line = next(ln for ln in line.split("\n") if "바꿔 쓰세요" in ln)
    avoid_line = next(ln for ln in line.split("\n") if "쓰지 마세요" in ln)

    assert "→" in replace_line
    assert "→" not in avoid_line, f"회피 목록에 화살표가 섞였다: {avoid_line[:80]!r}"
    assert "실패했습니다" not in replace_line, "쌍이 아닌 항이 치환 목록에 실렸다"


def test_a_from_blocked_term_stays_out() -> None:
    """⚠ `from`이 걸리는 항은 **여전히 못 싣는다** — 그 사실을 고정한다.

    🔴 **「안 된다」를 검사로 박아 두면 나중에 휴리스틱이 나아졌을 때 red가 나서
    자동 복귀가 눈에 보인다**(#04 규율 — 경계를 말했으면 그 자리에 red를 남긴다).

    ⚠ **`다른 학생에 비해`도 여기 있다** — 8/19 지시서는 걸린 조각이 `지난달`(to)이라
    봤으나 실측은 **`from`이 확정 검출**(`이름:⟪이름1⟫`)이다. ⇒ 그 항은 A군에도·
    프롬프트에도·게이트에도 없다. **불변식 7 축에 남은 구멍이다**(99 #79·#83).
    """
    prompt = assemble_prompt(_context())

    assert "이해력이 부족" not in prompt
    assert "다른 학생에 비해" not in prompt, (
        "🔴 `from`이 걸리는 항이 실렸다 — 휴리스틱이 나아졌다면 이 단정과 99 #83을 같이 고쳐라"
    )


# ── 잔여 2 — 계수는 「하한」이다 (99 #80) ──────────────────────────


def test_the_hit_count_is_a_lower_bound() -> None:
    """🔴 **종결형 등재 항의 다른 활용형은 안 잡힌다 — 계수가 하한임을 검사로 고정한다.**

    ⚠ **원인은 「종성 결합 미적용」이 아니다** — `find_forbidden`은 부분 문자열로 보므로
    **어간으로 등재된 항은 활용형도 잡는다**(아래 대조군). 진짜 원인은 B군 다수가
    `~습니다` **종결형**으로 등재돼 다른 활용형은 **표면이 달라** 안 걸리는 것이다.

    🔴 **이 숫자가 다음 회차 게이트 판정의 유일한 재료다**(99 #79) — 하한인 줄 모르고
    읽으면 *"빈도가 낮으니 안전하다"* 로 **틀린 방향**으로 기운다.
    ⚠ B군을 어간으로 재등재하면 이 검사가 red가 나서 **판정 재료가 바뀐 것을 알게 된다**
    (#04 규율 — 경계를 말했으면 그 자리에 red를 남긴다 · 99 #84).
    """
    #: 🔴 미검출 — 등재가 종결형(`심각합니다`·`느립니다`·`약점입니다`)이다.
    for body, entry in (
        ("이번 주는 심각한 상황입니다.", "심각합니다"),
        ("문제를 느려서 오래 붙잡고 있습니다.", "느립니다"),
        ("추론이 약점이 되고 있습니다.", "약점입니다"),
    ):
        hits = observe_gated_draft(
            body, origin=ORIGIN_DRAFT, tenant_id=_TENANT, execution_id=_RUN
        )
        assert entry not in hits, (
            f"{entry!r}의 활용형이 잡힌다 — B군을 어간으로 재등재했다는 뜻이다. "
            "계수가 더는 하한이 아니므로 99 #84와 이 검사를 같이 고쳐라"
        )

    #: ⚠ **대조군 — 어간으로 등재된 항은 활용형도 잡힌다.** 이게 없으면 위 단정이
    #: *"관측이 아무것도 못 잡는다"* 와 구분되지 않는다.
    caught = observe_gated_draft(
        "과제를 게을리했습니다.",
        origin=ORIGIN_DRAFT,
        tenant_id=_TENANT,
        execution_id=_RUN,
    )
    assert "게을리" in caught, "어간 등재 항의 활용형도 안 잡힌다 — 관측이 아예 눈이 멀었다"
