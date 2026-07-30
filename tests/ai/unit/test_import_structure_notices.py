"""파일 구조 주의사항 감지 — 중복·빈 헤더·데이터 없는 헤더 (10_import_spec §1.2).

백엔드 요청(2026-07-30): "AI가 파일 구조를 어떻게 해석했는지 강사가 검토할 참고정보"로
중복·빈 헤더를 알려달라. 기존 `source_columns()`는 중복을 **조용히 하나로 합쳤다** —
합치는 동작 자체는 유지하고(매핑 추론 대상 목록에 중복이 있으면 안 된다) 합친 사실을
알린다. 두 관심사가 분리됐는지를 여기서 고정한다.

픽스처는 openpyxl로 **헤더 셀을 직접** 쓴다 — pandas DataFrame 경유로는 중복 헤더를
파일에 만들 수 없고(개명됨), 이 테스트가 검증하려는 것이 바로 그 개명 이전의 원본이다.
"""

from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook  # type: ignore[import-untyped]

from ai.contracts.imports import MappingPreview, StructureNoticeKind
from ai.import_mapping.profiling import (
    SourceProfile,
    profile_source,
    source_columns,
    structure_notices,
)

_K = StructureNoticeKind


def _xlsx(sheets: dict[str, list[list[object]]]) -> bytes:
    """시트명 → 행 목록(첫 행이 헤더)을 그대로 쓴다."""
    book = Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(title=name)
        for row in rows:
            sheet.append(row)
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


#: 중복 헤더 2건(`점수`가 5·6열) · 빈 헤더 1건(7열) · 데이터 없는 헤더 1건(`비고` 8열).
_MESSY = _xlsx(
    {
        "1학기": [
            ["원생명", "반", "등원일", "상태", "점수", "점수", None, "비고"],
            ["김철수", "A1", "2026-03-02", "재원", 88, 91, None, None],
            ["이영희", "A1", "2026-03-02", "재원", 73, 80, None, None],
        ]
    }
)

_CLEAN = _xlsx(
    {
        "명부": [
            ["원생명", "반", "등원일", "상태"],
            ["김철수", "A1", "2026-03-02", "재원"],
        ]
    }
)


def _profile(data: bytes, filename: str = "roster.xlsx") -> SourceProfile:
    return profile_source(data, filename, max_rows=20)


# ── 감지 3종 ──────────────────────────────────────────────────────


def test_detects_duplicate_headers() -> None:
    """같은 헤더 2회 — **등장한 위치를 모두** 알린다(강사가 원본에서 둘 다 찾아야 한다)."""
    notices = structure_notices(_profile(_MESSY))
    duplicates = [n for n in notices if n.kind is _K.DUPLICATE_HEADER]
    assert [(n.column_index, n.header) for n in duplicates] == [(5, "점수"), (6, "점수")]


def test_detects_empty_header() -> None:
    notices = structure_notices(_profile(_MESSY))
    empty = [n for n in notices if n.kind is _K.EMPTY_HEADER]
    assert [(n.column_index, n.header) for n in empty] == [(7, "")]


def test_detects_header_without_data() -> None:
    """헤더는 있으나 값이 전량 결측 — 헤더-데이터 어긋남의 결정론 판별 가능 범위."""
    notices = structure_notices(_profile(_MESSY))
    empty_data = [n for n in notices if n.kind is _K.HEADER_WITHOUT_DATA]
    assert [(n.column_index, n.header) for n in empty_data] == [(8, "비고")]


def test_clean_file_has_no_notices() -> None:
    assert structure_notices(_profile(_CLEAN)) == ()
    assert all(sheet.notices == () for sheet in _profile(_CLEAN).sheets)


# ── 위치 정보·시트명 ──────────────────────────────────────────────


