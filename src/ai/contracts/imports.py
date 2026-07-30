"""Import(스마트 데이터 이전) 계약 — /v1/imports 요청·응답 타입.

사양 원본: docs/part_a/10_import_spec.md §1 (04_api_contract §3.8 상세화 · A 확정 범위 §6.3)
소유: 박진희(member-A) 단독 — 양자 승인 대상 아님 (docs/02_ownership.md §3, detection.py 선례).

범위(2026-07-30 확정): AI는 **매핑 제안까지**다 — 전체 행 변환·행별 검증·집계·산출물 저장은
백엔드 소유(10 §4). 그래서 이 계약에 output_url·행 집계 타입이 없다.

**책임 경계(2026-07-30 백엔드 확정):** 백엔드가 확정 매핑의 기준 데이터를 보유하고, AI는 양식
재사용을 위해 확정된 매핑을 전달받아 활용한다. **필수 여부 판단도 백엔드가 Import 유형별
규칙으로** 한다 — AI는 정보만 준다(미매핑 표준 필드·미매핑 원본 컬럼·신뢰도·강사 확인 필요
표시). 그래서 이 계약에 `blocked` 축이 없다(AI 측 확정 차단 제거).

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


class StructureNoticeKind(StrEnum):
    """파일 구조 주의사항 유형 — 10 §1.2 `structure_notices[].kind`."""

    DUPLICATE_HEADER = "duplicate_header"
    """같은 헤더 이름이 2회 이상 — 매핑 대상 목록에서는 하나로 합쳐진다."""

    EMPTY_HEADER = "empty_header"
    """이름 없는(공백만인) 헤더."""

    HEADER_WITHOUT_DATA = "header_without_data"
    """헤더는 있으나 그 컬럼의 값이 전량 결측."""


class StructureNotice(BaseModel):
    """파일 구조 주의사항 1건 — AI가 파일을 어떻게 해석했는지 강사가 검토할 참고정보.

    강사가 **원본에서 찾을 수 있어야** 하므로 시트·컬럼 위치·유형이 함께 실린다.
    ⚠ **셀 값은 담지 않는다**(10 §5.2 가드레일) — 헤더 이름만.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sheet: str = Field(min_length=1)
    kind: StructureNoticeKind
    column_index: int = Field(ge=1)
    """원본의 1-based 컬럼 위치(좌→우) — 강사가 파일에서 세어 찾는다."""

    header: str = ""
    """헤더 텍스트. `empty_header`면 빈 문자열."""


class MappingPreview(BaseModel):
    """preview_ready의 강사 확인 자료 — 04 §3.8 mapping_preview.

    **AI는 판정하지 않고 정보를 준다**(2026-07-30 백엔드 확정): 매핑 후보를 찾지 못한 표준
    필드(`unmapped_target_fields`) · 매핑되지 않은 원본 컬럼(`columns[].target is None`) ·
    각 매핑의 신뢰도(`confidence`) · 강사 확인이 필요한 매핑(`needs_review`). 필수 여부
    판단과 확정 차단은 백엔드 소유다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    spec_version: int = Field(ge=1)
    reused: bool = False
    """true = 같은 양식 재수입 → LLM·에이전트 0회로 기존 spec 재사용(§3.4)."""

    columns: tuple[MappingColumn, ...]
    unmapped_target_fields: tuple[str, ...] = ()
    """**매핑 후보를 찾지 못한 표준 필드**(정렬 — 결정론). 07 표준 필드 여집합이다.

    ⚠ **유형 무관** — 07 §2(명부) + §3(learning_event) 표준 필드 **전체 기준**의 여집합이므로
    이번 Import 유형과 무관한 필드도 포함된다(명부 파일을 올리면 `occurred_at`·`event_type`
    같은 학습기록 필드가 전부 들어온다).
    ⚠ **필수 여부 판단이 아니다** — 필수는 백엔드가 Import 유형별 규칙으로 판단한다
    (2026-07-30 백엔드 확정). AI는 "무엇이 안 채워졌는지"만 알려준다.
    """

    source_fingerprint: str = ""
    """양식 지문 — AI 내부의 "양식 시그니처"(§3.4 `form_signature`)와 같은 값이다.

    **백엔드는 이 값을 재계산할 수 없다**(헤더 정규화·시트 구성 규칙이 AI 안에 있다). 받은
    값을 그대로 보관하고 확정 매핑과 함께 반송하는 용도다 — 그래야 AI가 다음 재수입에서
    `reused`를 판정할 수 있다.
    🔴 **해시 입력에 `tenant_id`가 들어간다** — 같은 양식이라도 테넌트가 다르면 값이 다르다.
    테넌트 간에 공유·대조할 수 있는 값이 아니다.
    """

    structure_notices: tuple[StructureNotice, ...] = ()
    """파일 구조 주의사항(백엔드 요청, 2026-07-30) — 없으면 빈 목록."""

    sample_rows: tuple[dict[str, str], ...] = ()
    """변환 예시(≤N행). 실명 무접촉 가드레일(§5.2) — redactor 미주입 시 비어 있다(구조적)."""


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
    """preview_ready·done 공통 — done은 강사가 확정한 spec이 실린다.

    변환 결과(산출물 URL·행 집계)는 없다 — 백엔드 소유(10 §4, 2026-07-30).
    """
