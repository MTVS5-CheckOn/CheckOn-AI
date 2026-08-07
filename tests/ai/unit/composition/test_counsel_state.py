"""counsel_pack state — §1.2 필드 1:1 값 대조 + 불변식 4개.

정본은 `docs/policies/langgraph_state.md` §1.2(⑨ 개정본)다. 필드 집합을 문서에서 손으로
옮겨 대조하므로 문서와 구현이 갈리면 여기서 깨진다(`probe/state.py` 대조 테스트 선례).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai.composition.counsel.state import (
    STATE_SCHEMA_VERSION,
    CounselPackState,
    EmptyEvidenceError,
    has_record_id,
    validate_emphasis_points,
)
from ai.contracts.composition import DraftStatus, StudentResult

_HASH = "sha256:" + "a" * 64

#: langgraph_state §1.2 CounselPackState 코드블록의 필드 집합(손 작성).
#: ✅ **(8/11 · 99 #07 해소) 「문서 → 코드」 방향은 이제 다른 파일이 지킨다** —
#: `tests/ai/contract/test_doc_enum_parity.py::test_documented_codeblock_fields_match_the_model`
#: 이 §1.2 코드블록을 **실제로 파싱해** `CounselPackState.model_fields`와 대조한다.
#: 붙이자마자 **진짜 갈림을 찾았다** — 문서에 `state_schema_version`이 빠져 있었고
#: 여기 손사본은 코드를 따라 그 값을 갖고 있어서 `코드 == 코드`로 조용했다.
#:
#: ⚠ **그래서 이 집합은 왜 남아 있나** — 여기가 지키는 것은 **필드 이름 집합이 아니라
#: 기본값·불변식**이다(아래 단정들). 이름 대조만 놓고 보면 위 파서와 중복이지만,
#: 중복이 **정본이 둘**이라는 뜻은 아니다 — 파서 쪽이 문서를 읽는 유일한 자리다.
#: 🔴 이름 집합이 갈리면 **두 파일이 같이 red**가 난다. 그때 고칠 곳은 문서다.
_DOC_FIELDS = {
    "state_schema_version",
    "tenant_id",
    "class_ref",
    "student_refs",
    "context_ref",
    "context_hash",
    "plan_version",
    "emphasis_points",
    "plan_outcome",
    "plan_dropped",
    "cursor",
    "results",
    "quota_consumed",
    "summary",
}


def _state(**overrides: object) -> CounselPackState:
    base: dict[str, object] = {
        "tenant_id": "t1",
        "class_ref": "cl_a1",
        "student_refs": ["st_1", "st_2", "st_3"],
        "context_ref": "context://11111111-1111-4111-8111-111111111111",
        "context_hash": _HASH,
        "plan_version": "0.1",
    }
    return CounselPackState(**(base | overrides))


def _result(student_ref: str, status: DraftStatus = DraftStatus.GENERATED) -> StudentResult:
    return StudentResult(student_ref=student_ref, status=status)


# ── §1.2 필드 1:1 대조 ────────────────────────────────────────────


def test_state_fields_match_doc_exactly() -> None:
    """필드 집합이 §1.2와 정확히 일치 — 누락도 초과도 없다."""
    assert set(CounselPackState.model_fields) == _DOC_FIELDS


def test_schema_version() -> None:
    assert STATE_SCHEMA_VERSION == "counsel_pack.v1"
    assert _state().state_schema_version == "counsel_pack.v1"


def test_state_has_no_context_or_draft_body() -> None:
    """⑨ 개정 — 본문 미복제. contexts·blocks·content 류 필드가 없어야 한다."""
    fields = set(CounselPackState.model_fields)
    assert not {"contexts", "blocks", "content", "draft_body", "prompt"} & fields
    assert {"context_ref", "context_hash"} <= fields


def test_state_is_frozen_and_forbids_extra() -> None:
    with pytest.raises(ValidationError):
        _state(unexpected=1)


# ── 불변식 ① emphasis_points 근거 동반 ────────────────────────────


def test_invariant_1_pure_validator_raises_empty_evidence() -> None:
    """순수 함수 직접 호출 — 도메인 예외 그대로."""
    with pytest.raises(EmptyEvidenceError, match="record_id"):
        validate_emphasis_points({"st_1": ["정답률이 올랐습니다"]})


def test_invariant_1_model_path_rejects_point_without_record_id() -> None:
    """모델 생성 경로 — Pydantic이 ValidationError로 감싼다."""
    with pytest.raises(ValidationError, match="record_id"):
        _state(emphasis_points={"st_1": ["정답률이 올랐습니다"]})


def test_invariant_1_accepts_point_with_record_id() -> None:
    state = _state(emphasis_points={"st_1": ["정답률 62%→71% (record_id=le_1029)"]})
    assert state.emphasis_points["st_1"]


def test_has_record_id_helper() -> None:
    assert has_record_id("근거 (record_id=le_1)")
    assert not has_record_id("근거 없음")


# ── 불변식 ② cursor 단조 증가·results 길이 일치 ───────────────────


def test_invariant_2_cursor_matches_results_length() -> None:
    state = _state(cursor=2, results=[_result("st_1"), _result("st_2")])
    assert state.cursor == len(state.results)


def test_invariant_2_mismatch_is_corrupted_checkpoint() -> None:
    with pytest.raises(ValidationError, match="체크포인트 손상"):
        _state(cursor=2, results=[_result("st_1")])


def test_invariant_2_cursor_cannot_exceed_students() -> None:
    with pytest.raises(ValidationError, match="체크포인트 손상"):
        _state(
            cursor=4,
            results=[_result(f"st_{i}") for i in range(4)],
        )


# ── 불변식 ③ 학생 1명 실패가 루프를 멈추지 않는다 ──────────────────


def test_invariant_3_failed_student_does_not_stop_loop() -> None:
    """failed·skipped가 섞여도 cursor는 계속 나아간다(완료분 보존)."""
    state = _state(
        cursor=3,
        results=[
            _result("st_1", DraftStatus.GENERATED),
            _result("st_2", DraftStatus.FAILED),
            _result("st_3", DraftStatus.REJECTED_INSUFFICIENT),
        ],
    )
    assert state.is_complete
    assert state.next_student() is None
    assert [r.status for r in state.results] == [
        DraftStatus.GENERATED,
        DraftStatus.FAILED,
        DraftStatus.REJECTED_INSUFFICIENT,
    ]


def test_next_student_follows_cursor() -> None:
    assert _state().next_student() == "st_1"
    assert _state(cursor=1, results=[_result("st_1")]).next_student() == "st_2"


# ── 불변식 ④ context_hash 형식 (대조는 worker가 수행) ──────────────


def test_invariant_4_context_hash_format_enforced() -> None:
    """sha256:<64 hex> — ProblemGenerationState.request_hash 대칭."""
    with pytest.raises(ValidationError):
        _state(context_hash="deadbeef")
    with pytest.raises(ValidationError):
        _state(context_hash="sha256:XYZ")


def test_context_ref_required_non_empty() -> None:
    with pytest.raises(ValidationError):
        _state(context_ref="")