def test_notice_carries_sheet_name_for_each_sheet() -> None:
    """다중 시트 — 평탄화 목록의 항목마다 어느 시트인지 드러난다."""
    data = _xlsx(
        {
            "1학기": [["점수", "점수"], [1, 2]],
            # 빈 헤더는 **중간 열**에 둔다 — 맨 끝 빈 열은 xlsx에 아예 저장되지 않는다(실측).
            "2학기": [["반", None, "등원일"], ["A1", None, "2026-03-02"]],
        }
    )
    notices = structure_notices(_profile(data))
    assert {(n.sheet, n.kind) for n in notices} == {
        ("1학기", _K.DUPLICATE_HEADER),
        ("2학기", _K.EMPTY_HEADER),
    }


def test_column_index_is_one_based() -> None:
    """1-based 좌→우 위치 — 강사가 원본에서 세어 찾는 기준(0-based 아님)."""
    notices = structure_notices(_profile(_MESSY))
    assert min(n.column_index for n in notices) >= 1
    assert 5 in {n.column_index for n in notices}  # 5번째 컬럼 = 첫 `점수`


def test_one_notice_per_column() -> None:
    """사유가 겹쳐도 컬럼당 1건 — 참고정보 목록이 부풀지 않는다."""
    notices = structure_notices(_profile(_MESSY))
    indexes = [n.column_index for n in notices]
    assert len(indexes) == len(set(indexes))


# ── 가드레일: 셀 값 미포함 ────────────────────────────────────────


def test_notices_never_carry_cell_values() -> None:
    """§5.2 — 헤더 이름만. 실명·전화 등 셀 값은 주의사항에 담기지 않는다."""
    data = _xlsx(
        {
            "1학기": [
                ["점수", "점수", None],
                ["김철수", "010-1234-5678", "박민수"],
            ]
        }
    )
    notices = structure_notices(_profile(data))
    blob = " ".join(f"{n.sheet}|{n.kind.value}|{n.header}" for n in notices)
    for leaked in ("김철수", "010-1234-5678", "박민수"):
        assert leaked not in blob


# ── 관심사 분리: source_columns의 중복 제거는 유지 ────────────────


def test_source_columns_still_dedupes() -> None:
    """매핑 추론 대상 목록에는 중복이 없다 — 합치기 동작은 바뀌지 않았다."""
    columns = source_columns(_profile(_MESSY))
    assert len(columns) == len(set(columns))
    # pandas가 두 번째 `점수`를 `점수.1`로 개명하므로 목록에는 둘 다 남되 이름이 다르다.
    assert "점수" in columns


def test_notices_are_independent_of_column_list() -> None:
    """중복이 목록에서 합쳐지든 개명되든, 주의사항은 원본 헤더 기준으로 나온다."""
    profile = _profile(_MESSY)
    assert any(n.kind is _K.DUPLICATE_HEADER for n in structure_notices(profile))
    assert "점수" in source_columns(profile)


# ── csv 경로 ──────────────────────────────────────────────────────


def test_csv_duplicate_and_empty_headers() -> None:
    data = "점수,점수,,이름\n1,2,,a\n".encode()
    notices = structure_notices(_profile(data, "roster.csv"))
    assert [(n.sheet, n.kind, n.column_index) for n in notices] == [
        ("sheet1", _K.DUPLICATE_HEADER, 1),
        ("sheet1", _K.DUPLICATE_HEADER, 2),
        ("sheet1", _K.EMPTY_HEADER, 3),
    ]


# ── 계약 노출 ─────────────────────────────────────────────────────


def test_preview_carries_notices_and_defaults_empty() -> None:
    """MappingPreview에 실려 GET 응답으로 나간다. 기본값은 빈 목록."""
    notices = structure_notices(_profile(_MESSY))
    preview = MappingPreview(spec_version=1, reused=False, columns=(), structure_notices=notices)
    dumped = preview.model_dump(mode="json")
    assert dumped["structure_notices"][0]["kind"] == "duplicate_header"
    assert MappingPreview.model_validate(dumped) == preview
    assert MappingPreview(spec_version=1, reused=False, columns=()).structure_notices == ()


def test_notice_kind_values_frozen() -> None:
    """계약 어휘 고정 — 10 §1.2 kind 3종."""
    assert {kind.value for kind in StructureNoticeKind} == {
        "duplicate_header",
        "empty_header",
        "header_without_data",
    }
