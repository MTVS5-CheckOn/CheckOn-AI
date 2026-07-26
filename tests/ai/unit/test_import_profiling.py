"""결정론 프로파일링 + 실명 무접촉 가드레일 (10_import_spec §3.1·§5.2).

가드레일 검증은 구조로: redactor 미주입이면 샘플 자체가 비고, 인명·연락처 의심 컬럼은
값이 profile 어디에도 남지 않는다(통계만). 원문 실명·전화 문자열 잔존 0을 고정.
"""

from __future__ import annotations

from io import BytesIO

import pandas as pd  # type: ignore[import-untyped]

from ai.import_mapping.profiling import ProfilingError, profile_source, source_columns

# 실명·연락처가 섞인 타사 엑셀(강사 원본) — 이 값들이 profile에 새면 안 된다.
_NAMES = ("김철수", "이영희", "박민수")
_PHONES = ("010-1234-5678", "010-2222-3333", "010-4444-5555")


def _xlsx_bytes() -> bytes:
    frame = pd.DataFrame(
        {
            "원생명": list(_NAMES),
            "연락처": list(_PHONES),
            "점수A": [88, 91, 73],
            "제출일": ["2026-06-01", "2026-06-08", "2026-06-15"],
        }
    )
    buffer = BytesIO()
    frame.to_excel(buffer, index=False)
    return buffer.getvalue()


def _csv_bytes() -> bytes:
    return "이름,점수\n김철수,88\n이영희,91\n".encode()


def test_profile_xlsx_basic_stats() -> None:
    profile = profile_source(_xlsx_bytes(), "roster.xlsx", max_rows=20)
    assert profile.filename == "roster.xlsx"
    (sheet,) = profile.sheets
    assert sheet.n_rows == 3
    by_name = {c.name: c for c in sheet.columns}
    assert by_name["점수A"].dtype_guess == "int"
    assert by_name["제출일"].dtype_guess == "date"
    assert by_name["점수A"].n_unique == 3


def test_suspect_columns_flagged() -> None:
    profile = profile_source(_xlsx_bytes(), "roster.xlsx", max_rows=20)
    by_name = {c.name: c for c in profile.sheets[0].columns}
    assert by_name["원생명"].suspect_pii is True
    assert by_name["연락처"].suspect_pii is True
    assert by_name["점수A"].suspect_pii is False


def test_guardrail_no_sample_without_redactor() -> None:
    """redactor 미주입 → 샘플 비어 있음(§5.2 구조적)."""
    profile = profile_source(_xlsx_bytes(), "roster.xlsx", max_rows=20)
    assert profile.sheets[0].sample_rows == ()


def test_guardrail_no_realname_anywhere() -> None:
    """원문 실명·전화가 profile 어디에도 잔존하지 않는다(가드레일 핵심)."""
    profile = profile_source(_xlsx_bytes(), "roster.xlsx", max_rows=20)
    blob = repr(profile)
    for leaked in (*_NAMES, *_PHONES):
        assert leaked not in blob, f"실명/연락처 잔존: {leaked}"


def test_csv_single_sheet() -> None:
    profile = profile_source(_csv_bytes(), "f.csv", max_rows=20)
    assert source_columns(profile) == ("이름", "점수")
    assert profile.sheets[0].sample_rows == ()


def test_unreadable_file_raises_profiling_error() -> None:
    try:
        profile_source(b"not a spreadsheet", "broken.xlsx", max_rows=20)
    except ProfilingError:
        return
    raise AssertionError("손상 파일은 ProfilingError여야 한다")
