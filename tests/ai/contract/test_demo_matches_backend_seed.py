"""데모 스냅숏이 **백엔드 시드와 같은 모양**인지 (99 #66).

🔴 **2026-08-14 — 시나리오가 다르면 대조 자체가 성립하지 않았다.**

    시드(승우님께 넘김)   2026-08-10 · 17명 · 12주 · cl_a1 9 / cl_b2 7 / 미배정 1
    demo_snapshot.py     2026-07-20 · 10명 · 10주

숫자가 달라도 그게 **백엔드 버그인지 시나리오 차이인지 갈리지 않는다.** 나란히 놓으려면
모양이 같아야 한다.

━━ 🔴 `student_ref` 로는 대조할 수 없다 ━━

    데모     student_ref = "st_01" … "st_17"
    어댑터   ^st_[0-9a-f]{32}$        ← "st_01"은 백엔드 경로에서 막힌다

⇒ **비교 기준은 「시나리오 위치」다.** `demo_students()` 순서를 시드 `students.csv` 순서와
같게 두었으므로 **매핑표 없이 위치로 읽힌다**(1번째 학생 ↔ 1번째 행).
🔴 **`student_ref` 문자열을 단언하지 않는다.**
"""

from __future__ import annotations

from collections import Counter
from typing import Final

from ai.detection.engine import detect
from ai.evaluation.demo_snapshot import WEEK, WEEKS, build_demo_request, demo_students

#: 시드 구성 — 승우님께 넘긴 `students.csv` 의 모양.
_SEED_WEEK: Final = "2026-08-10"
_SEED_WEEKS: Final = 12
_SEED_CLASS_SIZES: Final = {"cl_a1": 9, "cl_b2": 7, "cl_unassigned": 1}


def test_the_demo_has_the_same_shape_as_the_backend_seed() -> None:
    """학생 수·반 배치·기준 주·주차 수가 시드와 같다.

    ⚠ `student_ref` 값은 비교하지 않는다 — 어댑터가 `st_` + 32hex 를 강제해 데모의
    `st_01` 과 다르다. **위치와 모양**으로 비교한다.
    """
    plans = demo_students()

    assert WEEK == _SEED_WEEK
    assert WEEKS == _SEED_WEEKS
    assert len(plans) == sum(_SEED_CLASS_SIZES.values())
    assert Counter(plan.class_ref for plan in plans) == _SEED_CLASS_SIZES
    assert {plan.weeks for plan in plans} == {_SEED_WEEKS}, "주차 수가 학생마다 다르다"


def test_the_demo_exercises_every_rule_and_every_exclusion() -> None:
    """🔴 데모 하나로 **6규칙 + 제외 경로 전부**를 태운다.

    하나라도 안 뜨면 그 학생 설계가 시드와 어긋난 것이다 — 대조할 때 «백엔드가 안 낸 건지
    우리가 안 낸 건지» 를 다시 물어야 한다.
    """
    response = detect(build_demo_request())
    fired = {signal.rule_id.value for signal in response.signals}

    assert fired == {"R1", "R2", "R3", "R4", "R5", "R6"}, sorted(fired)
    #: 🔴 상한 초과가 실제로 일어난다 — `cap_max=5` 를 넘는 반이 있어야 이 축이 산다
    assert response.stats.capped_out > 0
    #: 재원 2주 미만 1명(신규생)
    assert response.stats.excluded_under_2w == 1
    #: `duration_sec=0` 학생 하나 → R4 판정 불가
    assert any(
        row.reason == "duration_missing" for row in response.stats.rules_skipped
    ), response.stats.rules_skipped


def test_the_excluded_students_appear_nowhere_in_the_response() -> None:
    """🔴 미동의·휴원 학생은 **판정·근거 어디에도** 없다.

    ⚠ 「신호가 안 났다」와 「응답에 흔적이 없다」는 다른 사실이다 — 근거 목록에 record_id 가
    남으면 그것도 노출이다(불변식 3의 같은 방향).
    """
    request = build_demo_request()
    response = detect(request)

    excluded = {
        plan.student_ref
        for plan in demo_students()
        if plan.consent != "granted" or plan.status.value == "paused"
    }
    assert excluded, "제외 대상 학생이 설계에 없다 — 이 검사가 눈이 멀었다"

    cited = {signal.student_ref for signal in response.signals}
    assert not (cited & excluded)

    #: 그 학생들의 학습 기록 id 가 남의 근거에 섞이지도 않는다
    their_records = {
        event.record_id
        for event in request.learning_events
        if event.student_ref in excluded
    }
    all_cited_records = {
        item.record_id for signal in response.signals for item in signal.evidence
    }
    assert not (their_records & all_cited_records)


def test_the_unassigned_student_keeps_the_backend_class_alias() -> None:
    """반 미배정 학생이 `cl_unassigned` 그대로 흐른다.

    ⚠ 이 값은 **백엔드가 쓰는 문자열**이다 — 8/13 리허설에서 400 을 냈던 그 값이고
    (`cl_unassigned` 가 `^cl_[0-9a-f]{32}$` 를 안 만족했다), 데모가 그 경로를 태워야
    다음에 같은 일이 나면 여기서 먼저 걸린다.
    """
    response = detect(build_demo_request())

    unassigned = [s for s in response.signals if s.class_ref == "cl_unassigned"]
    assert unassigned, "미배정 학생이 신호를 안 냈다 — 그 경로가 안 태워진다"
