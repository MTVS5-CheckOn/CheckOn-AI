"""결정론 프로파일링 — xlsx·csv → source_profile (10_import_spec §3.1).

순수 집계(LLM 0). **실명 무접촉 가드레일(§5.2, 구조적):**
- 컬럼 값(원문)은 profile 어디에도 저장하지 않는다 — 통계(개수·타입·길이)만.
- 인명·연락처 의심 컬럼(masking §4)은 통계에서도 값 파생 금지.
- 샘플 행은 **redactor 주입 시에만** 보관한다. 이 브랜치는 redactor를 주입하지 않으므로
  sample_rows는 항상 비어 있다(구조적). 실 redactor(runtime/redaction.py — feat/signal-brief-v1)
  주입·샘플 보관 활성은 후속 커밋.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from typing import Protocol

import pandas as pd  # type: ignore[import-untyped]


class _Redacted(Protocol):
    """redaction 결과의 구조적 계약 — runtime/redaction.RedactionResult와 호환."""

    masked_text: str
    uncertain: bool


#: 샘플 셀을 마스킹하는 주입점 — 실 엔진(runtime/redaction.redact)이 이 시그니처를 만족한다.
Redactor = Callable[[str], _Redacted]

#: 인명·연락처 의심 헤더(masking §4 — 값 대신 통계만). 오탐 감수·미탐 최소화 방향.
_SUSPECT_HEADER_RE = re.compile(
    r"이름|성명|성함|학생명|원생명|보호자|학부모|연락처|전화|휴대폰|핸드폰|주소|이메일|email|phone|tel|addr",
    re.IGNORECASE,
)

#: 날짜 문자열 판별(구분자 필수) — 숫자 컬럼이 epoch로 오탐되는 것을 막는다.
_DATE_RE = re.compile(r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,2}")


class ProfilingError(Exception):
    """파일 판독 실패 — 라우터가 status=failed로 수렴시킨다(HTTP 아님, error_codes §2.4)."""


@dataclass(frozen=True)
class ColumnProfile:
    """한 컬럼의 통계 — 원문 값은 담지 않는다(가드레일)."""

    name: str
    n_total: int
    n_null: int
    n_unique: int
    dtype_guess: str
    """int·float·date·bool·string·mixed·empty — pandas dtype + 휴리스틱."""

    suspect_pii: bool
    """인명·연락처 의심 — 값 파생 금지(masking §4)."""


@dataclass(frozen=True)
class SheetProfile:
    name: str
    n_rows: int
    columns: tuple[ColumnProfile, ...]
    sample_rows: tuple[dict[str, str], ...]
    """redactor 주입 시에만 채워진다(§5.2). 미주입이면 ()."""


@dataclass(frozen=True)
class SourceProfile:
    filename: str
    sheets: tuple[SheetProfile, ...]


def _dtype_guess(series: pd.Series) -> str:
    non_null = series.dropna()
    if non_null.empty:
        return "empty"
    if pd.api.types.is_bool_dtype(non_null):
        return "bool"
    if pd.api.types.is_integer_dtype(non_null):
        return "int"
    if pd.api.types.is_float_dtype(non_null):
        return "float"
    if pd.api.types.is_datetime64_any_dtype(non_null):
        return "date"
    # object/문자열 — 구분자 있는 날짜 패턴만 date(숫자 문자열 오탐 방지). 값은 보관 안 함.
    text = non_null.astype(str)
    if bool(text.str.contains(_DATE_RE).all()):
        return "date"
    return "string"


def _column_profile(name: str, series: pd.Series) -> ColumnProfile:
    return ColumnProfile(
        name=str(name),
        n_total=int(series.size),
        n_null=int(series.isna().sum()),
        n_unique=int(series.nunique(dropna=True)),
        dtype_guess=_dtype_guess(series),
        suspect_pii=bool(_SUSPECT_HEADER_RE.search(str(name))),
    )


def _sample_rows(
    frame: pd.DataFrame,
    columns: tuple[ColumnProfile, ...],
    max_rows: int,
    redactor: Redactor | None,
) -> tuple[dict[str, str], ...]:
    """샘플 행 — redactor 미주입이면 빈 튜플(§5.2 구조적). 주입 시 의심 컬럼 제외 + 마스킹."""
    if redactor is None:
        return ()
    safe_cols = [c.name for c in columns if not c.suspect_pii]  # 의심 컬럼은 값 미노출
    rows: list[dict[str, str]] = []
    for _, row in frame.head(max_rows).iterrows():
        masked: dict[str, str] = {}
        for col in safe_cols:
            outcome = redactor("" if pd.isna(row[col]) else str(row[col]))
            if outcome.uncertain:  # fail-closed — 불확실하면 그 셀 미보관
                continue
            masked[col] = outcome.masked_text
        rows.append(masked)
    return tuple(rows)


def _sheets_from_bytes(data: bytes, filename: str) -> dict[str, pd.DataFrame]:
    lower = filename.lower()
    try:
        if lower.endswith((".xlsx", ".xlsm", ".xls")):
            sheets = pd.read_excel(BytesIO(data), sheet_name=None)
            return {str(name): frame for name, frame in sheets.items()}
        if lower.endswith(".csv"):
            return {"sheet1": pd.read_csv(BytesIO(data))}
    except (ValueError, OSError, pd.errors.ParserError) as exc:
        raise ProfilingError(f"파일 판독 실패: {filename}") from exc
    raise ProfilingError(f"미지원 형식: {filename} (xlsx·csv만)")


def profile_source(
    data: bytes,
    filename: str,
    *,
    max_rows: int,
    redactor: Redactor | None = None,
) -> SourceProfile:
    """파일 바이트 → SourceProfile. 값 원문 미보관(가드레일). 실패는 ProfilingError."""
    frames = _sheets_from_bytes(data, filename)
    sheets: list[SheetProfile] = []
    for name, frame in frames.items():
        columns = tuple(_column_profile(col, frame[col]) for col in frame.columns)
        sheets.append(
            SheetProfile(
                name=name,
                n_rows=int(len(frame)),
                columns=columns,
                sample_rows=_sample_rows(frame, columns, max_rows, redactor),
            )
        )
    return SourceProfile(filename=filename, sheets=tuple(sheets))


def source_columns(profile: SourceProfile) -> tuple[str, ...]:
    """전 시트의 컬럼명(중복 제거·등장 순서) — 매핑 추론·폴백의 대상."""
    seen: dict[str, None] = {}
    for sheet in profile.sheets:
        for col in sheet.columns:
            seen.setdefault(col.name, None)
    return tuple(seen)
