"""매핑 추론 오케스트레이션 — reused·1-shot·폴백·게이트 (10_import_spec §3.2·§3.4).

결정론 순수 로직(provider 주입). 흐름:
- 시그니처 캐시 hit → reused=true 미리보기(LLM·에이전트 0회).
- miss → provider.infer. 실패(MappingInferenceError) → **전 컬럼 needs_review 수동 미리보기**
  (§3.2 폴백 — 작업 실패로 안 떨어뜨림).
- needs_review = confidence < CONFIDENCE_REVIEW.
- probing(조사 에이전트) **기동 조건 판정까지만**(§3.3) — 실행은 후속(LangGraph B 리뷰 대기).

**차단하지 않는다(2026-07-30 백엔드 확정):** 필수 여부 판단은 백엔드가 Import 유형별 규칙으로
하고 AI는 정보만 준다. 그래서 RequiredField 게이트·`blocked` 상태·유형 추측이 전부 없다 —
`unmapped_target_fields`(표준 필드 여집합)를 미리보기에 실어 보내는 것으로 대체한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from ai.contracts.imports import ImportStatus, MappingColumn, MappingPreview
from ai.import_mapping.profiling import SourceProfile, source_columns, structure_notices
from ai.import_mapping.provider import MappingInferenceError, MappingProvider
from ai.import_mapping.signature import CachedSpec, SpecCache, form_signature

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


def unmapped_target_fields(targets: frozenset[str]) -> tuple[str, ...]:
    """매핑 후보를 찾지 못한 표준 필드 — `STANDARD_FIELDS` 여집합(정렬·결정론).

    **유형 무관**이다. 명부/학습 종류를 추측하지 않는다 — 유형별 필수 규칙은 백엔드
    소유이므로(2026-07-30 확정) AI가 유형을 판별할 근거가 없다. 07 표준 필드 전체를
    기준으로 여집합을 내고, 걸러내는 일은 백엔드가 한다.
    """
    return tuple(sorted(STANDARD_FIELDS - targets))


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
    # 구조 주의사항은 **이번에 올린 파일**의 사실이라 캐시 hit(reused)에도 함께 싣는다(§1.2).
    notices = structure_notices(profile)
    cached = cache.get(signature)
    if cached is not None:
        preview = MappingPreview(
            spec_version=cached.spec_version,
            reused=True,
            columns=cached.columns,
            unmapped_target_fields=unmapped_target_fields(mapped_targets(cached.columns)),
            source_fingerprint=signature,
            structure_notices=notices,
        )
        return InferenceOutcome(preview, ImportStatus.PREVIEW_READY, needs_probing=False)

    try:
        raw = await provider.infer(profile)
    except MappingInferenceError:
        # 폴백 — 전 컬럼 수동 미리보기(§3.2). 정보 필드는 폴백에도 채운다(전 컬럼 target=None
        # 이므로 여집합 = 표준 필드 전체).
        fallback = _fallback_columns(profile)
        preview = MappingPreview(
            spec_version=1,
            reused=False,
            columns=fallback,
            unmapped_target_fields=unmapped_target_fields(mapped_targets(fallback)),
            source_fingerprint=signature,
            structure_notices=notices,
        )
        return InferenceOutcome(preview, ImportStatus.PREVIEW_READY, needs_probing=False)

    columns = _apply_needs_review(raw, confidence_review)
    needs_probing = any(
        c.target is not None and (c.confidence or 0.0) < confidence_review for c in columns
    )

    # 차단하지 않는다 — 미매핑 표준 필드는 정보로 실어 보내고 필수 판단은 백엔드가 한다.
    preview = MappingPreview(
        spec_version=1,
        reused=False,
        columns=columns,
        unmapped_target_fields=unmapped_target_fields(mapped_targets(columns)),
        source_fingerprint=signature,
        structure_notices=notices,
    )
    return InferenceOutcome(preview, ImportStatus.PREVIEW_READY, needs_probing)


def confirmed_cache_entry(preview: MappingPreview) -> CachedSpec:
    """확정된 미리보기를 캐시 항목으로 — confirm 성공 시 저장(다음 재수입 reused)."""
    return CachedSpec(spec_version=preview.spec_version, columns=preview.columns)
