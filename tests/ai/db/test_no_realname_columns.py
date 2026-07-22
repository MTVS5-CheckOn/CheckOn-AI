"""불변식 3 — 실명·연락처 컬럼은 AI PG에 존재할 수 없다 (alias만).

ERD 대조(test_erd_model_parity)가 "ORM==ERD"를 보장하므로 1차 방어는 그쪽이지만,
이 검사는 **의도 자체를 금지**한다 — 누가 ERD에 `student_name`·`phone`을 더해도,
ORM에 PII 컬럼을 몰래 매핑해도 여기서 걸린다(벤더명 검출 방식의 확장).

정당한 `model_name`(LLM)·`filename`(업로드)·`gate_name`·`node_name`·`column_name`(엑셀)은
오탐하지 않는다 — 사람 실명/연락처 지시 토큰만 좁게 금지한다.
"""

from __future__ import annotations

import re
from pathlib import Path

from erd_parser import ERD_PATH, parse_erd

from ai.db.models import Base

#: 연락처·식별 PII 지시 토큰 (부분일치 금지).
_CONTACT_TOKENS = ("phone", "email", "mobile", "contact", "ssn", "zipcode", "birth")
#: 주소 — addr/address (단어 경계).
_ADDRESS = re.compile(r"\baddr(ess)?\b|_addr(ess)?\b|(^|_)address")
#: 사람 실명 컬럼 — student_name·guardian_name·real_name 등. model_name·filename은 제외.
_PERSON_NAME = re.compile(r"(student|guardian|parent|child|teacher|user|person|real|full)_?name")

_MODELS_SRC = Path(__file__).resolve().parents[3] / "src" / "ai" / "db" / "models.py"


def _is_pii_column(name: str) -> str | None:
    """PII로 의심되면 사유 문자열, 아니면 None."""
    low = name.lower()
    for tok in _CONTACT_TOKENS:
        if tok in low:
            return f"연락처 토큰 '{tok}'"
    if _ADDRESS.search(low):
        return "주소"
    if _PERSON_NAME.search(low):
        return "사람 실명"
    return None


def test_orm_has_no_pii_columns() -> None:
    """매핑된 전 테이블 컬럼에 실명·연락처 없음."""
    offenders = [
        f"{table}.{col}: {reason}"
        for table, tbl in Base.metadata.tables.items()
        for col in tbl.columns.keys()
        if (reason := _is_pii_column(col))
    ]
    assert not offenders, "실명·연락처 컬럼 금지(불변식 3): " + "; ".join(offenders)


def test_erd_has_no_pii_columns() -> None:
    """ERD 정본에도 실명·연락처 컬럼이 없어야 한다 (상류에서 막는다)."""
    offenders = [
        f"{table}.{col}: {reason}"
        for table, erd in parse_erd(ERD_PATH).items()
        for col in erd.columns
        if (reason := _is_pii_column(col))
    ]
    assert not offenders, "ERD에 실명·연락처 컬럼: " + "; ".join(offenders)


def test_models_source_has_no_pii_string_literals() -> None:
    """models.py 소스 텍스트에도 PII 컬럼명 리터럴이 없어야 한다(주석·미매핑 포함)."""
    src = _MODELS_SRC.read_text(encoding="utf-8").lower()
    hits = [tok for tok in _CONTACT_TOKENS if tok in src]
    # 'real_name'·'student_name' 같은 사람 실명 리터럴도 소스에서 직접 탐지.
    if _PERSON_NAME.search(src):
        hits.append("person_name")
    assert not hits, f"models.py 소스에 PII 토큰: {hits}"
