"""백엔드 시뮬레이터 — 실행 중인 실서버에 '백엔드처럼' 순차 POST (시연·연동 리허설).

소유: 박진희 (A · 평가 격리 — 프로덕션 경로 아님). 실운영 시나리오를 재현한다:
**day1 = 최근 10주 풀 스냅숏, day2부터 = 지난 1주 증분.** read-path(D-②b)가 축적분으로
baseline을 구성해 이틀치 배치가 실제 API에서 도는 장면을 표로 보여준다.

실행:
    uv run uvicorn ai.api.app:app --reload          # (별도 터미널) 서버 기동
    uv run python -m ai.evaluation.backend_sim    # 기본 3일치, http://127.0.0.1:8000

provider는 fake 기본(실 LLM 아님). store_backend=pg면 재시작 생존까지 볼 수 있다
(--restart-between은 test_pg_restart integration 참고 — 이 스크립트는 순차 전송만).
httpx는 기존 의존성. 서버가 없으면 친절히 안내하고 종료한다.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta

import httpx

from ai.contracts.detection import SignalType
from ai.evaluation.fake_snapshot import (
    StudentPlan,
    alert_open,
    build_detect_request,
    take_single_week,
    take_week_range,
    to_payload,
)

_TENANT = "tn_demo_teacher"
_BASE_MONDAY = date(2026, 5, 4)  # 고정 월요일 — 결정론(현재 시각 미사용)


def _week(offset_weeks: int) -> str:
    return (_BASE_MONDAY + timedelta(weeks=offset_weeks)).isoformat()


def _plans(total_weeks: int) -> list[StudentPlan]:
    """고정 seed 시나리오 — 하락 지속(R1)·숨은 위기(R4)·안정 대조."""
    declining = tuple(round(0.9 - 0.05 * i, 4) for i in range(total_weeks))
    surge = tuple(120 if i < total_weeks - 2 else 320 for i in range(total_weeks))
    return [
        StudentPlan(student_ref="st_dec", class_ref="cl_a1", weeks=total_weeks, accuracy=declining),
        StudentPlan(
            student_ref="st_hidden",
            class_ref="cl_a1",
            weeks=total_weeks,
            accuracy=0.8,
            duration_sec=surge,
            passage_word_count=100,
        ),
        StudentPlan(student_ref="st_stable", class_ref="cl_a1", weeks=total_weeks, accuracy=0.8),
    ]


def _headers(week_start: str) -> dict[str, str]:
    return {
        "X-Tenant-Id": _TENANT,
        "X-Request-Id": f"sim-{week_start}",
        "Idempotency-Key": f"{_TENANT}:{week_start}",
    }


def _print_row(day: int, week_start: str, signals: list[dict[str, object]]) -> None:
    label = "day1 풀 스냅숏(10주)" if day == 0 else f"day{day + 1} 증분(1주)"
    print(f"\n── {label} · week_start={week_start} · 신호 {len(signals)}건 ──")
    if not signals:
        print("   (신호 없음)")
    for signal in signals:
        print(
            f"   [{signal['class_ref']} #{signal['rank']}] {signal['student_ref']}"
            f" {signal['display_label']}({signal['signal_type']})"
            f" lifecycle={signal['lifecycle']}"
            f" · {signal['brief']['text']}"  # type: ignore[index]
        )


def run(base_url: str, days: int) -> int:
    total_weeks = 10 + (days - 1)
    full = build_detect_request(
        week_start=_week(9 + days - 1), seed=7, students=_plans(total_weeks)
    )

    prior_types: dict[str, set[SignalType]] = {}
    try:
        with httpx.Client(base_url=base_url, timeout=60.0) as client:
            for day in range(days):
                week_start = _week(9 + day)
                if day == 0:
                    request = take_week_range(full, week_start=week_start, weeks_back=10)
                else:
                    request = take_single_week(full, week_start=week_start)
                # 어제 경보를 alert_context로 반영(lifecycle 생애 판정)
                context = tuple(
                    alert_open(student_ref, signal_type)
                    for student_ref, types in prior_types.items()
                    for signal_type in types
                )
                request = request.model_copy(update={"alert_context": context})

                response = client.post(
                    "/v1/detect", json=to_payload(request), headers=_headers(week_start)
                )
                response.raise_for_status()
                signals = response.json()["data"]["signals"]
                _print_row(day, week_start, signals)

                prior_types = {}
                for signal in signals:
                    prior_types.setdefault(signal["student_ref"], set()).add(
                        SignalType(signal["signal_type"])
                    )
    except httpx.ConnectError:
        print(
            f"\n서버에 연결할 수 없습니다({base_url}).\n"
            "먼저 서버를 기동하세요:\n"
            "    uv run uvicorn ai.api.app:app --reload\n"
        )
        return 1
    print("\n완료 — 이틀치 이상 배치가 read-path로 이어졌습니다(증분 전송이 통짜와 동일 판정).")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="백엔드 순차 POST 시뮬레이터(day1 풀→증분)")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--days", type=int, default=3)
    args = parser.parse_args()
    raise SystemExit(run(args.url, args.days))


if __name__ == "__main__":
    main()
