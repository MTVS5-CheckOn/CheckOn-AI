"""근거 해소 resolver — B 요구 조건 5개 중 1~4를 고정 (09 §2-12-②).

조건 5(blind 무오염)는 AST 계약 테스트가 따로 고정한다
(`tests/ai/contract/test_evidence_blind_isolation.py`).

**조건 1과 2의 경계를 둘로 갈라 검사한다:**
- 일부 제외 + 하나라도 해소 → `ResolvedEvidence` 반환(`excluded`에 사유)
- 해소 0건(전부 제외·전부 실패) → `EvidenceResolutionFailed` 예외
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from collections.abc import Coroutine
from types import ModuleType
from uuid import UUID

import pytest
from pydantic import ValidationError

from ai.contracts.graphrag import (
    EvidenceCoverage,
    EvidencePack,
    EvidencePackAnchor,
    EvidencePackAnchorKind,
    EvidenceRetrieval,
    EvidenceRetrievalMode,
)
from ai.evidence import models as evidence_models
from ai.evidence import resolver as evidence_resolver
from ai.evidence.models import EvidenceOwnerKind
from ai.evidence.resolver import (
    EvidenceResolutionFailed,
    EvidenceResolver,
    ExclusionReason,
    FakeEvidenceResolver,
    ResolvedEvidence,
    StoredEvidence,
    classify_anchor,
)
from ai.runtime.errors import DomainException

#: 조건 3·4 AST 검사 대상 — evidence 계층 전 모듈.
_EVIDENCE_MODULES = (evidence_resolver, evidence_models)

_PACK_ID = UUID("00000000-0000-4000-8000-00000000000b")
_OWNER = UUID("00000000-0000-4000-8000-00000000000a")
_HASH_A = "sha256:" + "a" * 64
_HASH_B = "sha256:" + "b" * 64
_QUOTE_HASH = "sha256:" + "c" * 64


def _run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


def _anchor(
    anchor_id: str,
    *,
    quote: str | None = None,
    content_hash: str = _HASH_A,
) -> EvidencePackAnchor:
    return EvidencePackAnchor(
        anchor_id=anchor_id,
        kind=EvidencePackAnchorKind.GRAMMAR_RULE,
        ref=f"grammar:{anchor_id}",
        source_id="src-1",
        source_version="v1",
        source_content_hash=content_hash,
        quote=quote,
        quote_hash=_QUOTE_HASH if quote is not None else None,
        license_ref="lic-1",
    )


def _pack(*anchors: EvidencePackAnchor) -> EvidencePack:
    return EvidencePack(
        evidence_pack_id=_PACK_ID,
        graph_snapshot_id="snap-1",
        anchors=anchors,
        coverage=(
            EvidenceCoverage(
                item_field="answer.correct_no",
                supporting_anchor_ids=tuple(a.anchor_id for a in anchors),
            ),
        ),
        retrieval=EvidenceRetrieval(mode=EvidenceRetrievalMode.LOCAL, query_hash=_HASH_B),
        evidence_pack_hash=_HASH_B,
    )


def _resolve(
    resolver: FakeEvidenceResolver,
    pack: EvidencePack,
    anchor_ids: tuple[str, ...],
) -> ResolvedEvidence:
    return _run(
        resolver.resolve(
            pack=pack,
            anchor_ids=anchor_ids,
            tenant_id="t1",
            owner_kind=EvidenceOwnerKind.PROBLEM_ITEM,
            owner_id=_OWNER,
        )
    )


def test_fake_satisfies_protocol() -> None:
    assert isinstance(FakeEvidenceResolver(), EvidenceResolver)


# ── 조건 1: fail-closed ─────────────────────────────────────────


def test_zero_resolution_raises_instead_of_empty_result() -> None:
    """해소 0건은 **예외**다 — 빈 결과를 조용한 성공으로 돌려주지 않는다."""
    pack = _pack(_anchor("a1"))
    resolver = FakeEvidenceResolver(store={})  # 저장소에 아무것도 없음

    with pytest.raises(EvidenceResolutionFailed) as excinfo:
        _resolve(resolver, pack, ("a1",))

    assert isinstance(excinfo.value, DomainException)  # 상위가 도메인 예외로 받는다
    detail = excinfo.value.detail
    assert isinstance(detail, dict)
    assert detail["excluded"][0]["reason"] == ExclusionReason.NOT_FOUND.value


def test_unknown_anchor_id_is_not_silently_ignored() -> None:
    """Pack에 없는 앵커를 달라고 해도 빈 성공이 아니라 실패로 수렴한다."""
    pack = _pack(_anchor("a1"))
    stored = StoredEvidence(source_content_hash=_HASH_A, summary="s")
    resolver = FakeEvidenceResolver(store={"a1": stored})

    with pytest.raises(EvidenceResolutionFailed):
        _resolve(resolver, pack, ("nope",))


def test_empty_resolved_tuple_is_structurally_impossible() -> None:
    """타입이 빈 성공을 막는다 — resolved는 min_length=1."""
    with pytest.raises(ValidationError):
        ResolvedEvidence(evidence_pack_id=_PACK_ID, resolved=())


# ── 조건 2: 권리 게이트 (+ 제외 사실이 결과에 드러남) ──────────────


def test_rights_status_is_enforced_by_contract_type() -> None:
    """1차 방어 — Pack 생성 시점에 approved 외 값이 거부된다(11 §4.2 불변식 ①)."""
    with pytest.raises(ValidationError):
        EvidencePackAnchor(
            anchor_id="a1",
            kind=EvidencePackAnchorKind.GRAMMAR_RULE,
            ref="grammar:a1",
            source_id="src-1",
            source_version="v1",
            source_content_hash=_HASH_A,
            license_ref="lic-1",
            rights_status="pending",
        )


def test_resolver_double_checks_rights_status() -> None:
    """2차 방어 — 판정 함수는 타입을 우회한 값도 제외 사유로 분류한다(이중 확인)."""
    anchor = _anchor("a1").model_copy(update={"rights_status": "expired"})
    assert classify_anchor(anchor, StoredEvidence(source_content_hash=_HASH_A, summary="s")) is (
        ExclusionReason.RIGHTS_NOT_APPROVED
    )


def test_partial_exclusion_surfaces_reasons_and_still_resolves() -> None:
    """일부 제외돼도 하나라도 해소되면 성공 — 단 제외 사실이 결과에 드러난다."""
    pack = _pack(
        _anchor("ok"),
        _anchor("hash_bad", content_hash=_HASH_B),
        _anchor("quote_bad", quote="원문 A"),
        _anchor("missing"),
    )
    resolver = FakeEvidenceResolver(
        store={
            "ok": StoredEvidence(source_content_hash=_HASH_A, summary="통과 근거"),
            "hash_bad": StoredEvidence(source_content_hash=_HASH_A, summary="해시 불일치"),
            "quote_bad": StoredEvidence(
                source_content_hash=_HASH_A, summary="인용 불일치", quote="원문 B"
            ),
        }
    )

    result = _resolve(resolver, pack, ("ok", "hash_bad", "quote_bad", "missing"))

    assert [item.anchor_id for item in result.resolved] == ["ok"]
    assert {item.anchor_id: item.reason for item in result.excluded} == {
        "hash_bad": ExclusionReason.CONTENT_HASH_MISMATCH,
        "quote_bad": ExclusionReason.QUOTE_MISMATCH,
        "missing": ExclusionReason.NOT_FOUND,
    }


def test_exclusion_reasons_cover_required_four() -> None:
    """B가 요구한 최소 4종이 전부 존재한다."""
    assert {reason.value for reason in ExclusionReason} >= {
        "rights_not_approved",
        "quote_mismatch",
        "content_hash_mismatch",
        "not_found",
    }


# ── 조건 3: 결정론 ───────────────────────────────────────────────


def test_same_input_yields_identical_result() -> None:
    """같은 (EvidencePack, anchor) → 같은 결과. 바이트 동일."""
    pack = _pack(_anchor("a1"), _anchor("a2", content_hash=_HASH_B))
    store = {"a1": StoredEvidence(source_content_hash=_HASH_A, summary="근거")}

    first = _resolve(FakeEvidenceResolver(store=store), pack, ("a1", "a2"))
    second = _resolve(FakeEvidenceResolver(store=store), pack, ("a1", "a2"))

    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def _imported_names(module: ModuleType) -> set[str]:
    """모듈이 실제로 import한 이름 — 주석·docstring 언급은 세지 않는다(AST)."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names |= {f"{node.module}.{alias.name}" for alias in node.names}
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
    return names


