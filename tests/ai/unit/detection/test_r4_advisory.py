"""R4 강등 — advisory(동시 진단 보조). 판정 불변 · 소비만 변경 (04 §1 R4 재정의 · 13 §3).

바뀌는 것은 **산출물의 소비 방식**뿐이다: ⓐ TOP N 랭킹 비참여 ⓑ 상한 슬롯 미소비
(`capped_out` 미산입) ⓒ 응답에서 `advisory: true`로 구분 ⓓ **판정 자체는 무변경**.

⚠ **R4가 다른 규칙과 함께 발화하면 그 경보는 정식이다** — advisory는 **병합된 규칙이
전부 R4일 때만** 선다. R1+R4 병합 경보를 참고 표시로 내리면 R1 근거가 알림에서 사라진다.
"""

from __future__ import annotations

from ai.contracts.detection import DetectResponse, RuleId
from ai.detection.engine import detect
from ai.evaluation.fake_snapshot import StudentPlan, build_detect_request

WEEK_START = "2026-07-13"

#: baseline 180 대비 최근 2주 ~1.67~1.78배 — 04 §1 R4 `time_ratio: 1.5` 초과.
_SPIKE = (180,) * 8 + (300, 320)


def _r4_only(ref: str, class_ref: str = "cl_a1") -> StudentPlan:
    """R4 단독 발화 — 정답률은 유지(하락 없음)라 R1은 안 뜬다."""
    return StudentPlan(
        student_ref=ref,
        class_ref=class_ref,
        weeks=10,
        solves_per_week=12,
        accuracy=0.80,
        duration_sec=_SPIKE,
        passage_word_count=800,
    )


def _r1_only(ref: str, class_ref: str = "cl_a1") -> StudentPlan:
    """R1 단독 발화 — 정답률 20%p 하락, 시간은 평탄."""
    return StudentPlan(
        student_ref=ref,
        class_ref=class_ref,
        weeks=10,
        solves_per_week=20,
        accuracy=(0.8,) * 8 + (0.60, 0.60),
    )


def _detect(*plans: StudentPlan) -> DetectResponse:
    return detect(build_detect_request(week_start=WEEK_START, seed=1, students=list(plans)))


# ── ⓓ 판정 불변 — 먼저 고정한다 ────────────────────────────────


def test_r4_still_fires() -> None:
    """강등은 발화 여부를 건드리지 않는다(04 §1 — 판정식·evidence 무변경)."""
    signals = _detect(_r4_only("st_r4")).signals
    assert [s.rule_id for s in signals] == [RuleId.R4]


def test_r4_keeps_evidence() -> None:
    """참고 표시로 내려가도 근거는 그대로다(불변식 2)."""
    signal = _detect(_r4_only("st_r4")).signals[0]
    assert len(signal.evidence) >= 1


# ── ⓒ 응답에서 advisory로 구분된다 ─────────────────────────────


def test_r4_alone_is_advisory() -> None:
    """🔴 R4 단독 경보는 advisory다."""
    signal = _detect(_r4_only("st_r4")).signals[0]
    assert signal.advisory is True


def test_other_rules_are_not_advisory() -> None:
    """R1 등 나머지는 정식 경보다 — 기본값이 False임을 함께 고정한다."""
    signal = _detect(_r1_only("st_r1")).signals[0]
    assert signal.advisory is False


def test_r4_merged_with_r1_is_not_advisory() -> None:
    """🔴 R1+R4 병합 경보는 **정식**이다 — advisory로 내리면 R1 근거가 알림에서 사라진다."""
    both = StudentPlan(
        student_ref="st_both",
        class_ref="cl_a1",
        weeks=10,
        solves_per_week=20,
        accuracy=(0.8,) * 8 + (0.60, 0.60),  # R1
        duration_sec=_SPIKE,  # R4
        passage_word_count=800,
    )
    signals = _detect(both).signals
    assert len(signals) == 1, [s.rule_id for s in signals]
    assert signals[0].advisory is False


# ── ⓐⓑ 랭킹 비참여 · 상한 슬롯 미소비 ─────────────────────────


def test_r4_does_not_consume_cap_slots() -> None:
    """🔴 R4 3건 + 신규 5건에서 **신규가 하나도 잘리지 않는다**(cap_max=5)."""
    plans = [_r4_only(f"st_adv_{i}") for i in range(3)]
    plans += [_r1_only(f"st_new_{i}") for i in range(5)]
    response = _detect(*plans)

    formal = [s for s in response.signals if not s.advisory]
    advisory = [s for s in response.signals if s.advisory]
    assert len(formal) == 5, [s.student_ref for s in formal]
    assert len(advisory) == 3
    assert response.stats.capped_out == 0


def test_advisory_is_excluded_from_capped_out() -> None:
    """상한을 넘겨도 advisory는 `capped_out`에 안 든다 — 슬롯을 안 쓰니 잘릴 것도 없다."""
    plans = [_r4_only(f"st_adv_{i}") for i in range(7)]
    response = _detect(*plans)
    assert response.stats.capped_out == 0
    assert all(s.advisory for s in response.signals)
    assert len(response.signals) == 7  # 전부 실린다 — 상한 밖 합류


def test_advisory_ranks_after_formal_signals() -> None:
    """상한 밖 합류 — 정식 통과분 **뒤에** rank가 이어진다(ongoing·R5와 같은 축)."""
    plans = [_r4_only("st_adv_1"), _r1_only("st_new_1"), _r1_only("st_new_2")]
    signals = _detect(*plans).signals
    formal_ranks = [s.rank for s in signals if not s.advisory]
    advisory_ranks = [s.rank for s in signals if s.advisory]
    assert max(formal_ranks) < min(advisory_ranks)
