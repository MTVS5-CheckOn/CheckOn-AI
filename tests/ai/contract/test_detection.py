"""contracts/detection.py 스모크 — enum 값 고정 · 왕복 직렬화 · 명세 §1·§4 정합.

값 고정 테스트의 목적은 몰래 변경 방지다. 이 파일이 깨지면 명세
(docs/part_a/09_detect_spec.md — 백엔드 전달 확정본)를 먼저 고쳐야 한다.
"""

import pytest

from ai.contracts.detection import (
    DISPLAY_LABELS,
    RULE_SIGNAL_MAP,
    AlertContextItem,
    AlertStatus,
    Brief,
    ClassRef,
    DetectRequest,
    DetectResponse,
    DetectStats,
    EventSource,
    EventType,
    EvidenceItem,
    EvidenceRole,
    Lifecycle,
    RuleId,
    RuleSkipped,
    Signal,
    SignalType,
    SnapshotMeta,
    StudentInput,
    StudentStatus,
    TermContext,
)


def _signal(
    signal_type: SignalType = SignalType.HIDDEN_RISK,
    rule_id: RuleId = RuleId.R4,
    display_label: str = "숨은 위기",
    lifecycle: Lifecycle = Lifecycle.NEW,
) -> Signal:
    return Signal(
        signal_id="0a1b2c3d",
        student_ref="st_8f2a",
        class_ref="cl_a1",
        rule_id=rule_id,
        signal_type=signal_type,
        display_label=display_label,
        score=0.78,
        rank=1,
        lifecycle=lifecycle,
        brief=Brief(text="비문학 지문 시간이 늘고 있어요", gate_passed=True, fallback_used=False),
        evidence=(
            EvidenceItem(
                source_table="learning_event",
                record_id="le_1029",
                summary="지연",
                role=EvidenceRole.TRIGGER,
            ),
        ),
    )


def _signal_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "signal_id": "x",
        "student_ref": "st_1",
        "class_ref": "cl_a1",
        "rule_id": "R4",
        "signal_type": "hidden_risk",
        "display_label": "숨은 위기",
        "score": 0.5,
        "rank": 1,
        "lifecycle": "new",
        "brief": {"text": "t", "gate_passed": True, "fallback_used": False},
        "evidence": [
            {"source_table": "learning_event", "record_id": "le_1", "summary": "s"},
        ],
    }
    payload.update(overrides)
    return payload


# ── enum 값 고정 (명세 §1·§2·§4) ──


def test_signal_type_values_frozen() -> None:
    """명세 §1 — 신호 6종."""
    assert {s.value for s in SignalType} == {
        "acc_drop",
        "submit_drop",
        "volume_gap",
        "hidden_risk",
        "return_care",
        "type_bias",
    }


def test_rule_id_values_frozen() -> None:
    assert {r.value for r in RuleId} == {"R1", "R2", "R3", "R4", "R5", "R6"}


def test_lifecycle_values_frozen() -> None:
    """명세 §4 — 억제(제외)는 값이 아니라 배열 부재로 표현하므로 3값뿐."""
    assert {lc.value for lc in Lifecycle} == {"new", "ongoing", "follow_up"}


def test_term_context_values_frozen() -> None:
    assert {t.value for t in TermContext} == {"normal", "new_term", "vacation"}


def test_student_status_values_frozen() -> None:
    assert {s.value for s in StudentStatus} == {"enrolled", "paused", "returned"}


def test_event_type_values_frozen() -> None:
    assert {e.value for e in EventType} == {"solve", "submit", "attend", "consult"}


def test_event_source_values_frozen() -> None:
    """우리가 **허용하는** 값의 집합 — 안쪽 축이다.

    ⚠ **상대가 실제로 보내는 값이 이 안에 드는지는 여기서 못 본다** — 그 축은
    `test_backend_emitted_input_values.py`가 따로 문다(PR-τ). 두 축이 다른 것이라
    한쪽만 있으면 계약 검사가 전부 green인 채로 첫 실왕복이 400으로 죽는다.

    🔴 `MANUAL`은 **백엔드 서버 소유 라벨의 한시 호환값**이다(2026-08-13).
    백엔드가 `source_type → source` 화이트리스트 매핑을 배포하면 `EventSource.MANUAL`과
    저쪽 픽스처의 `"MANUAL"`을 **같이** 지운다 — 그때 이 리터럴도 함께 줄어든다.
    """
    assert {e.value for e in EventSource} == {
        "trackA",
        "trackB",
        "studentHome",
        "MANUAL",
    }


def test_alert_status_values_frozen() -> None:
    assert {s.value for s in AlertStatus} == {"open", "resolved"}


def test_display_labels_frozen() -> None:
    """명세 §1 표 — 화면 표시 한글 문구 (AI 확정)."""
    assert DISPLAY_LABELS == {
        SignalType.ACC_DROP: "정답률 하락",
        SignalType.SUBMIT_DROP: "제출 저조",
        SignalType.VOLUME_GAP: "학습 공백",
        SignalType.HIDDEN_RISK: "숨은 위기",
        SignalType.RETURN_CARE: "복귀 케어",
        SignalType.TYPE_BIAS: "유형 편중",
    }