def _called_attributes(module: ModuleType) -> set[str]:
    """`a.b(...)` 형태로 실제 호출된 속성 이름."""
    calls: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                calls.add(func.attr)
            elif isinstance(func, ast.Name):
                calls.add(func.id)
    return calls


@pytest.mark.parametrize("module", _EVIDENCE_MODULES)
def test_module_has_no_clock_or_random(module: ModuleType) -> None:
    """시계·난수를 evidence 계층에서 만들지 않는다(주입 원칙 · 03 §1)."""
    assert not {"random", "secrets"} & _imported_names(module)
    banned_calls = {"now", "utcnow", "time", "monotonic", "uuid4", "uuid1", "choice"}
    assert not banned_calls & _called_attributes(module)


# ── 조건 4: 예산 불변 (재시도 루프 없음) ──────────────────────────


@pytest.mark.parametrize("module", _EVIDENCE_MODULES)
def test_no_retry_library_in_evidence_layer(module: ModuleType) -> None:
    """tenacity를 붙이지 않는다 — 문항당 item_attempt 공통 예산을 그대로 쓴다(FIX-06).

    docstring 언급이 아니라 **실제 import**만 본다.
    """
    assert not {name for name in _imported_names(module) if name.startswith("tenacity")}


def test_failure_does_not_retry_internally() -> None:
    """해소 실패에도 저장소를 재조회하지 않는다 — 호출 1회로 끝난다."""
    pack = _pack(_anchor("a1"))
    resolver = FakeEvidenceResolver(store={})

    with pytest.raises(EvidenceResolutionFailed):
        _resolve(resolver, pack, ("a1",))

    assert len(resolver.calls) == 1  # 내부 재시도 루프 없음
