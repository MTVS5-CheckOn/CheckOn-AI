"""매핑 추론 오케스트레이션 — reused·1-shot·폴백·게이트 (10_import_spec §3.2·§3.4).

FakeMappingProvider(성공)·FailingMappingProvider(폴백)로 결정론화. 정상·경계·실패 분기 고정.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine

from ai.contracts.imports import ImportStatus, MappingColumn
from ai.import_mapping.inference import (
    InferenceOutcome,
    confirmed_cache_entry,
    infer_mapping,
    unmapped_target_fields,
)
from ai.import_mapping.profiling import ColumnProfile, SheetProfile, SourceProfile
from ai.import_mapping.provider import FailingMappingProvider, FakeMappingProvider
from ai.import_mapping.signature import InMemorySpecCache, form_signature

_REVIEW = 0.9


def _run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


def _profile(headers: list[str]) -> SourceProfile:
    cols = tuple(
        ColumnProfile(
            name=h, n_total=3, n_null=0, n_unique=3, dtype_guess="string", suspect_pii=False
        )
        for h in headers
    )
    return SourceProfile(filename="f.xlsx", sheets=(SheetProfile("s", 3, cols, ()),))


def _infer(
    profile: SourceProfile, *, provider: object, cache: InMemorySpecCache | None = None
) -> InferenceOutcome:
    return _run(
        infer_mapping(
            profile,
            "t1",
            provider=provider,  # type: ignore[arg-type]
            cache=cache or InMemorySpecCache(),
            confidence_review=_REVIEW,
        )
    )


# ── 게이트 순수 함수 ───────────────────────────────────────


def test_unmapped_target_fields_is_type_agnostic_complement() -> None:
    """유형 추측 없이 STANDARD_FIELDS 여집합 — 유형별 필수 판단은 백엔드 소유(2026-07-30).

    명부 필드만 매핑해도 학습기록 필드(`occurred_at` 등)가 목록에 남는다 — 의도된 동작이다.
    """
    from ai.import_mapping.inference import STANDARD_FIELDS

    roster = frozenset({"student_name", "class_name"})
    result = unmapped_target_fields(roster)
    assert set(result) == set(STANDARD_FIELDS) - roster
    assert "occurred_at" in result  # 유형 무관 — 명부만 매핑해도 학습 필드가 남는다
    assert "student_name" not in result
    assert list(result) == sorted(result)  # 결정론 정렬


def test_no_type_heuristic_survives() -> None:
    """유형 추측 휴리스틱이 근거를 잃어 제거됐다 — 두 곳이 다르게 판단하지 않게."""
    import ai.import_mapping.inference as inference

    for gone in ("detect_kind", "missing_required", "REQUIRED_ROSTER", "REQUIRED_LEARNING"):
        assert not hasattr(inference, gone), f"{gone}이 살아 있다 — 백엔드와 판단이 갈린다"


# ── 정상: 명부 전 필수 매핑 → preview_ready ────────────────


def test_roster_all_required_mapped_preview_ready() -> None:
    profile = _profile(["원생명", "반", "등원일", "상태", "동의"])
    outcome = _infer(profile, provider=FakeMappingProvider())
    assert outcome.status is ImportStatus.PREVIEW_READY
    assert outcome.preview.reused is False
    assert outcome.preview.source_fingerprint  # 백엔드 보관용 지문이 실린다


# ── 경계: 필수 미매핑이어도 차단하지 않는다 (2026-07-30 확정) ──


def test_missing_targets_are_reported_not_blocked() -> None:
    """필수 미충족이어도 `preview_ready` — 미매핑 표준 필드는 **정보 목록**으로 나간다.

    구 `test_missing_required_blocks`의 의미를 뒤집어 살렸다(회귀 커버리지 유지):
    같은 입력에 같은 관심사(무엇이 안 채워졌나)를 보되, 판정이 아니라 정보임을 단정한다.
    `blocked_reason` 문자열 안에만 있던 정보가 구조화 목록으로 승격됐다.
    """
    profile = _profile(["점수"])  # score만 매핑 — 07 표준 필드 대부분이 비어 있다
    outcome = _infer(profile, provider=FakeMappingProvider())

    assert outcome.status is ImportStatus.PREVIEW_READY  # 차단하지 않는다
    assert "occurred_at" in outcome.preview.unmapped_target_fields
    assert "event_type" in outcome.preview.unmapped_target_fields
    assert "score" not in outcome.preview.unmapped_target_fields  # 매핑된 건 빠진다


def test_low_confidence_sets_needs_review_and_probing() -> None:
    profile = _profile(["점수"])  # score confidence 0.55 < 0.9
    outcome = _infer(profile, provider=FakeMappingProvider())
    assert outcome.needs_probing is True
    assert any(c.needs_review for c in outcome.preview.columns)


# ── 실패: LLM 미가용 → 전 컬럼 needs_review 수동 미리보기 ──


def test_llm_failure_falls_back_to_manual_preview() -> None:
    profile = _profile(["원생명", "반", "등원일", "상태", "동의"])
    outcome = _infer(profile, provider=FailingMappingProvider())
    assert outcome.status is ImportStatus.PREVIEW_READY  # 작업 실패로 안 떨어뜨린다(§3.2)
    assert all(c.target is None and c.needs_review for c in outcome.preview.columns)
    assert outcome.needs_probing is False
    # 폴백에도 정보 2필드가 채워진다 — 전 컬럼 미매핑이므로 여집합 = 표준 필드 전체
    from ai.import_mapping.inference import STANDARD_FIELDS

    assert set(outcome.preview.unmapped_target_fields) == set(STANDARD_FIELDS)
    assert outcome.preview.source_fingerprint


# ── reused: 시그니처 캐시 hit → LLM 0회 ────────────────────


def test_reused_from_cache_skips_provider() -> None:
    profile = _profile(["원생명", "반", "등원일", "상태", "동의"])
    seed = confirmed_cache_entry(_infer(profile, provider=FakeMappingProvider()).preview)
    cache = InMemorySpecCache({form_signature(profile, "t1"): seed})
    outcome = _infer(profile, provider=FailingMappingProvider(), cache=cache)  # 실패 provider여도
    assert outcome.preview.reused is True  # 캐시로 재사용 — provider 미호출
    assert outcome.status is ImportStatus.PREVIEW_READY


def test_confirmed_cache_entry_roundtrip() -> None:
    cols = (MappingColumn(source="원생명", target="student_name", confidence=0.97),)
    from ai.contracts.imports import MappingPreview

    entry = confirmed_cache_entry(MappingPreview(spec_version=2, reused=False, columns=cols))
    assert entry.spec_version == 2 and entry.columns == cols
