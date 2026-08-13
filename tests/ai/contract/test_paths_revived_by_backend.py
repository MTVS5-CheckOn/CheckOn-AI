"""백엔드 수정으로 **처음 실데이터가 흐르게 될** 경로들의 기준선 (99 #66).

🔴 **2026-08-13 — 승우님이 백엔드를 다섯 군데 고치기로 했다**: `status` 상수(`"enrolled"`
고정) 제거 · `assignment_window` 생성 · `enrollment_transition` 생성 · `term_context` 상수
제거 · `learning_events` 8주 → 10주. 그 결과 아래 경로들이 **한꺼번에** 살아난다.

**여태 코드만 있고 실데이터가 안 흘렀다.** 이 파일이 없으면 그날 뭔가 깨져도 **새 결함인지
원래 그랬는지 알 수 없다.** ⇒ 지금 기준선을 값으로 박아 둔다.

━━ ⚠ 이 파일이 「없는 것만」 만든 이유 ━━

셋은 이미 충분히 검사되고 있어 **중복을 만들지 않았다**::

    returned → R5 발화        `unit/detection/test_engine.py` (return_care 발화 단언)
    전환이력 ↔ status 정합    `contract/test_detection_evidence_contract.py` (ValidationError)
    assignment_window → R2    `unit/detection/test_absence_*` (발화·skip 둘 다)

🔴 **다만 「검사는 있는데 그 규칙이 안 발화하는」 형태를 조심했다** — 99 #65에서 증분
동일성 검사가 정확히 그랬다(evidence를 값으로 비교하는데 그 시나리오에서 R3만 발화해서
`learning_event` 인용 축이 통째로 비었다). 여기서는 **단언 전에 발화 자체를 먼저 확인**한다.
"""

from __future__ import annotations

from typing import Final

import pytest
from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.contracts.detection import (
    DetectResponse,
    RuleId,
    StudentStatus,
    TermContext,
)
from ai.detection.baseline import compute_baseline
from ai.detection.engine import detect
from ai.detection.features import extract_features
from ai.detection.thresholds import ThresholdConfig
from ai.evaluation.fake_snapshot import (
    StudentPlan,
    build_detect_request,
    declining,
    to_payload,
)

_WEEK: Final = "2026-07-20"
_CONFIG: Final = ThresholdConfig()

#: 🔴 헤더는 라우터 계약이다 — 없으면 400이라 검사가 엉뚱한 이유로 통과한다.
_HEADERS: Final = {
    "X-Tenant-Id": "tn_revive",
    "X-Request-Id": "req-revive",
    "Idempotency-Key": "revive-1",
}


def _dropping(ref: str, *, status: StudentStatus = StudentStatus.ENROLLED) -> StudentPlan:
    """정답률이 꾸준히 떨어지는 학생 — R1이 서는 재료.

    🔴 **문항 수가 20인 이유** — 기본 8문항이면 정답률이 `n/8`로 **양자화**돼
    판정 창 첫 주의 하락폭이 14.1pp가 되고 임계 15.0을 못 넘는다(실측). 그러면 대조군이
    안 떠서 **검사가 조용히 아무것도 안 본다.** 20문항이면 16.2·21.2pp로 선다.
    ⚠ 그리고 그 16.2가 신학기 완화(×1.2 → 18.0)에서 **정확히 못 넘는 경계**라 ⑥의 재료도 된다.
    """
    return StudentPlan(
        student_ref=ref,
        class_ref="cl_a1",
        weeks=10,
        accuracy=declining(10, 0.90, 0.55),
        solves_per_week=20,
        status=status,
    )


def _rule_ids(response: DetectResponse, ref: str) -> set[RuleId]:
    return {s.rule_id for s in response.signals if s.student_ref == ref}


# ── ① status=paused → 판정에서 통째로 빠진다 ──────────────────────────


