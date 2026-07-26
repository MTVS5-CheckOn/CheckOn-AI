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
    detect_kind,
    infer_mapping,
    missing_required,
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


def test_detect_kind_and_missing_required() -> None:
    assert detect_kind(frozenset({"student_name"})) == "roster"
    assert detect_kind(frozenset({"score"})) == "learning"
    assert missing_required(frozenset({"occurred_at", "event_type"})) == frozenset()
    assert missing_required(frozenset({"score"})) == frozenset({"occurred_at", "event_type"})


# ── 정상: 명부 전 필수 매핑 → preview_ready ────────────────


def test_roster_all_required_mapped_preview_ready() -> None:
    profile = _profile(["원생명", "반", "등원일", "상태", "동의"])
    outcome = _infer(profile, provider=FakeMappingProvider())
    assert outcome.status is ImportStatus.PREVIEW_READY
    assert outcome.preview.blocked is False
    assert outcome.preview.reused is False


# ── 경계: 필수 미매핑 → blocked ────────────────────────────


def test_missing_required_blocks() -> None:
    profile = _profile(["점수"])  # score만 → learning 필수(occurred_at·event_type) 미충족
    outcome = _infer(profile, provider=FakeMappingProvider())
    assert outcome.status is ImportStatus.BLOCKED
    assert outcome.preview.blocked is True
    assert "occurred_at" in (outcome.preview.blocked_reason or "")


def test_low_confidence_sets_needs_review_and_probing() -> None:
    profile = _profile(["점수"])  # score confidence 0.55 < 0.9
    outcome = _infer(profile, provider=FakeMappingProvider())
    assert outcome.needs_probing is True
    assert any(c.needs_review for c in outcome.preview.columns)


# ── 실패: LLM 미가용 → 전 컬럼 needs_review 수동 미리보기 ──


def test_llm_failure_falls_back_to_manual_preview() -> None:
    profile = _profile(["원생명", "반", "등원일", "상태", "동의"])
    outcome = _infer(profile, provider=FailingMappingProvider())
    assert outcome.status is ImportStatus.PREVIEW_READY  # blocked 아님(§3.2)
    assert outcome.preview.blocked is False
    assert all(c.target is None and c.needs_review for c in outcome.preview.columns)
    assert outcome.needs_probing is False


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
