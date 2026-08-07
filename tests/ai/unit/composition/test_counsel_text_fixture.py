"""대역 초안 문면이 **게이트를 실제로 통과하는가** — 픽스처의 계약 (99 #14).

🔴 **이 파일이 없으면 다음 사람이 꼬리에 숫자를 넣는다.** `_GATE_FLOOR_TAIL`은 36곳이
쓰는 공용 문면이고, 여기에 숫자가 하나 들어가면 `ungrounded_number`로 **전부** 막힌다 —
그때 나는 red는 36곳에 흩어져 원인이 안 보인다. **원인 자리에서 먼저 잡는다.**
"""

from __future__ import annotations

import pytest
from counsel_text import DEFAULT_DRAFT, GATE_FLOOR_TAIL, draft

from ai.composition.counsel.gate import check_counsel_gate
from ai.composition.counsel.provider import max_chars_for, min_chars_for
from ai.contracts.composition import (
    CommStyle,
    DraftContext,
    EvidenceFact,
    Frequency,
    Interest,
    LabelSnapshot,
    Sensitivity,
)
from ai.runtime.redaction import redact

_MAX_OF_NARROWEST_COMBINATION = 360
"""24조합 중 가장 좁은 `max_chars_for` — `tone_map.yaml` 실측(8/11).

⚠ 손으로 옮긴 값이 아니라 **아래 테스트가 실제 조합에서 재확인**한다.
"""


def _snapshot(
    comm: CommStyle, sensitivity: Sensitivity, interest: Interest, frequency: Frequency
) -> LabelSnapshot:
    return LabelSnapshot(
        comm=comm, sensitivity=sensitivity, interest=interest, frequency=frequency
    )


def _context() -> DraftContext:
    """근거가 있는 평범한 컨텍스트 — 게이트 일곱을 다 태우기 위한 것."""
    return DraftContext(
        student_ref="st_8f2a",
        guardian_ref="pa_9c1d",
        period_label="2026년 7월",
        label_snapshot=_snapshot(
            CommStyle.NARRATIVE, Sensitivity.ANXIOUS, Interest.ATTITUDE, Frequency.FREQUENT
        ),
        facts=(EvidenceFact(label="제출", value="꾸준함", record_id="le_1"),),
        fallback_text="이번 기간 학습 상황을 정리해 드립니다.",
    )


def test_the_padded_draft_passes_every_gate_rule() -> None:
    """🔴 꼬리가 게이트 일곱을 다 통과한다 — *"길이만 채운다"* 가 성립하려면 그래야 한다."""
    context = _context()
    result = check_counsel_gate(
        DEFAULT_DRAFT,
        context,
        max_chars=max_chars_for(context),
        min_chars=min_chars_for(context),
    )
    assert result.passed, (
        f"공용 대역 문면이 게이트에 걸린다: {result.reason} — 36곳이 이 문면을 쓴다. "
        "숫자·금칙어·내부 용어·기호를 넣지 마라(파일 머리말 ⓐⓒ)"
    )


def test_the_tail_carries_no_digits() -> None:
    """🔴 숫자 0개 — `ungrounded_number`가 `allowed_numbers()`와 **EXACT** 대조한다.

    ⚠ 위 테스트만으로는 부족하다. 거기 쓰는 컨텍스트가 우연히 그 숫자를 허용하면
    통과해 버리고, **다른 컨텍스트를 쓰는 35곳에서 터진다.**
    """
    assert not any(ch.isdigit() for ch in draft("")), "꼬리에 숫자가 있다"
    assert not any(ch.isdigit() for ch in draft("본문")), "꼬리에 숫자가 있다"


def test_the_tail_survives_redaction() -> None:
    """🔴 꼬리에 **마스킹 불확실**이 없다 — 있으면 전송 전 fail-closed로 **500**이 난다.

    ⚠ **숫자만 보면 놓친다.** 처음 쓴 문면이 `가정에서도`·`정리하는`에서 걸렸다 —
    「성씨 1자 + 이름 2자」 휴리스틱이 평범한 활용형을 인명 후보로 잡는다(5차 리포트
    §7-a가 같은 조각을 관측했다). **이 PR에서 실제로 난 실수라 단정으로 고정한다.**
    """
    result = redact(GATE_FLOOR_TAIL)
    assert not result.uncertain, (
        f"공용 대역 꼬리가 마스킹 불확실이다 — 36곳이 500을 낸다: {result.masked_text[:200]}"
    )
    assert "⟪" not in result.masked_text, result.masked_text[:200]


def test_the_padded_draft_fits_the_narrowest_window() -> None:
    """🔴 가장 좁은 조합에서도 하한↔상한 **창 안**이다 — 안 그러면 `too_long`으로 바뀐다."""
    padded = draft("이번 기간 학습 상황을 정리해 드립니다.")
    assert len(padded) <= _MAX_OF_NARROWEST_COMBINATION, (
        f"{len(padded)}자 — 가장 좁은 조합의 상한 {_MAX_OF_NARROWEST_COMBINATION}자를 넘는다"
    )


def test_the_narrowest_combination_is_what_we_think_it_is() -> None:
    """⚠ 위 상수가 **실제 조합에서 나온 값**인지 확인한다 — 손사본이면 조용히 낡는다.

    ⚠ 이 검사가 없으면 `tone_map.yaml`이 좁아졌을 때 위 테스트가 **틀린 기준**으로
    통과한다(#07이 잡은 형태 — `코드 == 손사본`).
    """
    narrowest = min(
        max_chars_for(
            _context().model_copy(update={"label_snapshot": snapshot})
        )
        for snapshot in (
            _snapshot(CommStyle.DATA, Sensitivity.DIRECT, Interest.GRADE, Frequency.FREQUENT),
            _snapshot(
                CommStyle.DATA, Sensitivity.ANXIOUS, Interest.ATTITUDE, Frequency.FREQUENT
            ),
            _snapshot(
                CommStyle.NARRATIVE, Sensitivity.DIRECT, Interest.GRADE, Frequency.FREQUENT
            ),
        )
    )
    assert narrowest == _MAX_OF_NARROWEST_COMBINATION, (
        f"가장 좁은 조합의 상한이 {narrowest}로 바뀌었다 — 위 상수를 같이 고쳐라"
    )


@pytest.mark.parametrize("degenerate", ["네.", "확인했습니다.", "알겠습니다."])
def test_the_floor_still_blocks_degenerate_output(degenerate: str) -> None:
    """🔴 **이 PR이 고치는 것** — 꼬리를 안 붙인 퇴화 산출은 여전히 막힌다.

    ⚠ 이 단정이 없으면 위 셋(대역을 통과시키는 장치)이 **하한 자체를 무르게 했는지**
    구분되지 않는다.
    """
    context = _context()
    result = check_counsel_gate(
        degenerate,
        context,
        max_chars=max_chars_for(context),
        min_chars=min_chars_for(context),
    )
    assert not result.passed, f"{degenerate!r}가 통과했다 — 하한이 안 걸린다"
    assert result.reason.startswith("too_short:"), result.reason
