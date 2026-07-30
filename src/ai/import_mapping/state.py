"""Import 상태기계 — 10_import_spec §2(04 §3.8 정본) 전이 규칙.

순수 규칙 테이블(LLM·I/O 없음). 전이 규칙·종단 상태를 값으로 고정해 회귀를 막는다.
상태 자체와 문자열 값은 `contracts.imports.ImportStatus`가 원본이다(여기선 전이만 정의).
"""

from __future__ import annotations

from ai.contracts.imports import ImportStatus

S = ImportStatus

#: 상태별 허용 전이(10_import_spec §2 그림). PROFILING→PREVIEW_READY는 캐시 hit(reused) 직행.
#: transforming은 없다 — 전체 행 변환이 백엔드 소유가 됐다(§4, 2026-07-30). AI 종단은 확정 spec.
ALLOWED_TRANSITIONS: dict[ImportStatus, frozenset[ImportStatus]] = {
    S.PROFILING: frozenset({S.INFERRING, S.PREVIEW_READY, S.FAILED}),
    S.INFERRING: frozenset({S.PROBING, S.PREVIEW_READY, S.BLOCKED, S.FAILED}),
    S.PROBING: frozenset({S.PREVIEW_READY, S.BLOCKED, S.FAILED}),
    S.PREVIEW_READY: frozenset({S.DONE, S.BLOCKED, S.FAILED}),
    S.BLOCKED: frozenset({S.PREVIEW_READY, S.FAILED}),  # override로 필수 채우면 blocked 해제
    S.DONE: frozenset(),
    S.FAILED: frozenset(),
}

#: 종단 상태 — 더 전이하지 않는다. BLOCKED는 종단이 아니다(override로 해제 가능).
TERMINAL: frozenset[ImportStatus] = frozenset({S.DONE, S.FAILED})


class InvalidStateTransition(Exception):
    """허용되지 않은 상태 전이 — 내부 불변 위반(HTTP 아님). 라우터 조립 버그를 조기에 잡는다."""


def can_transition(frm: ImportStatus, to: ImportStatus) -> bool:
    """frm→to가 §2 규칙에서 허용되는가."""
    return to in ALLOWED_TRANSITIONS[frm]


def assert_transition(frm: ImportStatus, to: ImportStatus) -> None:
    """허용 전이가 아니면 InvalidStateTransition — 오케스트레이터가 규칙 밖으로 못 가게."""
    if not can_transition(frm, to):
        raise InvalidStateTransition(f"{frm.value} → {to.value} 는 허용되지 않는 전이")
