"""Import 상태기계 — 전이 규칙·종단·재시도 상한을 값 대조로 고정 (10_import_spec §2).

기대 전이표는 손 작성이다(코드에서 재생성 금지) — §2 그림이 회귀 없이 유지되는지 검증.
"""

from __future__ import annotations

import pytest

from ai.contracts.imports import ImportStatus as S
from ai.import_mapping.settings import ImportSettings
from ai.import_mapping.state import (
    ALLOWED_TRANSITIONS,
    TERMINAL,
    InvalidStateTransition,
    assert_transition,
    can_transition,
)

#: 10_import_spec §2 그림을 손으로 옮긴 기대표 — 이 값이 곧 계약이다.
#: ⚠ **같은 한계다** — `ALL_TRANSITIONS`(코드) ↔ 손사본이라 **그림이 바뀌면 조용하다**
#:   (99 #07). 코드가 바뀌면 red가 나는 방향만 지킨다.
_EXPECTED: dict[S, set[S]] = {
    S.PROFILING: {S.INFERRING, S.PREVIEW_READY, S.FAILED},
    S.INFERRING: {S.PROBING, S.PREVIEW_READY, S.FAILED},
    S.PROBING: {S.PREVIEW_READY, S.FAILED},
    S.PREVIEW_READY: {S.DONE, S.FAILED},
    S.DONE: set(),
    S.FAILED: set(),
}


def test_transition_table_matches_spec() -> None:
    assert {k: set(v) for k, v in ALLOWED_TRANSITIONS.items()} == _EXPECTED


def test_all_states_have_a_transition_entry() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(S)


def test_terminal_states_are_done_and_failed() -> None:
    assert TERMINAL == frozenset({S.DONE, S.FAILED})
    assert not ALLOWED_TRANSITIONS[S.DONE] and not ALLOWED_TRANSITIONS[S.FAILED]


def test_no_blocked_state_exists() -> None:
    """확정 차단이 백엔드로 이관돼 blocked가 사라졌다(2026-07-30 확정).

    구 `test_blocked_is_not_terminal_and_can_be_released`의 자리 — 상태 자체가 없어졌으므로
    "해제 가능"이 아니라 "존재하지 않음"을 단정한다.
    """
    assert "blocked" not in {state.value for state in S}
    assert all(S.PREVIEW_READY in ALLOWED_TRANSITIONS[s] or s in TERMINAL or s is S.PREVIEW_READY
               for s in (S.INFERRING, S.PROBING))  # 수렴은 항상 preview_ready로


def test_cache_hit_shortcut_profiling_to_preview() -> None:
    assert can_transition(S.PROFILING, S.PREVIEW_READY)  # reused(§3.4)


def test_no_transforming_state_exists() -> None:
    """전체 행 변환은 백엔드 소유(10 §4, 2026-07-30) — AI 상태기계에 transforming이 없다."""
    assert "transforming" not in {state.value for state in S}


def test_assert_transition_rejects_illegal() -> None:
    assert not can_transition(S.DONE, S.PREVIEW_READY)
    with pytest.raises(InvalidStateTransition):
        assert_transition(S.PROFILING, S.DONE)  # 프로파일링에서 바로 완료 불가


def test_retry_cap_default_is_five() -> None:
    """조사 루프 상한 기본값 5 고정(불변식 6). 값이 바뀌면 이 테스트가 잡는다."""
    assert ImportSettings().import_probe_loop_max == 5
