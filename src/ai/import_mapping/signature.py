"""양식 시그니처 + spec 캐시 — reused 판별 (10_import_spec §3.4).

시그니처 = 테넌트 스코프 + 정규화된 헤더 집합 + 시트 구성(시트 수·시트별 컬럼 수)의 결정론 해시.
시트 이름·순서에 의존하지 않는다(같은 양식의 월별 시트명 차이가 캐시 미스를 만들면 안 됨).
같은 시그니처 → confirmed spec 재사용(LLM·에이전트 0회).

canonical_form(사람이 읽을 수 있는 정규형)을 골든으로 고정하고, 시그니처는 그 해시다 —
기대값은 손 작성한다(엔진 산출 금지).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from ai.contracts.imports import MappingColumn
from ai.import_mapping.profiling import SourceProfile

_WS_RE = re.compile(r"\s+")


def _norm(header: str) -> str:
    """헤더 정규화 — 트림·소문자·내부 공백 단일화."""
    return _WS_RE.sub(" ", header.strip()).lower()


def canonical_form(profile: SourceProfile, tenant_id: str) -> str:
    """결정론 정규형(사람 판독 가능) — 시그니처의 입력. 시트명·순서 무관."""
    headers = sorted({_norm(c.name) for sheet in profile.sheets for c in sheet.columns})
    shape = sorted(len(sheet.columns) for sheet in profile.sheets)
    return (
        f"tenant={tenant_id}\n"
        f"sheets={len(profile.sheets)}\n"
        f"shape={shape}\n"
        f"headers={','.join(headers)}"
    )


def form_signature(profile: SourceProfile, tenant_id: str) -> str:
    """양식 시그니처 = canonical_form의 SHA-256 hex. 같은 입력 → 같은 값(결정론)."""
    return hashlib.sha256(canonical_form(profile, tenant_id).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CachedSpec:
    """confirmed 매핑 spec의 캐시 항목 — 재수입 시 그대로 재사용."""

    spec_version: int
    columns: tuple[MappingColumn, ...]


class SpecCache(Protocol):
    """양식 시그니처 → confirmed spec 캐시. 라우터는 이 타입에만 의존한다."""

    def get(self, signature: str) -> CachedSpec | None: ...

    def put(self, signature: str, spec: CachedSpec) -> None: ...


class InMemorySpecCache:
    """프로세스 인메모리 spec 캐시 — 테스트·개발용(재시작 소실). PG 영속은 후속(99 ⑨)."""

    def __init__(self, seed: dict[str, CachedSpec] | None = None) -> None:
        self._rows: dict[str, CachedSpec] = dict(seed or {})

    def get(self, signature: str) -> CachedSpec | None:
        return self._rows.get(signature)

    def put(self, signature: str, spec: CachedSpec) -> None:
        self._rows.setdefault(signature, spec)  # 최초 confirmed 유지

    def clear(self) -> None:
        self._rows.clear()


def columns_from_overrides(
    base: Sequence[MappingColumn], overrides: dict[str, str | None]
) -> tuple[MappingColumn, ...]:
    """강사 override를 base 컬럼에 적용 — confirm 재검증·캐시 저장용(결정론)."""
    applied: list[MappingColumn] = []
    for col in base:
        if col.source in overrides:
            applied.append(
                col.model_copy(
                    update={
                        "target": overrides[col.source],
                        "needs_review": False,
                        "unmapped_reason": None,
                    }
                )
            )
        else:
            applied.append(col)
    return tuple(applied)
