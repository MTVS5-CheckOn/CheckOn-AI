"""데모: 학생 10명 × 10주 가상 스냅숏 → 실제 HTTP 경로(POST /v1/detect) 실행.

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

#: 이번 주 월요일 — "이번 주 vs 평소" 비교 기준.
WEEK = "2026-07-20"

#: 산출물 경로 (레포 루트 기준: src/ai/evaluation/ → 상위 3단계가 루트).
_EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "docs" / "part_a" / "examples"


def demo_students() -> list[StudentPlan]:
    """6규칙 전부 발화 + 정상·신규생·복귀·이력을 담은 10명 (2반)."""
    return [
        # ── cl_a1 반 ──
        StudentPlan("st_01", "cl_a1", accuracy=0.85),  # 정상
        StudentPlan("st_02", "cl_a1", accuracy=stable(8, 0.85) + (0.62, 0.60)),  # R1 하락
        StudentPlan(
            "st_03", "cl_a1", accuracy=0.82, duration_sec=(180,) * 8 + (290, 330)
        ),  # R4 숨은 위기
        StudentPlan("st_04", "cl_a1", submit_ok=intermittent(10, 7)),  # R2 미제출 3주
        StudentPlan("st_05", "cl_a1", enrolled_weeks=1),  # 신규생(관찰 중)
        # ── cl_b2 반 ──
        StudentPlan("st_06", "cl_b2", accuracy=0.78),  # 정상
        StudentPlan(
            "st_07",
            "cl_b2",
            solves_per_week=24,
            bias=(AreaTag.LITERATURE, TypeTag.INFER),
            bias_accuracy=0.2,
        ),  # R6 유형 편중
        StudentPlan("st_08", "cl_b2", status=StudentStatus.RETURNED),  # R5 복귀 첫 주
        StudentPlan("st_09", "cl_b2", solves_per_week=(10,) * 9 + (3,)),  # R3 학습 공백
        StudentPlan("st_10", "cl_b2", accuracy=stable(8, 0.80) + (0.50, 0.45)),  # 이력 → ongoing
    ]


def build_demo_request() -> DetectRequest:
    """데모 요청 조립 — seed=42 고정(결과 재현)."""
    return build_detect_request(
        week_start=WEEK,
        seed=42,
        students=demo_students(),
        alert_context=[alert_open("st_10", SignalType.ACC_DROP)],
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