def test_a_paused_student_is_absent_from_every_output() -> None:
    """🔴 휴원 학생은 **판정·근거·집계 어디에도** 없다.

    ⚠ 종전 검사(`test_no_consent_and_paused_have_no_events`)는 **픽스처가 이벤트를 안
    만든다**는 것만 봤다 — 그건 생성기 성질이지 **엔진이 제외한다**는 보장이 아니다.
    백엔드가 `status` 상수를 걷어내면 처음으로 `paused`가 실제로 흘러온다.
    """
    request = build_detect_request(
        week_start=_WEEK,
        seed=1,
        students=[_dropping("st_on"), _dropping("st_off", status=StudentStatus.PAUSED)],
    )
    response = detect(request)

    assert _rule_ids(response, "st_on"), "대조군이 발화하지 않았다 — 이 검사가 눈이 멀었다"
    assert not _rule_ids(response, "st_off"), "휴원 학생이 발화했다"
    assert all(
        "st_off" not in item.record_id
        for signal in response.signals
        for item in signal.evidence
    )
    assert response.stats.students_evaluated == 1


# ── ③′ 전환이력 ↔ status 불일치가 **HTTP 400** 인가 ────────────────────


def test_a_returned_transition_without_returned_status_is_http_400() -> None:
    """🔴 계약 위반이 **응답 코드로** 400인지 고정한다.

    ⚠ `ValidationError`가 난다는 것은 이미 `test_detection_evidence_contract`가 본다.
    그러나 **라우터가 그것을 어떤 코드로 번역하는지는 응답으로만** 알 수 있다
    (`test_detect_api.py`가 같은 취지를 적어 뒀다).

    🔴 승우님이 `status`만 고치고 `enrollment_transition`을 안 보내면(또는 그 반대면)
    **여기서 400이 난다.** 그때 원인이 바로 보이게 지금 박아 둔다.
    """
    request = build_detect_request(
        week_start=_WEEK,
        seed=2,
        students=[_dropping("st_back", status=StudentStatus.RETURNED)],
    )
    payload = to_payload(request)
    assert any(
        row["kind"] == "enrollment_transition" for row in payload["detection_evidence"]
    ), "전환 이력이 애초에 없다 — 이 검사가 재려는 것이 없다"
    #: status만 되돌린다 = 백엔드가 한쪽만 고친 상태.
    for student in payload["students"]:
        student["status"] = StudentStatus.ENROLLED.value

    response = TestClient(create_app()).post("/v1/detect", json=payload, headers=_HEADERS)

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "INVALID_SCHEMA"


# ── ④′ expected_count == 0 인 주는 연속에서 빠진다 ─────────────────────


def test_a_week_without_assignments_does_not_break_the_missing_streak() -> None:
    """🔴 **과제가 없던 주는 미제출이 아니다** — 방학·휴강 주가 연속을 끊으면 안 된다.

    ⚠ 이 축은 검사가 없었다. `assignment_window`가 실제로 흘러오기 시작하면
    `expected_count=0` 행이 섞여 들어오는데, 그때 R2가 **끊기는지 이어지는지**가
    화면에 나오는 경보 수를 바꾼다.
    """
    #: 최근 4주 미제출인데 그 중 한 주는 **과제가 아예 없었다**.
    plan = StudentPlan(
        student_ref="st_gap",
        class_ref="cl_a1",
        weeks=10,
        submit_ok=(True,) * 6 + (False,) * 4,
        assignment_expected=(1,) * 7 + (0,) + (1,) * 2,
    )
    response = detect(build_detect_request(week_start=_WEEK, seed=3, students=[plan]))

    assert RuleId.R2 in _rule_ids(response, "st_gap"), (
        "과제 없던 주가 연속을 끊었다 — 방학 주가 미제출로 세어지고 있다"
    )


# ── ⑤ vacation 이면 R2·R3 가 아예 안 선다 ─────────────────────────────


