"""docs/06_erd.md 의 mermaid erDiagram 을 파싱한다 — 대조 테스트의 정본 소스.

ERD가 정본이므로(사용자 확정 7/22) 이 파서가 뽑은 표를 ORM 메타데이터와 대조한다.
파서는 테스트 전용이며 프로덕션 코드가 아니다.

추출 범위: 테이블명 · 컬럼명 · 타입 카테고리 · PK · FK.
(유니크 제약은 ERD가 산문·컬럼 주석에 흩어 적어 파싱이 취약하므로,
 대조 테스트에서 ERD 라인을 인용한 명시 기대셋으로 따로 검증한다.)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

#: ERD 타입 표기 → 대조용 카테고리. ORM 쪽 SQLAlchemy 타입도 같은 카테고리로 접는다.
ERD_TYPE_CATEGORY: dict[str, str] = {
    "uuid": "uuid",
    "varchar": "string",
    "text": "text",
    "jsonb": "json",
    "int": "integer",
    "numeric": "numeric",
    "timestamptz": "datetime",
    "date": "date",
    "boolean": "boolean",
}

_TABLE_OPEN = re.compile(r"^([A-Z_][A-Z0-9_]*)\s*\{$")


@dataclass
class ErdColumn:
    name: str
    type_category: str
    pk: bool = False
    fk: bool = False


@dataclass
class ErdTable:
    name: str
    columns: dict[str, ErdColumn] = field(default_factory=dict)

    @property
    def pk_columns(self) -> set[str]:
        return {c.name for c in self.columns.values() if c.pk}

    @property
    def fk_columns(self) -> set[str]:
        return {c.name for c in self.columns.values() if c.fk}


def _mermaid_block(md_text: str) -> list[str]:
    """```mermaid ... ``` 사이의 줄들을 뽑는다 (erDiagram 블록)."""
    lines = md_text.splitlines()
    out: list[str] = []
    inside = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```mermaid"):
            inside = True
            continue
        if inside and stripped.startswith("```"):
            break
        if inside:
            out.append(line)
    if not out:
        raise ValueError("06_erd.md 에서 mermaid 블록을 찾지 못했다")
    return out


def _parse_column(line: str) -> ErdColumn:
    """`type name [PK|FK] ["comment"]` 한 줄을 파싱한다."""
    comment = ""
    decl = line
    if '"' in line:
        idx = line.index('"')
        decl = line[:idx].strip()
        comment = line[idx:].strip().strip('"')
    parts = decl.split()
    erd_type, name = parts[0], parts[1]
    markers = parts[2:]
    category = ERD_TYPE_CATEGORY.get(erd_type)
    if category is None:
        raise ValueError(f"ERD 미지원 타입 표기: {erd_type!r} (줄: {line!r})")
    pk = "PK" in markers
    # FK는 마커(FK) 또는 PK 컬럼의 주석에 담긴 경우(LLM_PAYLOAD.call_id "FK llm_call")까지.
    fk = "FK" in markers or comment.strip().upper().startswith("FK ")
    return ErdColumn(name=name, type_category=category, pk=pk, fk=fk)


def parse_erd(md_path: Path) -> dict[str, ErdTable]:
    """06_erd.md → {테이블명: ErdTable}. 테이블명은 소문자로 정규화한다."""
    tables: dict[str, ErdTable] = {}
    current: ErdTable | None = None
    for raw in _mermaid_block(md_path.read_text(encoding="utf-8")):
        s = raw.strip()
        if not s or s.startswith("%%") or s == "erDiagram":
            continue
        if current is None:
            m = _TABLE_OPEN.match(s)
            if m:
                current = ErdTable(name=m.group(1).lower())
                tables[current.name] = current
            # 매칭 안 되면 관계선(`A ||--o{ B : "..."`) — 건너뛴다.
            continue
        if s == "}":
            current = None
            continue
        col = _parse_column(s)
        current.columns[col.name] = col
    if current is not None:
        raise ValueError(f"ERD 테이블 블록이 닫히지 않았다: {current.name}")
    return tables


ERD_PATH = Path(__file__).resolve().parents[3] / "docs" / "06_erd.md"
