"""매핑 추론 오케스트레이션 — reused·1-shot·폴백·게이트 (10_import_spec §3.2·§3.4).

결정론 순수 로직(provider 주입). 흐름:
- 시그니처 캐시 hit → reused=true 미리보기(LLM·에이전트 0회).
- miss → provider.infer. 실패(MappingInferenceError) → **전 컬럼 needs_review 수동 미리보기**
  (§3.2 폴백 — 작업 실패로 안 떨어뜨림).
- needs_review = confidence < CONFIDENCE_REVIEW. RequiredField 게이트 미충족 → blocked.
- probing(조사 에이전트) **기동 조건 판정까지만**(§3.3) — 실행은 후속(LangGraph B 리뷰 대기).
"""

from __future__ import annotations

from dataclasses import dataclass

from ai.contracts.imports import ImportStatus, MappingColumn, MappingPreview
from ai.import_mapping.profiling import SourceProfile, source_columns
from ai.import_mapping.provider import MappingInferenceError, MappingProvider
from ai.import_mapping.signature import CachedSpec, SpecCache, form_signature

#: RequiredField 게이트 필수 표준 필드(10_import_spec §3.4 — 07 §2·§3의 ✅ 예시 그대로).
#: 명부(roster) vs 학습(learning)은 매핑된 필드로 종류를 판별한다(v0 휴리스틱).
REQUIRED_ROSTER: frozenset[str] = frozenset(
    {"student_name", "class_name", "enrolled_at", "status", "consent"}
)
REQUIRED_LEARNING: frozenset[str] = frozenset({"occurred_at", "event_type"})

#: 유효한 매핑 목적지 = 07 표준 필드명(§2 명부 + §3 learning_event). override 검증에 쓴다.
#: 자유 필드명 금지(07 §4) — 이 집합 밖은 400 INVALID_SCHEMA.
STANDARD_FIELDS: frozenset[str] = frozenset(
    {
        "student_name", "grade", "class_name", "enrolled_at", "status",
        "guardian_name", "guardian_phone", "consent", "subject_track",
        "occurred_at", "event_type", "assignment_title", "area_tag", "type_tag",
        "item_format", "correct", "score", "max_score", "duration_sec",
        "passage_word_count", "source",
    }
)


def detect_kind(targets: frozenset[str]) -> str:
    """명부/학습 종류 판별 — 명부 식별 필드가 매핑됐으면 roster(§3.4 '명부 이전 시')."""
    return "roster" if targets & {"student_name", "class_name"} else "learning"


def missing_required(targets: frozenset[str]) -> frozenset[str]:
    """매핑된 표준 필드가 필수 집합을 채우는가 — 미충족분 반환(빈 집합=통과)."""
    required = REQUIRED_ROSTER if detect_kind(targets) == "roster" else REQUIRED_LEARNING
    return required - targets


@dataclass(frozen=True)
class InferenceOutcome:
    """추론 결과 — 미리보기 + 도달 상태 + probing 기동 필요 여부(실행은 후속)."""

    preview: MappingPreview
    status: ImportStatus
    needs_probing: bool


def _apply_needs_review(
    columns: tuple[MappingColumn, ...], threshold: float
) -> tuple[MappingColumn, ...]:
    """confidence < threshold인 매핑에 needs_review 플래그(숨기지 않고 노출 — §2.4)."""
    flagged: list[MappingColumn] = []
    for col in columns:
        low = col.target is not None and (col.confidence or 0.0) < threshold
        flagged.append(col.model_copy(update={"needs_review": low}) if low else col)
    return tuple(flagged)


def _fallback_columns(profile: SourceProfile) -> tuple[MappingColumn, ...]:
    """LLM 미가용 폴백 — 전 컬럼 needs_review·target=null(수동 매핑, §3.2). 억지 매핑 없음."""
    return tuple(
        MappingColumn(
            source=name,
            target=None,
            confidence=0.0,
            needs_review=True,
            unmapped_reason="LLM 미가용 — 수동 매핑 필요",
        )
        for name in source_columns(profile)
    )


def mapped_targets(columns: tuple[MappingColumn, ...]) -> frozenset[str]:
    """매핑된(target 있는) 표준 필드 집합 — 게이트·캐시가 쓴다."""
    return frozenset(c.target for c in columns if c.target is not None)


async def infer_mapping(
    profile: SourceProfile,
    tenant_id: str,
    *,
    provider: MappingProvider,
    cache: SpecCache,
    confidence_review: float,
) -> InferenceOutcome:
    """프로파일 → 미리보기·상태. 판정식은 결정론, provider(LLM)는 매핑 후보만."""
    signature = form_signature(profile, tenant_id)
    cached = cache.get(signature)
    if cached is not None:
        preview = MappingPreview(
            spec_version=cached.spec_version, reused=True, columns=cached.columns
        )
        return InferenceOutcome(preview, ImportStatus.PREVIEW_READY, needs_probing=False)

    try:
        raw = await provider.infer(profile)
    except MappingInferenceError:
        # 폴백 — 전 컬럼 수동 미리보기. required 미충족이라도 blocked가 아니라 preview_ready(§3.2).
        preview = MappingPreview(
            spec_version=1, reused=False, columns=_fallback_columns(profile)
        )
        return InferenceOutcome(preview, ImportStatus.PREVIEW_READY, needs_probing=False)

    columns = _apply_needs_review(raw, confidence_review)
    targets = mapped_targets(columns)
    needs_probing = any(
        c.target is not None and (c.confidence or 0.0) < confidence_review for c in columns
    )

    missing = missing_required(targets)
    if missing:
        preview = MappingPreview(
            spec_version=1,
            reused=False,
            columns=columns,
            blocked=True,
            blocked_reason=f"필수 필드 미매핑: {', '.join(sorted(missing))}",
        )
        return InferenceOutcome(preview, ImportStatus.BLOCKED, needs_probing)

    preview = MappingPreview(spec_version=1, reused=False, columns=columns)
    return InferenceOutcome(preview, ImportStatus.PREVIEW_READY, needs_probing)


def confirmed_cache_entry(preview: MappingPreview) -> CachedSpec:
    """확정된 미리보기를 캐시 항목으로 — confirm 성공 시 저장(다음 재수입 reused)."""
    return CachedSpec(spec_version=preview.spec_version, columns=preview.columns)
