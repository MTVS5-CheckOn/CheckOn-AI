"""canonical **입력 벡터** — 백엔드(Java) 대조용 요청 바디 (04 부록 A · 99 #43).

🔴 **여기서 내는 것은 「입력」뿐이다.** 해시도 canonical 문자열도 산출하지 않는다.
백엔드와 *"규칙 문서를 심판으로 두고 각자 계산해 동시 공개"* 로 합의했기 때문이다 —
산출물에 우리 해시를 적으면 그 값이 사실상 정답이 되고, 백엔드가 우려한
*"한쪽이 임의로 만든 값을 canonical 정답으로 확정"* 이 그대로 재현된다.

⚠ **기존 정확 벡터 3종은 이 모듈이 아니다** —
`tests/ai/contract/test_detection_evidence_contract.py`의 `_HASH_LEGACY`·`_HASH_AGGREGATE`
·`_HASH_TRANSITION` **상수**이고, 같은 파일의 `test_the_canonical_json_text_is_pinned`가
직렬화 전문까지 고정한다. 🔴 **이 모듈은 그것을 대체하지 않고 옆에 선다.**

━━ 벡터를 왜 여러 개로 쪼개나 ━━

    실요청형 한 건만 쓰면  →  "다르다"까지만 알고 원인을 다시 찾아야 한다
    규칙별로 쪼개면        →  🔴 어느 규칙이 갈렸는지 그 파일이 말해 준다

**평가 전용** (평가 격리 — 02_ownership.md §5, A 소유). 프로덕션 capability는 이 모듈을
import 하지 않는다.

실행::

    python -m ai.evaluation.canonical_vectors                      # 목록만 출력
    WRITE_CANONICAL_VECTORS=1 python -m ai.evaluation.canonical_vectors   # 파일 재생성

🔴 **정본은 이 파일의 파이썬 상수다.** 커밋된 JSON 은 그 산출물이고,
`tests/ai/contract/test_canonical_vectors.py`가 둘이 갈리면 red 를 낸다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Final

_VECTOR_DIR: Final = (
    Path(__file__).resolve().parents[3] / "docs" / "part_a" / "examples" / "canonical_vectors"
)

_WEEK: Final = "2026-08-10"


def _meta(**over: Any) -> dict[str, Any]:  # noqa: ANN401 — 벡터 오버라이드
    """snapshot_meta 기본형.

    ⚠ `snapshot_hash`는 **해시 대상이 아니다**(자기 참조 · 규칙 문서 §1-1). 자리 채우기
    값을 그대로 둔다 — 실값을 넣으면 다음 사람이 *"이 값도 맞춰야 하나"* 로 읽는다.
    """
    body: dict[str, Any] = {
        "week_start": _WEEK,
        "snapshot_hash": "sha256:x",
        "term_context": "normal",
        "classes": [{"class_ref": "cl_a1"}],
    }
    body.update(over)
    return body


def _student(ref: str = "st_1", **over: Any) -> dict[str, Any]:  # noqa: ANN401
    body: dict[str, Any] = {
        "student_ref": ref,
        "class_ref": "cl_a1",
        "enrolled_weeks": 10,
        "status": "enrolled",
        "consent": "granted",
    }
    body.update(over)
    return body


# ─────────────────────────── v00 · 실요청형 ───────────────────────────

#: 🔴 `passage_ref`를 **일부러 싣는다.** `canonical.py:81`이 `exclude={"passage_ref"}`로
#: 빼므로, 이 벡터는 *"실려 있어도 해시에 안 들어간다"* 를 백엔드가 확인하는 자리다.
#: ⚠ 넣은 판/뺀 판 **두 벌을 만들지 않는다** — 현행 계약이 이미 제외로 확정돼 있다.
_V00: Final[dict[str, Any]] = {
    "snapshot_meta": _meta(),
    "students": [_student("st_1"), _student("st_2")],
    "learning_events": [
        {
            "record_id": "le_1",
            "student_ref": "st_1",
            "type": "solve",
            "occurred_at": "2026-08-11T10:30:00+09:00",
            "correct": True,
            "duration_sec": 95,
            "passage_word_count": 820,
            "passage_ref": "pg_7",
            "assignment_title_text": "8월 2주차 독서 과제",
            "source": "trackB",
        },
        {
            "record_id": "le_2",
            "student_ref": "st_2",
            "type": "submit",
            "occurred_at": "2026-08-12T21:05:00+09:00",
            "source": "studentHome",
        },
    ],
    "alert_context": [
        {
            "student_ref": "st_1",
            "signal_type": "acc_drop",
            "status": "resolved",
            "resolved_at": "2026-08-05T09:00:00+09:00",
            "followed_up": False,
        }
    ],
    "detection_evidence": [
        {
            "kind": "assignment_window",
            "record_id": "aws_1",
            "student_ref": "st_1",
            "source_table": "assignment_week_summary",
            "week_start": _WEEK,
            "expected_count": 3,
            "submitted_count": 1,
        }
    ],
}


# ────────────────── v01 · null 이 남는가 (규칙 문서 §2-3) ──────────────────

#: 🔴 **규칙 문서가 「가장 먼저 갈릴 자리」로 지목한 곳이다.**
#: 우리는 `model_dump(mode="json")`을 쓰고 `exclude_none`을 쓰지 않으므로 안 보낸
#: optional 필드가 **`null` 키로 남는다.** Jackson 이 `@JsonInclude(NON_NULL)`이면
#: 그 키들이 통째로 빠져 바이트가 달라진다.
#:
#: ⚠ `detection_evidence`를 **일부러 하나 싣는다** — 비우면 v05(빈 컨테이너 특례)와
#:   두 규칙이 한 벡터에서 겹쳐서, 갈렸을 때 어느 쪽인지 안 갈린다.
_V01: Final[dict[str, Any]] = {
    "snapshot_meta": _meta(),
    "students": [_student("st_1")],
    "learning_events": [
        {
            # correct · duration_sec · passage_word_count · passage_ref ·
            # area_tag · subject_track · type_tag · item_format ·
            # assignment_title_text 를 **안 보낸다** → canonical 에 null 로 남는다
            "record_id": "le_1",
            "student_ref": "st_1",
            "type": "attend",
            "occurred_at": "2026-08-11T10:00:00+09:00",
            "source": "trackA",
        }
    ],
    "alert_context": [
        {
            # status=open 이면 resolved_at 이 **없어야** 한다(모델 검증) → null 로 남는다
            "student_ref": "st_1",
            "signal_type": "volume_gap",
            "status": "open",
            "followed_up": False,
        }
    ],
    "detection_evidence": [
        {
            "kind": "weekly_activity",
            "record_id": "swa_1",
            "student_ref": "st_1",
            "source_table": "student_week_activity",
            "week_start": _WEEK,
            "activity_count": 0,
        }
    ],
}


# ───────────── v02 · UTC 표기가 한 payload 안에서 갈린다 (§2-1) ─────────────

#: 🔴 **이 벡터의 요점은 「저희 안에서도 두 형태가 나온다」이다.**
#:
#:     detection_evidence[].at      경로 A  canonical.py:37 `.isoformat()`  → "…+00:00"
#:     alert_context[].resolved_at  경로 B  pydantic model_dump             → "…Z"
#:
#: ⚠ **둘 다 UTC 로 준다.** `+09:00` 처럼 명시 오프셋이면 두 경로가 같은 문자열을 내서
#:   아무것도 안 드러난다 — 갈리는 것은 **UTC 일 때뿐**이다.
_V02: Final[dict[str, Any]] = {
    "snapshot_meta": _meta(),
    "students": [_student("st_1", status="returned")],
    "learning_events": [],
    "alert_context": [
        {
            "student_ref": "st_1",
            "signal_type": "acc_drop",
            "status": "resolved",
            "resolved_at": "2026-08-05T00:00:00+00:00",  # 경로 B → "Z"
            "followed_up": True,
        }
    ],
    "detection_evidence": [
        {
            "kind": "enrollment_transition",
            "record_id": "ssh_1",
            "student_ref": "st_1",
            "source_table": "student_status_history",
            "occurred_at": "2026-08-10T00:00:00+00:00",  # 경로 A → "+00:00"
            "from_status": "paused",
            "to_status": "returned",
        }
    ],
}


# ───────────── v06 · 배열 입력 순서가 해시를 안 바꾼다 (§1-2) ─────────────

#: 🔴 **네 배열이 전부 역순이다.** `canonical_snapshot_payload`가 각 배열을 자기 키로
#: 다시 정렬하므로 정순 입력과 같은 해시가 나와야 한다. Java 가 **같은 키·같은 방향**으로
#: 정렬하지 않으면 여기서 갈린다.
#:
#: ⚠ 이 파일을 «보기 좋게» 정렬하지 마라 — **뒤집혀 있는 것이 요점이다.**
_V06: Final[dict[str, Any]] = {
    "snapshot_meta": _meta(),
    "students": [_student("st_2"), _student("st_1")],  # student_ref 역순
    "learning_events": [
        {
            "record_id": "le_2",
            "student_ref": "st_2",
            "type": "solve",
            "occurred_at": "2026-08-12T10:00:00+09:00",
            "correct": False,
            "source": "trackB",
        },
        {
            "record_id": "le_1",
            "student_ref": "st_1",
            "type": "solve",
            "occurred_at": "2026-08-11T10:00:00+09:00",
            "correct": True,
            "source": "trackB",
        },
    ],  # record_id 역순
    "alert_context": [
        {
            "student_ref": "st_2",
            "signal_type": "volume_gap",
            "status": "open",
            "followed_up": False,
        },
        {
            "student_ref": "st_1",
            "signal_type": "acc_drop",
            "status": "open",
            "followed_up": False,
        },
    ],  # (student_ref, signal_type) 역순
    "detection_evidence": [
        {
            "kind": "weekly_activity",
            "record_id": "swa_1",
            "student_ref": "st_2",
            "source_table": "student_week_activity",
            "week_start": _WEEK,
            "activity_count": 4,
        },
        {
            "kind": "assignment_window",
            "record_id": "aws_1",
            "student_ref": "st_1",
            "source_table": "assignment_week_summary",
            "week_start": _WEEK,
            "expected_count": 2,
            "submitted_count": 0,
        },
    ],  # (kind, student_ref, …) 역순
}


#: 벡터 목록 — `(파일명 stem, 찌르는 규칙 한 줄, 바디)`.
#: 🔴 **미착수분은 여기 없고 README 표에만 「다음 회차」로 적는다** —
#:   빠뜨린 것과 아직 안 한 것이 갈려야 한다.
VECTORS: Final[tuple[tuple[str, str, dict[str, Any]], ...]] = (
    ("v00_realistic", "실요청 모양 · passage_ref 가 실려도 해시에 안 들어간다", _V00),
    ("v01_null_vs_absent", "안 보낸 optional 필드가 null 키로 남는다 (§2-3)", _V01),
    ("v02_time_utc", "한 payload 안에서 UTC 표기가 +00:00 과 Z 로 갈린다 (§2-1)", _V02),
    ("v06_array_order", "배열 입력 순서가 달라도 해시가 같다 (§1-2)", _V06),
)

#: README 표에 함께 적을 **아직 안 만든** 벡터. 🔴 목록에서 지우지 말고 여기 남긴다.
PENDING: Final[tuple[tuple[str, str], ...]] = (
    ("v03_time_fraction", "소수 초 — 후행 0 보존 (§2-1 ㉡)"),
    ("v04_nonascii", "한글이 \\uXXXX 가 아니라 원문 UTF-8 (§2 ②)"),
    ("v05_empty_containers", "detection_evidence 만 키 자체가 없다 (§2-4)"),
    ("v07_escape", "`/` 는 이스케이프 안 함 · 제어문자 소문자 hex (§2-5)"),
    ("v08_integers", "정수에 후행 .0 이 없다 (§2-2)"),
)


def _dump(path: Path, obj: dict[str, Any]) -> None:
    # 끝 개행을 붙이지 않는다 — 같은 폴더의 다른 예시 JSON과 같은 규약.
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def render_readme() -> str:
    """벡터 폴더의 README — 🔴 상위 README 와 성격이 다르다는 것을 여기서 밝힌다."""
    rows = "\n".join(
        f"| `{name}.json` | {why} | ✅ |" for name, why, _ in VECTORS
    )
    pending = "\n".join(f"| `{name}.json` | {why} | 다음 회차 |" for name, why in PENDING)
    return f"""# canonical 입력 벡터 — 백엔드(Java) 대조용

