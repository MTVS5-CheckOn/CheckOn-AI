"""데모: 학생 17명 × 12주 가상 스냅숏 → 실제 HTTP 경로(POST /v1/detect) 실행.

🔴 **백엔드 시드와 같은 모양으로 맞췄다**(2026-08-14). 시드가 `2026-08-10 · 17명 · 12주 ·
cl_a1 9 / cl_b2 7 / 미배정 1`이라, 데모가 `2026-07-20 · 10명 · 10주`이던 동안에는
**«백엔드가 만든 요청»과 «우리 기대 요청»을 나란히 놓을 수 없었다** — 숫자가 달라도 그게
백엔드 버그인지 시나리오 차이인지 갈리지 않는다.

━━ 🔴 대조는 `student_ref` 로 못 한다 ━━

    데모     student_ref = "st_01" … "st_17"
    어댑터   ^st_[0-9a-f]{32}$        ← "st_01"은 백엔드 경로에서 막힌다

⇒ **대조 기준은 「시나리오 위치」다.** `demo_students()` 순서를 시드 `students.csv` 순서와
같게 두었으므로 **매핑표 없이 위치로 읽힌다**(1번째 학생 ↔ 1번째 행).
비교하는 것: 반 배치 · 학생 수 · 주차 수 · 학생별 주간 활동량·정답률 배열 · 발화 규칙 조합 ·
`stats`. **`student_ref` 값은 비교하지 않는다.**

━━ 🔴 백엔드 경로와 **다를 수밖에 없다** (2026-08-14 · 승우님 ④⑤ 수정 전) ━━

    submit_drop   데모 ✅ / 백엔드 ❌   assignment_window 를 안 싣는다
    return_care   데모 ✅ / 백엔드 ❌   status 가 "enrolled" 상수 · enrollment_transition 없음
    term_context  데모 지정 가능 / 백엔드 "normal" 고정
    기준선 주 수   데모 12주 / 백엔드 8주   ANALYSIS_DAYS = 56

⇒ **이 넷은 차이가 나는 게 정상이다.** 나머지가 맞으면 통과로 본다.
승우님 수정이 배포되면 이 절을 지운다(99 #66 재대조).

⚠ **표현하지 못한 시드 축이 하나 있다 — 태그 누락률.** `fake_snapshot`이 모든 solve에
`area_tag`·`type_tag`를 **반드시** 채워 `tagging_rate`가 항상 1.0이다. 그래서
`rules_skipped`에 `tagging_below_60pct`가 안 나온다(`duration_missing`은 `duration_sec=0`
으로 만들 수 있다). 🔴 **생성기를 고치지 않았다** — 그건 별건이다.

**평가·데모 전용** (평가 격리 — 02_ownership.md §5, A 소유). 프로덕션 capability는
이 모듈을 import하지 않는다. 포폴·백엔드 연동 예시용으로, FakeSnapshot 빌더로 요청을
조립하고 FastAPI TestClient(실제 라우터·envelope·멱등 경로)로 호출한다.

실행: `python -m ai.evaluation.demo_snapshot`
산출물: docs/part_a/examples/detect_demo_request.json · detect_demo_response.json
seed=42 고정 — request는 결정론(바이트 동일). response의 execution_id만 uuid4라 매 실행 다르다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from ai.api.app import create_app
from ai.contracts.detection import DetectRequest, SignalType, StudentStatus
from ai.contracts.taxonomy import AreaTag, TypeTag
from ai.evaluation.fake_snapshot import (
    StudentPlan,
    alert_open,
    build_detect_request,
    intermittent,
    stable,
    to_payload,
)

#: 이번 주 월요일 — "이번 주 vs 평소" 비교 기준. **백엔드 시드의 `week_offset=0`과 같은 주.**
#:
#: 🔴 **`date.today()`로 바꾸지 마라** — 재생성마다 산출물이 달라져 골든이 못 쓴다.
#: **재생성할 때 사람이 시드 기준 주와 맞춘다**(시드가 움직이면 여기도 같이 움직인다).
WEEK = "2026-08-10"

#: 시드와 같은 주차 수 — 기준선 창(8주) + 판정 창(2주)을 넉넉히 덮는다.
WEEKS = 12

#: 산출물 경로 (레포 루트 기준: src/ai/evaluation/ → 상위 3단계가 루트).
_EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "docs" / "part_a" / "examples"


def demo_students() -> list[StudentPlan]:
    """백엔드 시드와 같은 17명 — 🔴 **순서가 시드 `students.csv` 순서다**(위치로 대조한다).

    반 배치: `cl_a1` 9 · `cl_b2` 7 · 미배정 1 (= 17명).
    ⚠ 미배정 학생의 `class_ref`는 백엔드가 쓰는 `cl_unassigned` 그대로다.
    """
    w = WEEKS
    return [
        # ── cl_a1 (9명) ─────────────────────────────────────────────
        #: 🔴 **비-advisory 위험 경보를 6건 만든다** — `cap_max=5`라 하나가 잘려
        #:   `capped_out=1`이 된다. ⚠ **R1로 채우면 안 된다**: R1 임계는 테넌트 풀
        #:   **분위**라(`r1_threshold_source=quantile`) 하락 학생을 늘리면 임계가 같이
        #:   올라가 **자기들끼리 상쇄된다**(실측: 3명 넣으니 임계가 15 → 25로 뛰었다).
        #:   ⇒ 분위와 무관한 R2·R3·R6로 정원을 채운다.
        StudentPlan(
            "st_01", "cl_a1", weeks=w, submit_ok=intermittent(w, w - 3),
        ),  # R2 연속 미제출
        StudentPlan(
            "st_02", "cl_a1", weeks=w, solves_per_week=20,
            accuracy=stable(w - 2, 0.85) + (0.55, 0.52),
        ),  # R1 정답률 하락
        StudentPlan(
            "st_03", "cl_a1", weeks=w, accuracy=0.82,
            duration_sec=(180,) * (w - 2) + (290, 330),
        ),  # 🔴 R4 **단독** → advisory=True (상한 밖 · 알림에서 빠진다)
        StudentPlan(
            "st_04", "cl_a1", weeks=w, submit_ok=intermittent(w, w - 4),
        ),  # R2 연속 미제출 4주
        StudentPlan("st_05", "cl_a1", weeks=w, enrolled_weeks=1),  # 🔴 신규생 → excluded_under_2w
        StudentPlan(
            "st_06", "cl_a1", weeks=w, consent="denied",
        ),  # 🔴 미동의 — 응답 어디에도 안 나온다
        StudentPlan(
            "st_07", "cl_a1", weeks=w, solves_per_week=(12,) * (w - 1) + (2,),
        ),  # R3 학습 공백
        StudentPlan(
            "st_08", "cl_a1", weeks=w, solves_per_week=24,
            bias=(AreaTag.LANGUAGE, TypeTag.CONCEPT), bias_accuracy=0.2,
        ),  # R6 유형 편중
        StudentPlan(
            "st_09", "cl_a1", weeks=w, duration_sec=0,
            solves_per_week=(12,) * (w - 1) + (2,),
        ),  # 🔴 duration 없음 → R4 skip(duration_missing) · R3 로 발화
        # ── cl_b2 (7명) ─────────────────────────────────────────────
        StudentPlan("st_10", "cl_b2", weeks=w, accuracy=0.78),  # 정상
        StudentPlan(
            "st_11", "cl_b2", weeks=w, solves_per_week=24,
            bias=(AreaTag.LITERATURE, TypeTag.INFER), bias_accuracy=0.2,
        ),  # R6 유형 편중
        StudentPlan(
            "st_12", "cl_b2", weeks=w, status=StudentStatus.RETURNED,
        ),  # R5 복귀 첫 주
        StudentPlan(
            "st_13", "cl_b2", weeks=w, solves_per_week=(10,) * (w - 1) + (3,),
        ),  # R3 학습 공백
        StudentPlan(
            "st_14", "cl_b2", weeks=w, solves_per_week=20,
            accuracy=stable(w - 2, 0.80) + (0.50, 0.45),
        ),  # 이력 있음 → lifecycle=ongoing
        StudentPlan(
            "st_15", "cl_b2", weeks=w, status=StudentStatus.PAUSED,
        ),  # 🔴 휴원 — 판정에서 제외된다
        StudentPlan("st_16", "cl_b2", weeks=w, accuracy=0.81),  # 정상
        # ── 반 미배정 (1명) ──────────────────────────────────────────
        StudentPlan(
            "st_17", "cl_unassigned", weeks=w, solves_per_week=20,
            #: ⚠ 하락폭을 크게 잡는다 — R1 임계가 **분위**라 다른 학생 구성에 따라 오른다.
            accuracy=stable(w - 2, 0.90) + (0.50, 0.46),
        ),  # 🔴 미배정 반 — R1 발화까지 시켜 반 없는 경로를 태운다
    ]


def build_demo_request() -> DetectRequest:
    """데모 요청 조립 — seed=42 고정(결과 재현)."""
    return build_detect_request(
        week_start=WEEK,
        seed=42,
        students=demo_students(),
        #: st_14 가 이력 보유 → lifecycle=ongoing (상한 밖 합류 경로를 태운다)
        alert_context=[alert_open("st_14", SignalType.ACC_DROP)],
    )


def run_demo() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """HTTP 경로로 실행하고 (요청 payload, 응답 body, 멱등 재호출 body)를 반환."""
    payload = to_payload(build_demo_request())
    client = TestClient(create_app())
    headers = {
        "X-Tenant-Id": "tn_demo_teacher",
        "X-Request-Id": "req-demo-0001",
        "Idempotency-Key": f"tn_demo_teacher:{WEEK}",
    }
    first = client.post("/v1/detect", json=payload, headers=headers)
    second = client.post("/v1/detect", json=payload, headers=headers)  # 멱등 재호출
    return payload, first.json(), second.json()


def _dump(path: Path, obj: dict[str, Any]) -> None:
    # 끝 개행을 붙이지 않는다 — 커밋된 예시 JSON(개행 없음)과 request 바이트 동일 유지.
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    payload, body, replay = run_demo()

    _EXAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    _dump(_EXAMPLES_DIR / "detect_demo_request.json", payload)
    _dump(_EXAMPLES_DIR / "detect_demo_response.json", body)

    data = body["data"]
    stats = data["stats"]
    print(f"이벤트 {len(payload['learning_events'])}건 전송 · 멱등 재호출 동일 = {replay == body}")
    print(
        f"신호 {stats['signals_raised']}건 · 판정 {stats['students_evaluated']}명"
        f" · 관찰중 제외 {stats['excluded_under_2w']}명 · 상한컷 {stats['capped_out']}건"
    )
    for signal in data["signals"]:
        print(
            f"  [{signal['class_ref']} #{signal['rank']}] {signal['student_ref']}"
            f" {signal['display_label']}({signal['signal_type']}/{signal['rule_id']})"
            f" score={signal['score']:.2f} lifecycle={signal['lifecycle']}"
        )
    print(f"rules_skipped: {stats['rules_skipped']}")
    print(f"meta.versions: {body['meta']['versions']}")


if __name__ == "__main__":
    main()
