"""라벨 생성기 — 프롬프트·파서·fail-closed (99 #191).

⚠ 🔴 **실 LLM 0회** — `FakeLabelSuggestProvider` 와 순수 함수만 쓴다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, Final, cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from ai.composition.counsel.versions import counsel_versions
from ai.composition.labels.grounding import ground_suggestions, keep_sendable_history
from ai.composition.labels.prompt import PROMPT_VERSION, assemble_prompt
from ai.composition.labels.provider import (
    HISTORY_ALL_BLOCKED,
    FakeLabelSuggestProvider,
    GatewayLabelSuggestProvider,
    LabelHistoryUnusable,
    LabelSettings,
    MissingLabelSuggestProvider,
    build_label_suggest_provider,
    merge_duplicate_axes,
    parse_suggestions,
)
from ai.contracts.counsel import LabelSuggestion
from ai.contracts.execution import Capability, ExecutionContext
from ai.contracts.labels import (
    EvidenceQuote,
    HistoryItem,
    LabelSuggestResponse,
    SuggestedLabel,
)
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
        pytest.fail(
            f"{blocked!r} 가 이제 안 걸린다 — 🔴 이 검사가 재려는 대상이 사라졌다. "
            "`_BLOCKED_TEXT` 를 실제로 걸리는 문면으로 바꿔라(skip 은 green 이 아니다)"
        )
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
        pytest.fail(
            f"{blocked!r} 가 이제 안 걸린다 — 🔴 이 검사가 재려는 틈이 사라졌다"
        )
    outcome = redact(blocked)
    assert outcome.findings and not outcome.uncertain, (
        "이 문면이 `uncertain` 을 내면 이 검사가 재려는 틈이 아니다"
    )
    kept, dropped = keep_sendable_history(_history_with(blocked))
    assert kept == () and dropped == ("cm_88",)

@pytest.mark.anyio
async def test_the_provider_actually_drops_the_blocked_item_from_the_prompt() -> None:
    """🔴 **배선을 잰다 — 필터가 실제로 프롬프트에서 빠지게 하는가.**

    ⚠ 🔴 **이 검사가 없어서 뒤집기가 green 이었다**(8/22 실측): 픽스처 검사는 **대역
    provider 를 주입**하므로 실 `GatewayLabelSuggestProvider.suggest` 를 **안 지나고**,
    단위 검사는 `keep_sendable_history` 를 **직접** 부른다 ⇒ **provider 가 그 함수를 안 불러도
    둘 다 통과한다.** 🔴 «함수는 맞는데 배선을 안 했다» 를 아무도 안 봤다 —
    #382 의 `meta.versions` 와 **같은 형태**다(99 #191).

    ⇒ **게이트웨이 자리에 기록기를 넣어 실제로 나간 프롬프트**를 본다.
    """
    sent: list[str] = []

    class _RecordingGateway:
        async def complete(self, request: object, context: object) -> object:
            del context
            sent.append(getattr(request, "prompt"))  # noqa: B009 — 실효값 확인
            return SimpleNamespace(text="")

    blocked = _BLOCKED_TEXT
    if not _still_blocked(blocked):
        pytest.fail(f"{blocked!r} 가 이제 안 걸린다 — 이 검사가 재려는 대상이 사라졌다")
    #: 🔴 **여섯 건이다** — `MIN_HISTORY = 5` 라 다섯 건에서 하나가 빠지면 **미달**이 되고
    #: 그때는 판정 대기 구간(99 #194)이 **전체를 태운다.** ⚠ 그러면 이 검사가 재려는
    #: «걸린 건만 빠진다» 를 못 잰다 — **필터가 실제로 효과를 내는 구간**에서 재야 한다.
    #: 🔴 **그 사실 자체가 이 회차의 발견이다**: 요청 하한과 필터 하한이 같은 5라서
    #: **다섯 건 요청은 한 건만 걸려도 필터가 무효**다(99 #194 에 적었다).
    history = _history_with(
        "숫자로 정리해 주세요",
        blocked,
        "수업 시간표를 알려 주세요",
        "다음 상담은 언제인가요",
        "결석하면 보충이 되나요",
        "모의고사 일정이 어떻게 되나요",
    )
    provider = GatewayLabelSuggestProvider(cast("Any", _RecordingGateway()))
    await provider.suggest(guardian_ref="gd_1", history=history, context=_context())

    assert len(sent) == 1, "게이트웨이가 안 불렸다 — 선검사가 통째로 막았을 수 있다"
    assert blocked not in sent[0], (
        f"걸린 이력이 프롬프트에 그대로 실렸다 — `keep_sendable_history` 배선이 없다: {blocked!r}"
    )
    assert "숫자로 정리해 주세요" in sent[0], "안 걸린 이력까지 빠졌다"

@pytest.mark.anyio
async def test_an_all_blocked_history_is_a_failure_not_an_empty_list() -> None:
    """🔴 **이력이 전부 걸리면 «없다»가 아니라 «못 한다»** (99 #194).

    ⚠ 04 §3.7 은 `suggestions: []` 를 «**인용 실존 게이트가 전량 드롭했다**» 로 정의한다 —
    «우리가 조립할 것이 없었다» 를 같은 모양으로 내면 **강사가 «이 학부모는 제안할 게
    없구나» 로 읽는다**(№63 §E ③ · №64 §E 가 세운 규율).

    ⚠ 🔴 **뒤집기가 이 검사의 부재를 알려 줬다**(8/24): «0건 처리를 빈 배열로 되돌린다» 가
    **green** 이었다 — 0건 경로를 재는 자리가 **하나도 없었다.**
    """
    blocked = _BLOCKED_TEXT
    if not _still_blocked(blocked):
        pytest.fail(f"{blocked!r} 가 이제 안 걸린다 — 이 검사가 재려는 대상이 사라졌다")
    #: 🔴 **전부 걸리는 이력** — 같은 문면 다섯이면 다섯 다 걸린다.
    history = _history_with(*([blocked] * 5))
    kept, dropped = keep_sendable_history(history)
    assert kept == () and len(dropped) == 5, "이 이력이 전부 걸리지 않는다 — 전제가 깨졌다"

    provider = GatewayLabelSuggestProvider(cast("Any", object()))
    with pytest.raises(LabelHistoryUnusable) as caught:
        await provider.suggest(
            guardian_ref="gd_1", history=history, context=_context()
        )
    assert caught.value.detail["reason"] == HISTORY_ALL_BLOCKED  # type: ignore[index]
    #: 🔴 **빈 배열이 아니다** — 그게 이 검사의 전부다.

# ── 🔴 중복 병합 (99 #204 · №70) ────────────────────────────────────


def _line(axis: str, value: str, confidence: str, record: str, quote: str) -> str:
    return f"{axis} | {value} | {confidence} | {record} | {quote}"


def test_duplicate_axes_are_merged_not_dropped() -> None:
    """🔴 같은 `(축, 값)` 셋이 **하나로 합쳐지고 인용은 다 남는다**(99 #204).

    ⚠ 🔴 **버리는 방식이 아니다** — 먼저 나온 것만 남기면 **근거가 사라지고**,
    confidence 최고만 남기면 **나머지 인용이 사라진다.**
    """
    parsed = merge_duplicate_axes(
        parse_suggestions(
            "\n".join(
            (
                _line("interest", "attitude", "0.5", "cm_88", "숫자로"),
                _line("interest", "attitude", "0.9", "cm_89", "점수 추이"),
                _line("interest", "attitude", "0.3", "cm_90", "표로"),
            )
            ),
            guardian_ref="gd_1",
        )
    )
    assert len(parsed) == 1, [s.label.value for s in parsed]
    assert len(parsed[0].evidence_quotes) == 3, "인용이 사라졌다 — 버리는 방식이다"
    #: 🔴 **최댓값**이다 — 평균이면 «우리가 재서 정한 값» 이 된다.
    assert parsed[0].confidence == 0.9


def test_an_identical_quote_is_not_duplicated_in_the_merge() -> None:
    """같은 `record_id` + 같은 문장이면 인용을 하나로 — 근거가 부풀지 않는다."""
    parsed = merge_duplicate_axes(
        parse_suggestions(
            "\n".join(
            (
                _line("comm", "data", "0.5", "cm_88", "숫자로"),
                _line("comm", "data", "0.7", "cm_88", "숫자로"),
            )
            ),
            guardian_ref="gd_1",
        )
    )
    assert len(parsed) == 1
    assert len(parsed[0].evidence_quotes) == 1, "같은 인용이 두 번 실렸다"


def test_different_axes_are_not_merged() -> None:
    """🔴 **과잉 병합 시험 — 앵커 폭이다.**

    ⚠ 이 검사가 없으면 «다 합치는 구현» 도 위 둘을 통과한다. 축이 다르거나 값이
    다르면 **각자 남아야** 한다.
    """
    parsed = merge_duplicate_axes(
        parse_suggestions(
            "\n".join(
            (
                _line("comm", "data", "0.5", "cm_88", "숫자로"),
                _line("interest", "grade", "0.6", "cm_89", "점수"),
                _line("comm", "narrative", "0.4", "cm_90", "이야기"),
            )
            ),
            guardian_ref="gd_1",
        )
    )
    assert len(parsed) == 3, [
        (s.label.axis, s.label.value) for s in parsed
    ]


def test_the_merge_keeps_first_appearance_order() -> None:
    """🔴 정렬하지 않는다 — **모델의 우선순위**가 사라진다."""
    parsed = merge_duplicate_axes(
        parse_suggestions(
            "\n".join(
            (
                _line("interest", "grade", "0.5", "cm_88", "점수"),
                _line("comm", "data", "0.5", "cm_89", "숫자로"),
                _line("interest", "grade", "0.9", "cm_90", "정답률"),
            )
            ),
            guardian_ref="gd_1",
        )
    )
    assert [s.label.axis for s in parsed] == ["interest", "comm"]


def test_the_contract_rejects_duplicates_that_slipped_past_the_merge() -> None:
    """🔴 계약이 **앞 단계가 깨진 것**을 잡는다 — 그 자리는 500 이 맞다.

    ⚠ 모델 출력이 아니라 **우리 병합**이 안 돈 신호다(조립부가 먼저 합친다).
    """
    duplicated = SuggestedLabel(
        suggestion_id=UUID("00000000-0000-4000-8000-00000000b001"),
        guardian_ref="gd_1",
        label=LabelSuggestion(axis="comm", value="data"),
        confidence=0.5,
        evidence_quotes=(EvidenceQuote(record_id="cm_88", quote="숫자로"),),
    )
    with pytest.raises(ValidationError):
        LabelSuggestResponse(suggestions=(duplicated, duplicated))

def test_a_forged_quote_does_not_take_the_true_ones_with_it() -> None:
    """🔴 **이 검사가 「병합이 게이트 뒤」라는 순서 자체를 문다** (99 #206 · №71).

    ⚠ 🔴 **병합을 게이트 앞으로 되돌리면 red 다** — 그게 이 검사의 존재 이유다.
    앞에 두면 병합된 제안이 인용 셋을 들고, 게이트는 «하나라도 실패하면 전체 드롭» 이라
    **진짜 인용 둘이 지어낸 하나에 딸려 사라진다**(불변식 2 의 반대편).

    ⚠ 입력: 같은 `(interest, grade)` **셋** 중 **하나만** `record_id` 가 없는 것을 가리킨다.
    ⇒ 순서가 맞으면 **거짓 하나만 떨어지고 진짜 둘이 합쳐져 나온다**.
    """
    history = _history_with(
        "숫자로 정리해 주세요",
        "점수 추이 표로 부탁드려요",
        "지난주 결과가 궁금합니다",
        "표로 보여 주시면 좋겠어요",
        "이번 달 통계도 알려 주세요",
    )
    parsed = parse_suggestions(
        "\n".join(
            (
                _line("interest", "grade", "0.5", "cm_88", "숫자로 정리해 주세요"),
                #: 🔴 **없는 record_id** — 게이트가 이 제안만 버려야 한다.
                _line("interest", "grade", "0.9", "cm_없음", "지어낸 인용"),
                _line("interest", "grade", "0.7", "cm_89", "점수 추이 표로 부탁드려요"),
            )
        ),
        guardian_ref="gd_1",
    )
    #: ① 파서는 **안 합친다** — 셋이 그대로 나온다(그래야 게이트가 하나씩 판정한다).
    assert len(parsed) == 3, "파서가 합쳤다 — 병합이 다시 앞으로 갔다"

    grounded = ground_suggestions(parsed, history=history)
    assert len(grounded.suggestions) == 2, (
        f"진짜 인용 둘이 지어낸 하나에 딸려 사라졌다: {len(grounded.suggestions)}개 남음"
    )

    merged = merge_duplicate_axes(grounded.suggestions)
    assert len(merged) == 1, "게이트 통과분이 안 합쳐졌다"
    assert len(merged[0].evidence_quotes) == 2, "합친 뒤 인용이 둘이어야 한다"
    assert merged[0].confidence == 0.7, "게이트가 버린 0.9 가 confidence 에 남았다"

