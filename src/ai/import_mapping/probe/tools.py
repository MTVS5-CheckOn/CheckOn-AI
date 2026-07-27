"""조사 도구 — 계약 Protocol + 결정론 Fake (langgraph_state §2.1 · 01_pipeline §3).

도구 3종은 전부 **결정론 코드**이고 **마스킹 레이어 뒤**에 있다 — 에이전트(플래너)가 아무리
조사해도 실명·연락처는 볼 수 없다(구조적 차단, §5.2). 반환은 마스킹 통과 통계·문자열뿐.
실 도구(실 파일 조회)는 후속 — 이 골격은 SourceProfile(이미 마스킹된 통계)에서 파생한다.
"""

from __future__ import annotations

from typing import Protocol

from ai.import_mapping.probe.state import ProbeToolName
from ai.import_mapping.profiling import SourceProfile


class ProbeTool(Protocol):
    """조사 도구 인터페이스 — 반환은 전부 마스킹 통과분(문자열)."""

    def get_unique_values(self, column: str) -> str: ...

    def get_more_sample(self, sheet: str, rows: int) -> str: ...

    def check_join_key(self, sheet_a: str, sheet_b: str) -> str: ...


TOOL_NAMES: tuple[ProbeToolName, ...] = (
    "get_unique_values",
    "get_more_sample",
    "check_join_key",
)


class FakeProbeTools:
    """결정론 Fake — SourceProfile 통계에서 마스킹 관찰을 파생한다(원본 값 미접근)."""

    def __init__(self, profile: SourceProfile) -> None:
        self._profile = profile
        self._columns = {
            col.name: col for sheet in profile.sheets for col in sheet.columns
        }
        self._sheet_cols = {
            sheet.name: {c.name for c in sheet.columns} for sheet in profile.sheets
        }

    def get_unique_values(self, column: str) -> str:
        col = self._columns.get(column)
        if col is None:
            return "컬럼 없음"
        if col.suspect_pii:  # 인명·연락처 후보 → 값 대신 통계만(masking §4)
            return f"값 미노출(인명 후보) · 유니크 {col.n_unique}개 · 한글 2~3자 추정"
        return f"유니크 {col.n_unique}개 · {col.dtype_guess} · 결측 {col.n_null}"

    def get_more_sample(self, sheet: str, rows: int) -> str:
        cols = self._sheet_cols.get(sheet)
        if cols is None:
            return "시트 없음"
        return f"샘플 {rows}행(마스킹 통과분) · 컬럼 {len(cols)}개"

    def check_join_key(self, sheet_a: str, sheet_b: str) -> str:
        a, b = self._sheet_cols.get(sheet_a), self._sheet_cols.get(sheet_b)
        if a is None or b is None:
            return "시트 없음"
        shared = sorted(a & b)
        if not shared:
            return "공통 컬럼 없음 — 조인키 미확정"
        return f"조인 후보 {shared[0]} · 일치율만 반환(값 미노출)"