🔴 **이 폴더는 상위 `examples/README.md` 와 성격이 다르다.** 상위는 *"손으로 지은 예시가
아니라 실제로 받은 응답"* 인데, 여기 JSON 은 **대조용으로 지어낸 입력**이다. 상위 문장을
이 파일들에 적용해 읽지 마라.

## 파일

| 파일 | 찌르는 것 | 상태 |
| --- | --- | --- |
{rows}
{pending}

## 🔴 해시는 여기 없다

백엔드와 *"규칙 문서를 심판으로 두고 각자 계산해 **동시 공개**"* 로 합의했다. 산출물에
우리 해시를 적으면 그 값이 정답이 되고, 합의가 무의미해진다.

재생성::

    WRITE_CANONICAL_VECTORS=1 python -m ai.evaluation.canonical_vectors

⚠ **정본은 `src/ai/evaluation/canonical_vectors.py` 의 파이썬 상수다.** 이 JSON 을 직접
고치면 `tests/ai/contract/test_canonical_vectors.py` 가 red 를 낸다.

## ⚠ 기존 정확 벡터 3종은 여기 없다

`tests/ai/contract/test_detection_evidence_contract.py` 의 `_HASH_LEGACY` ·
`_HASH_AGGREGATE` · `_HASH_TRANSITION` **상수**이고, 같은 파일이 canonical 문자열 전문도
고정한다. 이 폴더는 그것을 대체하지 않고 옆에 선다.
"""


def main() -> None:
    write = os.environ.get("WRITE_CANONICAL_VECTORS") == "1"
    if write:
        _VECTOR_DIR.mkdir(parents=True, exist_ok=True)
        for name, _why, body in VECTORS:
            _dump(_VECTOR_DIR / f"{name}.json", body)
        (_VECTOR_DIR / "README.md").write_text(render_readme(), encoding="utf-8")
    for name, why, _ in VECTORS:
        print(f"{'wrote' if write else 'have '} {name}.json  — {why}")
    for name, why in PENDING:
        print(f"pending {name}.json  — {why}")


if __name__ == "__main__":
    main()