def test_vacation_disables_the_submit_and_volume_rules() -> None:
    """🔴 방학이면 R2(미제출)·R3(학습량)는 **판정하지 않는다**(04 §2).

    ⚠ 종전 검사는 `TermContext` **enum 값 집합**과 생성기가 그 값을 싣는지만 봤다 —
    **분기가 실제로 도는지**는 아무도 안 봤다. `term_context` 상수가 걷히면 처음 흐른다.
    """
    plan = StudentPlan(
        student_ref="st_vac",
        class_ref="cl_a1",
        weeks=10,
        submit_ok=(True,) * 5 + (False,) * 5,
        solves_per_week=(10,) * 9 + (1,),
    )
    normal = detect(build_detect_request(week_start=_WEEK, seed=4, students=[plan]))
    vacation = detect(
        build_detect_request(
            week_start=_WEEK, seed=4, students=[plan], term_context=TermContext.VACATION
        )
    )

    fired = _rule_ids(normal, "st_vac") & {RuleId.R2, RuleId.R3}
    assert fired, "normal에서 R2·R3가 안 떴다 — 대조군이 없어 이 검사가 헛돈다"
    assert not (_rule_ids(vacation, "st_vac") & {RuleId.R2, RuleId.R3})


# ── ⑥ new_term 은 임계를 완화한다 ─────────────────────────────────────


def test_new_term_relaxes_thresholds_enough_to_change_a_verdict() -> None:
    """신학기 완화(×1.2)가 **판정을 실제로 바꾼다**.

    🔴 계수가 설정에 있다는 것(`new_term_relax == 1.2`)은 이미 검사된다. 여기서 보는 것은
    **그 계수가 엔진을 지나 결과를 바꾸는가**다 — 값이 있는 것과 쓰이는 것은 다르다.
    """
    #: normal에서 **겨우** 서는 하락폭(16.2pp ≥ 15.0)이라 완화(×1.2 → 18.0)에서는 못 넘는다.
    plan = _dropping("st_edge")
    normal = detect(build_detect_request(week_start=_WEEK, seed=5, students=[plan]))
    new_term = detect(
        build_detect_request(
            week_start=_WEEK, seed=5, students=[plan], term_context=TermContext.NEW_TERM
        )
    )

    assert RuleId.R1 in _rule_ids(normal, "st_edge"), "normal에서 R1이 안 떴다 — 대조군 없음"
    assert RuleId.R1 not in _rule_ids(new_term, "st_edge"), (
        "신학기 완화가 판정을 안 바꿨다 — 계수가 엔진까지 안 닿는다"
    )


# ── ⑦ baseline 이 설정 창을 다 쓰는가 ─────────────────────────────────


@pytest.mark.parametrize("weeks", [12, 11, 10])
def test_baseline_uses_the_full_configured_window_when_input_allows(weeks: int) -> None:
    """🔴 입력이 충분하면 기준선이 **설정 창을 다 쓴다**.

    ⚠ 숫자(`== 8`)를 박지 않는다 — 그러면 설정을 바꾼 테넌트에서 검사가 거짓이 된다.
    `config.baseline_window_weeks`에서 유도한다(03 §1 «값이 두 곳에 살면 안 된다»).
    """
    plan = StudentPlan(student_ref="st_b", class_ref="cl_a1", weeks=weeks)
    features = extract_features(build_detect_request(week_start=_WEEK, seed=6, students=[plan]))
    baseline = compute_baseline(features["st_b"], _CONFIG.baseline_window_weeks)

    assert baseline.weeks_used == _CONFIG.baseline_window_weeks


def test_a_short_input_yields_a_shorter_baseline_and_that_is_the_current_state() -> None:
    """🔴 **지금 백엔드가 보내는 8주에서는 기준선이 짧다** — 그 사실을 기록으로 남긴다.

    판정 창(`ASSESSMENT_WEEKS`)을 뺀 나머지가 기준선 창이므로, 8주 입력이면 설정 8주를
    다 못 채운다. 백엔드가 10주로 늘리면 이 검사의 부등호가 **등호로 바뀌어야** 하고,
    그때 이 검사가 red가 되어 «늘어난 것이 반영됐다»를 알려 준다.
    """
    plan = StudentPlan(student_ref="st_short", class_ref="cl_a1", weeks=8)
    features = extract_features(build_detect_request(week_start=_WEEK, seed=7, students=[plan]))
    baseline = compute_baseline(features["st_short"], _CONFIG.baseline_window_weeks)

    assert 0 < baseline.weeks_used < _CONFIG.baseline_window_weeks
