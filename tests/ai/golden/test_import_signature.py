"""양식 시그니처 골든 — canonical_form 기대값 손 작성 (10_import_spec §3.4).

기대 정규형은 사람이 손으로 옮긴 값이다(엔진 산출 금지). 시그니처는 그 정규형의 SHA-256이며,
시트명·순서·컬럼 순서에 불변임을 고정한다.
"""

from __future__ import annotations

import hashlib

from ai.import_mapping.profiling import ColumnProfile, SheetProfile, SourceProfile
from ai.import_mapping.signature import canonical_form, form_signature


def _col(name: str) -> ColumnProfile:
    return ColumnProfile(
        name=name, n_total=3, n_null=0, n_unique=3, dtype_guess="string", suspect_pii=False
    )


def _profile(sheet_name: str, headers: list[str]) -> SourceProfile:
    sheet = SheetProfile(
        name=sheet_name, n_rows=3, columns=tuple(_col(h) for h in headers), sample_rows=()
    )
    return SourceProfile(filename="f.xlsx", sheets=(sheet,))


#: 손 작성 기대 정규형 — 정규화(트림·소문자) + 정렬(코드포인트) + 시트 구성. 시트명 미포함.
_GOLDEN_CANONICAL = "tenant=t1\nsheets=1\nshape=[3]\nheaders=원생명,점수a,주소"


def test_canonical_form_matches_golden() -> None:
    profile = _profile("3월", ["원생명", "점수A", "주소"])
    assert canonical_form(profile, "t1") == _GOLDEN_CANONICAL


def test_signature_is_sha256_of_canonical() -> None:
    profile = _profile("3월", ["원생명", "점수A", "주소"])
    expected = hashlib.sha256(_GOLDEN_CANONICAL.encode("utf-8")).hexdigest()
    assert form_signature(profile, "t1") == expected


def test_signature_invariant_to_sheet_name_and_column_order() -> None:
    """같은 양식의 월별 시트명·컬럼 순서 차이는 같은 시그니처(reused 성립 조건)."""
    a = _profile("3월", ["원생명", "점수A", "주소"])
    b = _profile("4월", ["주소", "점수A", "원생명"])  # 시트명·순서만 다름
    assert form_signature(a, "t1") == form_signature(b, "t1")


def test_signature_scoped_by_tenant() -> None:
    profile = _profile("3월", ["원생명", "점수A", "주소"])
    assert form_signature(profile, "t1") != form_signature(profile, "t2")


def test_signature_changes_with_headers() -> None:
    a = _profile("3월", ["원생명", "점수A", "주소"])
    b = _profile("3월", ["원생명", "점수B", "주소"])  # 헤더 하나 다름
    assert form_signature(a, "t1") != form_signature(b, "t1")
