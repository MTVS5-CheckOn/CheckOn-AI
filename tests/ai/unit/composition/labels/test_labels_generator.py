"""라벨 생성기 — 프롬프트·파서·fail-closed (99 #191).

⚠ 🔴 **실 LLM 0회** — `FakeLabelSuggestProvider` 와 순수 함수만 쓴다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Final

import pytest

from ai.composition.counsel.versions import counsel_versions
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
