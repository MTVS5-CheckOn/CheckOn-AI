"""🔴 이력 순서가 프롬프트를 흔들지 않는다 — 99 #253 · №109.

🔴 **LLM 출력으로 재지 않는다** — 비결정성 때문에 아무것도 증명 못 한다.
⇒ **조립된 프롬프트 문자열을 직접 대조**한다(바이트 동일).
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ai.composition.labels.grounding import keep_sendable_history
from ai.composition.labels.prompt import assemble_prompt, render_history_block
from ai.contracts.labels import MAX_HISTORY, HistoryItem

_BASE = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)


def _item(index: int, *, at: datetime | None = None, text: str | None = None) -> HistoryItem:
    return HistoryItem(
        record_id=f"rec_{index:02d}",
        direction="inbound" if index % 2 else "outbound",
        text=text or f"지난주 과제를 {index}번 확인했습니다",
        at=at if at is not None else _BASE + timedelta(hours=index),
    )


def _ten() -> tuple[HistoryItem, ...]:
    return tuple(_item(i) for i in range(MAX_HISTORY))


def test_any_input_order_gives_the_same_prompt() -> None:
    """🔴 ① 같은 10건을 섞어 넣으면 **바이트 동일**한가 — 순열 여러 가지."""
    items = _ten()
    expected = assemble_prompt(items)
    shuffles = (
        tuple(reversed(items)),
        items[5:] + items[:5],
        items[::2] + items[1::2],
        (items[-1], *items[1:-1], items[0]),
        tuple(sorted(items, key=lambda i: i.record_id, reverse=True)),
    )
    for order in shuffles:
        assert set(order) == set(items)
        assert assemble_prompt(order) == expected, order[0].record_id


def test_the_prompt_reads_oldest_first() -> None:
    """정렬 **방향**까지 물어 둔다 — 이유는 `render_history_block` docstring."""
    block = render_history_block(tuple(reversed(_ten())))
    assert block.index("rec_00") < block.index("rec_09")


def test_identical_timestamps_still_have_one_order() -> None:
    """🔴 ② `at` 이 **전부 같을** 때 — 보조 키(`record_id`)가 무나."""
    same = tuple(_item(i, at=_BASE) for i in range(MAX_HISTORY))
    expected = assemble_prompt(same)
    for order in itertools.islice(itertools.permutations(same), 0, 24, 5):
        assert assemble_prompt(order) == expected
    block = render_history_block(tuple(reversed(same)))
    assert block.index("rec_00") < block.index("rec_09")


def test_naive_and_aware_timestamps_do_not_crash() -> None:
    """⚠ 🔴 계약이 naive 를 막지 않는다 — 섞여도 `TypeError` 로 죽지 않는다."""
    mixed = (
        _item(0, at=datetime(2026, 8, 1, 12, 0)),
        _item(1, at=datetime(2026, 8, 1, 9, 0, tzinfo=ZoneInfo("Asia/Seoul"))),
        _item(2, at=datetime(2026, 8, 1, 6, 0, tzinfo=UTC)),
    )
    expected = assemble_prompt(mixed)
    assert assemble_prompt(tuple(reversed(mixed))) == expected
    #: naive 는 `Asia/Seoul` 로 읽으므로 09:00 KST < 12:00 KST · 06:00 UTC = 15:00 KST
    block = render_history_block(mixed)
    assert block.index("rec_01") < block.index("rec_00") < block.index("rec_02")


def test_masking_drops_do_not_disturb_the_order() -> None:
    """🔴 ④ 마스킹으로 **일부가 빠져도** 남은 것의 순서가 유지되나."""
    with_pii = (
        _item(0),
        _item(1, text="010-1234-5678 로 연락 주세요"),
        _item(2),
        _item(3, text="이메일 parent@example.com 으로 보냈습니다"),
        _item(4),
    )
    sendable, dropped = keep_sendable_history(with_pii)
    assert dropped, "마스킹이 아무것도 안 걸렀다 — 이 검사가 재는 것이 없어진다"
    expected = assemble_prompt(sendable)
    assert assemble_prompt(tuple(reversed(sendable))) == expected
    kept = [item.record_id for item in sendable]
    block = render_history_block(tuple(reversed(sendable)))
    assert [r for r in kept] == sorted(kept), "남은 것이 이미 정렬돼 있어야 비교가 선다"
    positions = [block.index(record_id) for record_id in kept]
    assert positions == sorted(positions)


def test_the_timestamp_itself_is_not_in_the_prompt() -> None:
    """🔴 §2-3 — 정렬에는 쓰되 **프롬프트에는 안 싣는다**(불변식 3 의 방향)."""
    items = _ten()
    block = render_history_block(items)
    for item in items:
        assert item.at.isoformat() not in block
        assert item.at.strftime("%Y-%m-%d") not in block
        assert item.direction not in block