def test_display_labels_cover_all_signal_types() -> None:
    """새 signal_type이 라벨 없이 추가되는 것을 막는다."""
    assert set(DISPLAY_LABELS) == set(SignalType)


def test_rule_signal_map_is_one_to_one() -> None:
    """명세 §1 표 — rule_id ↔ signal_type 1:1."""
    assert set(RULE_SIGNAL_MAP) == set(RuleId)
    assert set(RULE_SIGNAL_MAP.values()) == set(SignalType)


# ── 왕복 직렬화 ──


def test_detect_request_roundtrip() -> None:
    request = DetectRequest(
        snapshot_meta=SnapshotMeta(
            week_start="2026-07-13",
            snapshot_hash="sha256:9f2c",
            term_context=TermContext.NORMAL,
            classes=(ClassRef(class_ref="cl_a1"),),
        ),
        students=(
            StudentInput(
                student_ref="st_8f2a",
                class_ref="cl_a1",
                enrolled_weeks=14,
                status=StudentStatus.ENROLLED,
                consent="granted",
            ),
        ),
        alert_context=(
            AlertContextItem(
                student_ref="st_8f2a",
                signal_type=SignalType.HIDDEN_RISK,
                status=AlertStatus.OPEN,
                resolved_at=None,
                followed_up=False,
            ),
        ),
    )
    assert DetectRequest.model_validate(request.model_dump(mode="json")) == request


def test_detect_response_roundtrip() -> None:
    response = DetectResponse(
        signals=(_signal(),),
        stats=DetectStats(
            students_evaluated=58,
            signals_raised=3,
            excluded_under_2w=4,
            capped_out=2,
            rules_skipped=(RuleSkipped(rule_id=RuleId.R4, reason="duration_missing", students=5),),
        ),
    )
    assert DetectResponse.model_validate(response.model_dump(mode="json")) == response


# ── 불변식·정합 ──


def test_signal_requires_evidence() -> None:
    """evidence 없는 신호는 스키마상 불가 (불변식 2)."""
    with pytest.raises(ValueError, match="evidence"):
        Signal.model_validate(_signal_payload(evidence=[]))


def test_display_label_must_match_signal_type() -> None:
    """AI가 만드는 신호는 라벨이 항상 확정 문구와 일치한다 (명세 §1)."""
    with pytest.raises(ValueError, match="display_label"):
        _signal(signal_type=SignalType.HIDDEN_RISK, display_label="잠재 위험")


def test_rule_id_must_match_signal_type() -> None:
    """rule_id ↔ signal_type 1:1 위반 차단 (명세 §1)."""
    with pytest.raises(ValueError, match="rule_id"):
        _signal(signal_type=SignalType.ACC_DROP, rule_id=RuleId.R4, display_label="정답률 하락")


def test_request_rejects_real_name_field() -> None:
    """실명 등 정의되지 않은 필드 차단 — extra='forbid' (불변식 3)."""
    with pytest.raises(ValueError, match="student_name"):
        StudentInput.model_validate(
            {
                "student_ref": "st_1",
                "class_ref": "cl_a1",
                "enrolled_weeks": 14,
                "status": "enrolled",
                "consent": "granted",
                "student_name": "홍길동",
            },
        )


def test_week_start_rejects_non_iso_date() -> None:
    """week_start ISO date 엄격 검증 (09 §2 A판정) — 오타는 거부."""
    with pytest.raises(ValueError, match="week_start"):
        SnapshotMeta.model_validate(
            {
                "week_start": "2026-13-99",
                "snapshot_hash": "sha256:x",
                "term_context": "normal",
                "classes": [{"class_ref": "cl_a1"}],
            },
        )


def test_alert_resolved_requires_resolved_at() -> None:
    """resolved면 resolved_at 필수 (09 §2 A판정)."""
    with pytest.raises(ValueError, match="resolved_at"):
        AlertContextItem.model_validate(
            {
                "student_ref": "st_1",
                "signal_type": "acc_drop",
                "status": "resolved",
                "resolved_at": None,
                "followed_up": False,
            },
        )


def test_alert_open_forbids_resolved_at() -> None:
    """open이면 resolved_at 부재 (09 §2 A판정)."""
    with pytest.raises(ValueError, match="resolved_at"):
        AlertContextItem.model_validate(
            {
                "student_ref": "st_1",
                "signal_type": "acc_drop",
                "status": "open",
                "resolved_at": "2026-07-06T00:00:00+09:00",
                "followed_up": False,
            },
        )


def test_response_has_no_observed_only() -> None:
    """observed_only 제거 확정 (명세 §3) — 응답에 학생 목록 필드가 없다."""
    assert "observed_only" not in DetectResponse.model_fields
    assert "excluded_under_2w" in DetectStats.model_fields


def test_rank_must_be_positive() -> None:
    """rank는 1 이상 — 0/음수는 순위가 될 수 없다."""
    with pytest.raises(ValueError, match="rank"):
        Signal.model_validate(_signal_payload(rank=0))


def test_score_out_of_range_rejected() -> None:
    with pytest.raises(ValueError, match="score"):
        Signal.model_validate(_signal_payload(score=1.5))
