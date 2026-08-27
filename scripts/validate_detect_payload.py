"""저장된 `/v1/detect` 요청 본문에서 **모든** 계약 위반을 한 번에 뽑는다.

    uv run --frozen python scripts/validate_detect_payload.py <스냅숏.json>

🔴 **왜 필요한가** — 서버 응답은 pydantic 이 **먼저 걸린 하나**에서 멈추는 자리가 있고
(요청 단위 교차 검증 여섯은 전부 그렇다), 게다가 그 여섯은 응답 `detail[].field` 가
**빈 문자열**이라 어느 행인지 안 보인다. 이 스크립트는 **끝까지 훑어 전부** 적는다.

⚠ **서버를 안 띄운다** — 계약 모듈만 import 한다. 실 LLM·DB 를 안 만진다.
⚠ `scripts/` 는 `02_ownership.md` 에 소유가 **미등재**다(99 #269).
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from pydantic import ValidationError

from ai.contracts.detection import DetectRequest


def _violations(body: dict) -> list[str]:
    """계약을 다시 구현하지 않는다 — **행 단위로 나눠 계약에 물어본다.**"""
    out: list[str] = []
    meta = body.get("snapshot_meta") or {}
    try:
        week_monday = date.fromisoformat(str(meta.get("week_start")))
    except ValueError:
        return [f"snapshot_meta.week_start 가 ISO date 가 아니다: {meta.get('week_start')!r}"]

    students = body.get("students") or []
    status_of = {s.get("student_ref"): s.get("status") for s in students}
    known = set(status_of)
    evidence = body.get("detection_evidence") or []

    seen_record: dict[tuple, dict] = {}
    seen_key: dict[tuple, int] = Counter()

    for i, row in enumerate(evidence):
        kind = row.get("kind")
        ref = row.get("student_ref")
        where = f"detection_evidence[{i}] {kind} student_ref={ref!r}"

        if ref not in known:
            out.append(f"{where}: students[] 에 없는 student_ref")

        rk = (row.get("source_table"), row.get("record_id"))
        if rk in seen_record and seen_record[rk] != row:
            out.append(f"{where}: 같은 (source_table, record_id)={rk} 에 다른 내용")
        seen_record[rk] = row

        if kind in {"assignment_window", "weekly_activity"}:
            try:
                ws = date.fromisoformat(str(row.get("week_start")))
            except ValueError:
                out.append(
                    f"{where}: week_start 가 ISO date 가 아니다: {row.get('week_start')!r}"
                )
                continue
            if ws > week_monday:
                out.append(f"{where}: 분석 주({week_monday})보다 미래인 집계 week_start={ws}")
            key = (kind, ref, ws)
            seen_key[key] += 1
            if seen_key[key] == 2:
                out.append(f"{where}: (kind, student_ref, week_start)={key} 집계가 중복")

        if kind == "weekly_activity":
            es = row.get("enrolled_seconds")
            if es is None:
                out.append(f"{where}: enrolled_seconds 가 없다(필수)")
            elif not isinstance(es, int) or isinstance(es, bool):
                out.append(
                    f"{where}: enrolled_seconds 가 정수가 아니다: {es!r} (strict)"
                )
            elif not 1 <= es <= 604800:
                out.append(f"{where}: enrolled_seconds 범위 밖: {es}")

        if kind == "assignment_window":
            exp, sub = row.get("expected_count"), row.get("submitted_count")
            if isinstance(exp, int) and isinstance(sub, int) and sub > exp:
                out.append(f"{where}: submitted_count({sub}) > expected_count({exp})")

        if kind == "enrollment_transition":
            raw = str(row.get("occurred_at") or "")
            try:
                occurred = date.fromisoformat(raw[:10])
            except ValueError:
                out.append(f"{where}: occurred_at 을 못 읽는다: {raw!r}")
                continue
            if not (raw.endswith("Z") or "+" in raw[10:] or "-" in raw[11:]):
                out.append(f"{where}: occurred_at 이 timezone-aware 가 아니다: {raw!r}")
            monday = occurred - timedelta(days=occurred.weekday())
            if monday > week_monday:
                out.append(f"{where}: 분석 주({week_monday})보다 미래인 전환: {monday}")
            to_returned = row.get("to_status") == "returned"
            if to_returned and status_of.get(ref) not in {"returned", "paused"}:
                out.append(
                    f"{where}: returned 전환인데 students[].status={status_of.get(ref)!r} "
                    "(returned 또는 paused 여야 한다)"
                )

    allowed_source = {"trackA", "trackB", "studentHome", "MANUAL"}
    bad_source = Counter(
        str(e.get("source"))
        for e in (body.get("learning_events") or [])
        if e.get("source") not in allowed_source
    )
    for value, n in bad_source.items():
        out.append(
            f"learning_events[].source 가 열거 밖: {value!r} × {n}건 "
            f"(허용: {sorted(allowed_source)})"
        )

    for i, item in enumerate(body.get("alert_context") or []):
        st, at = item.get("status"), item.get("resolved_at")
        if st == "resolved" and at is None:
            out.append(f"alert_context[{i}]: status=resolved 인데 resolved_at 이 없다")
        if st == "open" and at is not None:
            out.append(f"alert_context[{i}]: status=open 인데 resolved_at 이 있다")

    return out


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    body = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))

    print("── 계약 판정 (서버와 같은 코드) ──")
    try:
        DetectRequest.model_validate(body)
        print("  통과 — 이 본문으로는 400 이 안 난다")
        verdict = 0
    except ValidationError as exc:
        print(f"  거부 — pydantic 오류 {exc.error_count()}건 (서버가 이 중 일부만 detail 로 낸다)")
        for err in exc.errors()[:10]:
            loc = ".".join(str(p) for p in err["loc"]) or "(루트 — 요청 단위 교차 검증)"
            print(f"    {loc}: {err['msg']}")
        verdict = 1

    print("\n── 전수 훑기 (멈추지 않고 끝까지) ──")
    found = _violations(body)
    if not found:
        print("  위반 0건")
    for line in found:
        print(f"  🔴 {line}")

    ev = body.get("detection_evidence") or []
    kinds = Counter(r.get("kind") for r in ev)
    print(
        f"\n── 입력 요약 ── students {len(body.get('students') or [])}명 · "
        f"learning_events {len(body.get('learning_events') or [])}건 · "
        f"detection_evidence {len(ev)}건 {dict(kinds)} · "
        f"week_start={(body.get('snapshot_meta') or {}).get('week_start')}"
    )
    return verdict or (1 if found else 0)


if __name__ == "__main__":
    raise SystemExit(main())
