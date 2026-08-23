"""라벨 생성기 — 프롬프트·파서·fail-closed (99 #191).

⚠ 🔴 **실 LLM 0회** — `FakeLabelSuggestProvider` 와 순수 함수만 쓴다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Final

import pytest

from ai.composition.counsel.versions import counsel_versions
from ai.composition.labels.grounding import keep_sendable_history
from ai.composition.labels.prompt import PROMPT_VERSION, assemble_prompt
from ai.composition.labels.provider import (
    FakeLabelSuggestProvider,
    LabelSettings,
    MissingLabelSuggestProvider,
    build_label_suggest_provider,
    parse_suggestions,
)
from ai.contracts.execution import Capability, ExecutionContext
from ai.contracts.labels import HistoryItem
from ai.runtime.errors import LlmUpstreamDown
from ai.runtime.redaction import redact

_HISTORY: Final = tuple(
    HistoryItem(
        record_id=f"cm_{index}",
        direction="inbound",
        text=text,
        at=datetime(2026, 6, 12, 10, 11, tzinfo=UTC),
    )
    for index, text in enumerate(
        (
            "숫자로 정리해 주세요",
            "점수 추이 표로 부탁드려요",
            "지난주 결과가 궁금합니다",
            "표로 보여 주시면 좋겠어요",
            "이번 달 통계도 알려 주세요",
        ),
        start=88,
    )
)


def _context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=uuid.uuid4(),
        tenant_id="t1",
        capability=Capability.COMPOSITION,
        input_snapshot_hash="guardian:gd_1",
        versions=counsel_versions(),
    )


def test_the_prompt_forbids_comparison_and_rank() -> None:
    """🔴 **불변식 7** — 반 평균·석차·백분위·비교를 **문면으로** 막는다.

    ⚠ 입력이 「그 학부모의 소통 이력」뿐이라 재료가 없지만, **모델이 없는 것을 지어내는
    자리가 정확히 거기다**. 프롬프트 지시가 1차 방어다.
    """
    prompt = assemble_prompt(_HISTORY)
    for forbidden in ("반 평균", "석차", "백분위", "비교"):
        assert forbidden in prompt, f"불변식 7 문면에서 「{forbidden}」이 빠졌다"
    assert "지어내지 마세요" in prompt


def test_the_prompt_does_not_demand_all_four_axes() -> None:
    """🔴 «네 축을 다 채워라» 로 지시하지 않는다 — 근거 없으면 안 낸다(불변식 2)."""
    prompt = assemble_prompt(_HISTORY)
    assert "근거가 있는 축만" in prompt
    assert "빈 결과가 정직한 답" in prompt


def test_the_history_block_carries_record_ids_inline() -> None:
    """이력이 `record_id` 와 **같은 줄**에 실린다 — 모델이 인용에 붙일 수 있어야 한다."""
    prompt = assemble_prompt(_HISTORY)
    assert "[cm_88] 숫자로 정리해 주세요" in prompt
    #: ⚠ 🔴 `at`·`direction` 은 안 싣는다 — 축 판정에 안 쓰이고 시각은 식별을 좁힌다.
    assert "2026-06-12" not in prompt
    assert "inbound" not in prompt


@pytest.mark.parametrize(
    "line",
    [
        "comm | 자유라벨 | 0.5 | cm_88 | 숫자로",  # 열거형 밖
        "없는축 | data | 0.5 | cm_88 | 숫자로",  # 축이 아님
        "comm | data | 높음 | cm_88 | 숫자로",  # confidence 가 수가 아님
        "comm | data | 0.5",  # 필드 부족
    ],
)
def test_a_malformed_line_is_dropped_not_raised(line: str) -> None:
    """🔴 **형식이 틀린 줄은 조용히 버린다 — 예외를 올리지 않는다**(불변식 4).

    ⚠ 예외면 **한 줄의 오류가 나머지 제안까지 죽인다.** counsel 강조점 드롭과 같은 결.
    """
    assert parse_suggestions(line, guardian_ref="gd_1") == ()


def test_a_good_line_becomes_a_suggestion() -> None:
    """정상 줄 하나 — 인용문에 `|` 가 있어도 앞 넷만 가른다."""
    parsed = parse_suggestions(
        "comm | data | 0.86 | cm_88 | 숫자로 | 정리해 주세요", guardian_ref="gd_1"
    )
    assert len(parsed) == 1
    assert parsed[0].label.axis == "comm"
    assert parsed[0].evidence_quotes[0].quote == "숫자로 | 정리해 주세요"


@pytest.mark.anyio
async def test_the_fake_provider_runs_the_real_prompt_and_parser() -> None:
    """🔴 대역이 **프롬프트 조립과 파서를 실제로 탄다** — 흉내 내지 않는다(로그 129)."""
    parsed = await FakeLabelSuggestProvider().suggest(
        guardian_ref="gd_1", history=_HISTORY, context=_context()
    )
    assert len(parsed) == 1
    assert parsed[0].evidence_quotes[0].record_id == "cm_88"


@pytest.mark.anyio
async def test_the_missing_provider_raises_instead_of_returning_empty() -> None:
    """🔴 **№63 §E ③ 의 뒤집기를 여기로 옮겼다 — 지우지 않았다.**

    종전에는 라우터 층에서 «생성기 미구현을 빈 배열로 위장하면 red» 를 쟀는데,
    생성기가 생기면서(#191) **프로덕션 경로에서 그 상태가 사라졌다.** ⚠ 그렇다고 검사를
    지우면 **규율이 사라진다** — 「없다」와 「제안할 근거가 없다」를 가르는 그 규율은
    여전히 유효하다(04 §3.7 이 빈 배열의 뜻을 정의한다). ⇒ **대역 자체를 단위로 잰다.**
    """
    with pytest.raises(LlmUpstreamDown):
        await MissingLabelSuggestProvider().suggest(
            guardian_ref="gd_1", history=_HISTORY, context=_context()
        )


def test_the_builder_picks_the_fake_by_default() -> None:
    """`LLM_PROVIDER` 기본값은 fake — 🔴 **실 LLM 을 실수로 부르지 않는다.**"""
    assert isinstance(
        build_label_suggest_provider(LabelSettings(llm_provider="fake")),
        FakeLabelSuggestProvider,
    )


def test_the_prompt_version_is_declared() -> None:
    """재현성(불변식 8) — 프롬프트 버전이 선언돼 있다."""
    assert PROMPT_VERSION

# ── 🔴 걸린 이력만 빼고 진행 (99 #192 ⓑ · №66) ──────────────────────

_BLOCKED_TEXT: Final = "오답률이 높은가요"
"""🔴 **실측으로 고른 문면이다**(8/22) — `오`(성씨)+`답률`+조사 `이`. `findings=1 ·
uncertain=False` 라 **선검사가 `uncertain` 만 보면 빠지는 그 틈**이기도 하다(99 #83).

⚠ 🔴 **처음에 `성적표를`·`문제집이` 로 썼다가 두 검사가 `skip` 됐다** — №65 가 그 어간을
`name_exclude` 에 넣어 **더 이상 안 걸리기 때문**이다. 🔴 **skip 은 green 이 아니다** —
그대로 뒀으면 «걸러진다» 를 하나도 증명 못 하는 검사가 남았다.
⚠ 그래서 `_still_blocked()` 로 **먼저 확인**하고, 이 낱말도 언젠가 `name_exclude` 에
들어가면 그때 다시 골라야 한다 — 🔴 **두더지잡기의 대가가 여기에도 있다**(#178)."""


def _still_blocked(text: str) -> bool:
    outcome = redact(text)
    return bool(outcome.uncertain or outcome.findings)


def _history_with(*texts: str) -> tuple[HistoryItem, ...]:
    return tuple(
        HistoryItem(
            record_id=f"cm_{index}",
            direction="inbound",
            text=text,
            at=datetime(2026, 6, 12, 10, 11, tzinfo=UTC),
        )
        for index, text in enumerate(texts, start=88)
    )


def test_a_blocked_history_item_is_dropped_not_the_whole_request() -> None:
    """🔴 **한 건의 오탐이 제안 전체를 죽이지 않는다**(99 #192 ⓑ).

    ⚠ 🔴 **fail-closed 를 지킨다** — 걸린 건은 **안 보낸다**. 트립와이어도 그대로다.
    """
    blocked = _BLOCKED_TEXT
    if not _still_blocked(blocked):
        pytest.skip(f"{blocked!r} 가 이제 안 걸린다 — 다른 문면으로 재야 한다")
    history = _history_with(
        "숫자로 정리해 주세요",
        blocked,
        "수업 시간표를 알려 주세요",
        "다음 상담은 언제인가요",
        "결석하면 보충이 되나요",
    )
    kept, dropped = keep_sendable_history(history)
    assert len(kept) == 4, [item.record_id for item in kept]
    assert dropped == ("cm_89",), dropped
    #: 🔴 **본문이 아니라 `record_id` 만** 돌려준다(불변식 3 · 99 #80).
    assert all(not text.startswith("문제집") for text in dropped)


def test_a_clean_history_loses_nothing() -> None:
    """🔴 **오탐 시험** — 깨끗한 이력은 하나도 안 빠진다.

    ⚠ 이게 없으면 «전부 버리는 필터» 도 위 검사를 통과한다(앵커 폭).
    """
    history = _history_with(
        "숫자로 정리해 주세요",
        "수업 시간표를 알려 주세요",
        "다음 상담은 언제인가요",
        "결석하면 보충이 되나요",
        "모의고사 일정이 어떻게 되나요",
    )
    kept, dropped = keep_sendable_history(history)
    assert len(kept) == len(history)
    assert dropped == ()


def test_the_filter_looks_at_findings_not_only_uncertain() -> None:
    """🔴 `uncertain` 만 보면 **그 틈으로 빠진다**(99 #83 이 실측한 갈림).

    트립와이어는 `findings or uncertain` 으로 막으므로, 선검사가 `uncertain` 만 보면
    «조립은 통과인데 전송이 죽는다» 가 된다.
    """
    blocked = _BLOCKED_TEXT
    if not _still_blocked(blocked):
        pytest.skip(f"{blocked!r} 가 이제 안 걸린다")
    outcome = redact(blocked)
    assert outcome.findings and not outcome.uncertain, (
        "이 문면이 `uncertain` 을 내면 이 검사가 재려는 틈이 아니다"
    )
    kept, dropped = keep_sendable_history(_history_with(blocked))
    assert kept == () and dropped == ("cm_88",)

