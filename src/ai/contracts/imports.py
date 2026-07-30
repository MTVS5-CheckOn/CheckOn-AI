"""Import(스마트 데이터 이전) 계약 — /v1/imports 요청·응답 타입.

사양 원본: docs/part_a/10_import_spec.md §1 (04_api_contract §3.8 상세화 · A 확정 범위 §6.3)
소유: 박진희(member-A) 단독 — 양자 승인 대상 아님 (docs/02_ownership.md §3, detection.py 선례).

범위(2026-07-30 확정): AI는 **매핑 제안까지**다 — 전체 행 변환·행별 검증·집계·산출물 저장은
백엔드 소유(10 §4). 그래서 이 계약에 output_url·행 집계 타입이 없다.

불변식 1(CLAUDE.md): LLM은 매핑을 추론만 — 확정은 결정론. 억지 매핑 금지(모르면 unmapped).
불변식 3: AI의 LLM·저장소는 실명 값을 보지 않는다(§5). extra="forbid"로 경계 밖 필드를
구조적으로 차단한다.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ImportStatus(StrEnum):
    """Import 작업 상태 — 10_import_spec §2 상태기계(04 §3.8 정본)."""

    PROFILING = "profiling"
    INFERRING = "inferring"
    PROBING = "probing"
    PREVIEW_READY = "preview_ready"
    DONE = "done"
    """강사 확정 spec 저장 완료 — 전체 행 변환은 AI 범위 밖(10 §4)."""

    BLOCKED = "blocked"
    FAILED = "failed"


# ───────────────────────── Request (백엔드/강사 → AI) ─────────────────────────


class ImportCreateRequest(BaseModel):
    """POST /v1/imports 바디 — 파일은 스토리지 URL로만 받는다(Open-3, 바디에 바이트 없음)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_url: str = Field(min_length=1)
    """강사가 올린 원본 파일 위치(xlsx·csv). AI가 이 URL에서 내려받아 프로파일링."""

    filename: str = Field(min_length=1)
    """확장자 판별·에러 문구용."""

    sheet_hint: str | None = None
    """강사가 시트를 지정하면 힌트 — 미지정 시 전 시트 프로파일링."""


class SpecOverride(BaseModel):
    """confirm의 강사 수정 1건 — 타사 컬럼을 표준 필드로(또는 null=제외 확정)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_column: str = Field(min_length=1)
    target_field: str | None = None
    """07 표준 필드명 또는 null(제외 확정). 표준 필드명이 아니면 라우터가 400."""


class ConfirmRequest(BaseModel):
    """POST /v1/imports/{job_id}/confirm 바디 — override 없으면 미리보기 그대로 확정."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    spec_overrides: tuple[SpecOverride, ...] = ()


# ───────────────────────── Response (AI → 백엔드/강사) ─────────────────────────


class MappingColumn(BaseModel):
    """미리보기 컬럼 1개 — 04 §3.8 mapping_preview.columns[] 형태."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(min_length=1)
    target: str | None = None
    """07 표준 필드명 또는 null(unmapped). 자유 필드명 금지."""

    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    needs_review: bool = False
    """confidence < CONFIDENCE_REVIEW — 숨기지 않고 강사에게 노출."""

    probe_note: str | None = None
    """조사 에이전트 추론 근거(툴팁용) — 후속(agent PR)."""

    unmapped_reason: str | None = None
    """target=null의 정직한 사유('모름' 또는 개인정보 정책 제외)."""


class MappingPreview(BaseModel):
    """preview_ready·blocked의 강사 확인 자료 — 04 §3.8 mapping_preview."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    spec_version: int = Field(ge=1)
    reused: bool = False
    """true = 같은 양식 재수입 → LLM·에이전트 0회로 기존 spec 재사용(§3.4)."""

    columns: tuple[MappingColumn, ...]
    sample_rows: tuple[dict[str, str], ...] = ()
    """변환 예시(≤N행). 실명 무접촉 가드레일(§5.2) — redactor 미주입 시 비어 있다(구조적)."""

    blocked: bool = False
    blocked_reason: str | None = None
    """blocked=true면 필수 필드 미매핑 → confirm 차단(override로 해제 가능)."""


class ImportJobView(BaseModel):
    """GET /v1/imports/{job_id} 및 POST/confirm의 data — 상태별로 채워지는 필드가 다르다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str = Field(min_length=1)
    status: ImportStatus
    progress: str | None = None
    """probing 루프 회차 등 진행 표시('2/5') — 후속."""

    status_reason: str | None = None
    """failed 사유(file_unreadable·timeout_5min 등) — error_codes §2.4."""

    mapping_preview: MappingPreview | None = None
    """preview_ready·blocked·done 공통 — done은 강사가 확정한 spec이 실린다.

    변환 결과(산출물 URL·행 집계)는 없다 — 백엔드 소유(10 §4, 2026-07-30).
    """
