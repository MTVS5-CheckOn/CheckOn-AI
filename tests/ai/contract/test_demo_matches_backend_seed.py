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

━━ 🔴 (2026-08-14) 이 파일은 **시드를 안 보고 있었다** (99 #68) ━━

종전에는 시드가 저장소에 없어 `_SEED_CLASS_SIZES` 처럼 **여기에 손으로 적은 숫자**와만
대조했다. ⇒ **이름이 약속하는 것을 못 지켰고, 데모가 시드와 갈렸는데 아무도 몰랐다.**
지금은 `docs/part_a/examples/seed/students.csv` 를 **읽어서** 대조한다.

⚠ **손으로 적은 숫자와의 대조는 「같은 사람이 두 번 적은 것」이다** — 틀리면 둘 다 틀린다.
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import Final

import pytest

from ai.detection.engine import detect
from ai.evaluation.demo_snapshot import WEEK, WEEKS, build_demo_request, demo_students

#: 🔴 정본 — 승우님께 넘긴 시드 그대로(2026-08-13). 반입 경위는 그 폴더 `README.md`.
_SEED_CSV: Final = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "part_a"
    / "examples"
    / "seed"
    / "students.csv"
)

#: 시드 구성 중 **CSV 로 표현되지 않는 것**만 여기 둔다.
#: ⚠ `students.csv` 에 기준 주·주차 수 열이 없다 — `checkon_seed.sql` 의 `\set monday`
#:   와 주차 배열이 정본이고, 그 값을 옮겨 적은 것이다.
#: 🔴 그 SQL 은 **저장소에 없다**(2026-08-13 전달분 · 99 #68 재조정). 즉 아래 두 값은
#:   여전히 **손으로 옮겨 적은 값**이고 검사가 원본을 읽어 확인하지 않는다.
_SEED_WEEK: Final = "2026-08-10"
_SEED_WEEKS: Final = 12

#: 시드의 반 미배정 표기 ↔ 백엔드가 실제로 보내는 alias.
#: ⚠ 이 변환이 필요한 것 자체가 사실이다 — CSV 는 사람이 읽는 표기(`(미배정)`)를 쓰고
#:   요청 경로는 `cl_unassigned` 를 쓴다(8/13 리허설에서 400 을 냈던 그 값).
_UNASSIGNED: Final = "cl_unassigned"


def _seed_rows() -> list[dict[str, str]]:
    """🔴 시드 CSV 를 **읽는다** — 이 파일에 숫자를 옮겨 적지 않는다."""
    with _SEED_CSV.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows, f"시드를 못 읽었다: {_SEED_CSV} — 검사 경로가 끊기면 통과가 아니라 실패다"
    return rows


def _seed_class(alias: str) -> str:
    return alias if alias.startswith("cl_") else _UNASSIGNED


def test_the_demo_has_the_same_shape_as_the_backend_seed() -> None:
    """학생 수·반 배치·기준 주·주차 수가 시드와 같다.

    ⚠ `student_ref` 값은 비교하지 않는다 — 어댑터가 `st_` + 32hex 를 강제해 데모의
    `st_01` 과 다르다. **위치와 모양**으로 비교한다.
    """
    rows = _seed_rows()
    plans = demo_students()

    assert WEEK == _SEED_WEEK
    assert WEEKS == _SEED_WEEKS
    assert len(plans) == len(rows)
    assert Counter(plan.class_ref for plan in plans) == Counter(
        _seed_class(row["class_alias"]) for row in rows
    )
    assert {plan.weeks for plan in plans} == {_SEED_WEEKS}, "주차 수가 학생마다 다르다"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "🔴 데모가 시드와 갈려 있다 (2026-08-14 실측 · 폴더 README §3). "
        "① 평가 학생 14 vs 시드 기대 15 — 데모에만 PAUSED 가 1명 있어 하나 더 빠진다"
        "(시드에는 PAUSED 가 0명이라 휴원 제외 경로가 시드로는 검증되지 않는다). "
        "② type_bias 2 vs 시드 기대 1 — 시드가 st_15 를 「R6 skip · tagging_below_60pct」로 "
        "겨냥했는데 `fake_snapshot._cell_for()` 가 **모든 문항에 type_tag 를 채워** 태그 누락을 "
        "만들 수 없어 그 학생이 skip 대신 정상 발화한다 (99 #67). "
        "🔴 **어느 쪽을 맞출지 아직 정하지 않았다**(99 #71) — 데모를 시드에 맞추면 "
        "휴원 제외 경로가 아무 데서도 검증되지 않고, 시드에 PAUSED 를 넣으면 승우님이 "
        "시드를 다시 넣어야 한다. 시드 2판을 낼 때 같이 정한다. "
        "⚠ 「고치면 되는데 안 고친 것」이 아니다. "
        "⚠ strict=True 다 — 데모를 고치면 XPASS 로 red 가 나서 이 마커를 지우게 된다."
    ),
)
def test_the_demo_matches_the_seed_student_axes() -> None:
    """🔴 status·consent 분포와 **판정 결과 수**가 시드와 같다.

    ⚠ 위 검사(모양)는 통과하는데 여기가 갈린다 — **모양이 같아도 축이 다르면 대조가
    성립하지 않는다.** 8/14 에 승우님 E2E 와 숫자를 나란히 놓고서야 드러났다.
    """
    rows = _seed_rows()
    plans = demo_students()

    assert Counter(plan.status.value for plan in plans) == Counter(
        row["status"].lower() for row in rows
    ), "status 분포가 시드와 다르다 (시드에는 PAUSED 가 없다)"
    assert Counter(plan.consent for plan in plans) == Counter(
        row["consent"] for row in rows
    )

    #: 시드 기대 = 전체 − 재원 2주 미만 − 미동의
    expected = len(rows) - sum(
        1
        for row in rows
        if int(row["relationship_started_weeks_ago"]) < 2 or row["consent"] != "granted"
    )
    response = detect(build_demo_request())
    assert response.stats.students_evaluated == expected, (
        f"평가 학생 수가 시드 기대({expected})와 다르다"
    )

    fired = Counter(signal.signal_type.value for signal in response.signals)
    assert fired["type_bias"] == 1, (
        "type_bias 가 시드 기대(1건 + R6 skip 1건)와 다르다 — R6 skip 이 안 만들어진다"
    )


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
