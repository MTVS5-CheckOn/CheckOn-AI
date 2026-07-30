"""결정론 프로파일링 — xlsx·csv → source_profile (10_import_spec §3.1).

순수 집계(LLM 0).

**파일 구조 주의사항(§1.2 `structure_notices`):** `source_columns()`가 중복 헤더를 하나로
합치는 것은 매핑 추론 대상 목록이라 의도된 동작이고, **삼킨 사실을 알리지 않는 것**이
문제였다(백엔드 요청, 2026-07-30). 두 관심사를 분리해 합치기는 그대로 두고, 합쳐진·비어 있는
헤더를 `SheetProfile.notices`로 드러낸다. 원본 헤더 행을 따로 읽는 이유는 pandas가
중복을 `점수.1`로, 빈 헤더를 `Unnamed: 2`로 **개명해 버려** 프레임 컬럼만으로는 판별이
불가능하기 때문이다(실측).

**실명 무접촉 가드레일(§5.2, 구조적):**
- 컬럼 값(원문)은 profile 어디에도 저장하지 않는다 — 통계(개수·타입·길이)만.
- 인명·연락처 의심 컬럼(masking §4)은 통계에서도 값 파생 금지.
- 샘플 행은 **redactor 주입 시에만** 보관한다. 이 브랜치는 redactor를 주입하지 않으므로
  sample_rows는 항상 비어 있다(구조적). 실 redactor(runtime/redaction.py — feat/signal-brief-v1)
  주입·샘플 보관 활성은 후속 커밋.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from io import BytesIO
from typing import Protocol

import pandas as pd  # type: ignore[import-untyped]

from ai.contracts.imports import StructureNotice, StructureNoticeKind


class _Redacted(Protocol):
    """redaction 결과의 구조적 계약 — runtime/redaction.RedactionResult와 호환.

    읽기 전용 속성으로 선언한다(property) — frozen 모델(RedactionResult)도 만족하도록.
    소비자는 masked_text·uncertain을 읽기만 한다.
    """

    @property
    def masked_text(self) -> str: ...

    @property
    def uncertain(self) -> bool: ...


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
class SheetNotice:
    """이 시트의 구조 주의사항 1건 — 시트명은 SheetProfile이 이미 안다(평탄화 시 부착).

    ⚠ 셀 값은 담지 않는다(§5.2) — 헤더 이름과 위치만.
    """

    kind: StructureNoticeKind
    column_index: int
    """원본의 1-based 컬럼 위치(좌→우)."""

    header: str
    """헤더 텍스트. empty_header면 빈 문자열."""


@dataclass(frozen=True)
class SheetProfile:
    name: str
    n_rows: int
    columns: tuple[ColumnProfile, ...]
    sample_rows: tuple[dict[str, str], ...]
    """redactor 주입 시에만 채워진다(§5.2). 미주입이면 ()."""

    notices: tuple[SheetNotice, ...] = ()
    """파일 구조 주의사항 — 매핑 대상 목록에서 삼킨 사실을 드러낸다(§1.2)."""


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


def _header_text(cell: object) -> str:
    """헤더 셀 → 정규화 텍스트. 결측·공백만이면 ''(빈 헤더)."""
    if cell is None or (isinstance(cell, float) and pd.isna(cell)):
        return ""
    return str(cell).strip()


def _sheet_notices(
    raw_header: Sequence[object],
    columns: tuple[ColumnProfile, ...],
    n_rows: int,
) -> tuple[SheetNotice, ...]:
    """원본 헤더 행 + 컬럼 통계 → 구조 주의사항(순수 함수).

    **컬럼 하나당 최대 1건**이고 우선순위는 빈 헤더 > 중복 헤더 > 데이터 없음이다 —
    같은 컬럼에 사유가 겹칠 때 목록이 부풀지 않게 한다(참고정보의 신뢰도).
    """
    texts = [_header_text(cell) for cell in raw_header]
    duplicated = {text for text, count in Counter(t for t in texts if t).items() if count > 1}
    # 프레임 컬럼과 원본 헤더는 좌→우 1:1이다. 개수가 어긋나면(예상 밖) 통계 기반 항목은 건다.
    aligned = len(texts) == len(columns)

    notices: list[SheetNotice] = []
    for index, text in enumerate(texts, start=1):
        if not text:
            notices.append(SheetNotice(StructureNoticeKind.EMPTY_HEADER, index, ""))
        elif text in duplicated:
            notices.append(SheetNotice(StructureNoticeKind.DUPLICATE_HEADER, index, text))
        elif aligned and n_rows > 0:
            column = columns[index - 1]
            if column.n_total > 0 and column.n_null == column.n_total:
                notices.append(
                    SheetNotice(StructureNoticeKind.HEADER_WITHOUT_DATA, index, text)
                )
    return tuple(notices)


def _raw_headers(data: bytes, filename: str) -> dict[str, tuple[object, ...]]:
    """헤더 행을 **개명 전 원본 그대로** 읽는다 — 중복·빈 헤더 판별의 유일한 근거.

    `_sheets_from_bytes`와 같은 시트 키 규칙을 쓴다(csv = 'sheet1').
    """
    lower = filename.lower()
    try:
        if lower.endswith((".xlsx", ".xlsm", ".xls")):
            frames = pd.read_excel(BytesIO(data), sheet_name=None, header=None, nrows=1)
            return {
                str(name): tuple(frame.iloc[0]) if not frame.empty else ()
                for name, frame in frames.items()
            }
        if lower.endswith(".csv"):
            frame = pd.read_csv(BytesIO(data), header=None, nrows=1)
            return {"sheet1": tuple(frame.iloc[0]) if not frame.empty else ()}
    except (ValueError, OSError, pd.errors.ParserError) as exc:
        raise ProfilingError(f"헤더 판독 실패: {filename}") from exc
    raise ProfilingError(f"미지원 형식: {filename} (xlsx·csv만)")


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
    raw_headers = _raw_headers(data, filename)
    sheets: list[SheetProfile] = []
    for name, frame in frames.items():
        columns = tuple(_column_profile(col, frame[col]) for col in frame.columns)
        n_rows = int(len(frame))
        sheets.append(
            SheetProfile(
                name=name,
                n_rows=n_rows,
                columns=columns,
                sample_rows=_sample_rows(frame, columns, max_rows, redactor),
                notices=_sheet_notices(raw_headers.get(name, ()), columns, n_rows),
            )
        )
    return SourceProfile(filename=filename, sheets=tuple(sheets))


def source_columns(profile: SourceProfile) -> tuple[str, ...]:
    """전 시트의 컬럼명(중복 제거·등장 순서) — 매핑 추론·폴백의 대상.

    **중복 제거는 의도된 동작이다** — 추론 대상 목록에 같은 이름이 두 번 들어가면 안 된다.
    합쳐진 사실은 `structure_notices()`가 알린다(관심사 분리, §1.2).
    """
    seen: dict[str, None] = {}
    for sheet in profile.sheets:
        for col in sheet.columns:
            seen.setdefault(col.name, None)
    return tuple(seen)


def structure_notices(profile: SourceProfile) -> tuple[StructureNotice, ...]:
    """전 시트의 구조 주의사항(등장 순서) — 계약 노출용으로 **시트명을 부착**한다.

    `source_columns()`와 같은 평탄화 선례를 따른다. 다중 시트에서 강사가 원본을 찾으려면
    "어느 시트의 어느 컬럼"이 필요하다(§1.2).
    """
    return tuple(
        StructureNotice(
            sheet=sheet.name,
            kind=notice.kind,
            column_index=notice.column_index,
            header=notice.header,
        )
        for sheet in profile.sheets
        for notice in sheet.notices
    )
