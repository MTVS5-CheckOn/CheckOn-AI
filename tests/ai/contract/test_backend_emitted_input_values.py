"""🔴 **상대가 실제로 보내는 값이 우리 계약 안에 드는가** (PR-τ).

계약이 **자기 자신만 보고 있었다.**

```
contracts/detection.py :: EventSource   trackA · trackB · studentHome   (닫힌 집합)
test_detection.py                       "이 집합이 셋인가"              ✅ green
백엔드가 실제로 보내는 값                MANUAL                          🔴 아무도 안 묻는다
⇒ /v1/detect 첫 실요청 = 400 INVALID_SCHEMA
```

⚠ **검사가 없어서 놓친 게 아니다.** 검사는 있었고 **축이 안쪽**이었다 — *"우리가 허용하는
값이 무엇인가"* 는 고정돼 있었지만 *"상대가 실제로 보내는 값이 그 안에 있는가"* 는 한 번도
묻지 않았다. 그래서 계약 검사 전부 green인 채로 첫 실왕복이 죽는다.

🔴 **이 파일은 「우리 enum이 셋인가」의 동어반복이 아니다.** 그건
`test_detection.py::test_event_source_values_frozen`이 이미 문다(중복 만들지 않았다).
여기는 **반대 축**이다 — 상대 발신값 집합을 전제로 두고 **파싱이 되는지**를 묻는다.
그래서 `EventSource` 멤버 집합과 **직접 비교하지 않는다**(비교하면 다시 동어반복이다).

🔴 **이 검사는 시간으로 소멸하지 않는다.** 백엔드가 `source_type → source` 화이트리스트
매핑을 배포해도 **픽스처는 저절로 안 줄어든다** — 그때는 `EventSource.MANUAL`과 아래
`BACKEND_EMITTED_SOURCE_VALUES`의 `"MANUAL"`을 **같이** 지워야 green이다.
⚠ **소멸형 검사(#33 계열)와 방향이 반대다**: 그쪽은 날짜가 지나면 스스로 꺼지지만, 이쪽은
안 고치면 **낡은 값을 계속 요구하는 쪽**으로 남는다. 그게 의도다 — 조용히 사라지면
「상대가 아직 그 값을 보내는가」를 아무도 다시 묻지 않는다.
"""

from __future__ import annotations

from typing import Any, Final

import pytest
from pydantic import ValidationError

from ai.contracts.detection import DetectRequest, EventSource

#: 🔴 **백엔드가 `learning_events[].source`에 실제로 넣는 값** — 출처를 값마다 박는다.
#: ⚠ 출처가 없으면 다음 사람이 그 값을 지운다.
BACKEND_EMITTED_SOURCE_VALUES: Final[tuple[tuple[str, str], ...]] = (
    (
        "MANUAL",
        #: 백엔드 `LearningRecordController :: LearningRecordRequest.toCommand`가
        #: `LearningRecordSource.MANUAL`을 고정으로 넣는다(2026-08-13 · v1 유일 등록 경로).
        "LearningRecordController :: LearningRecordRequest.toCommand → LearningRecordSource.MANUAL",
    ),
)

_WEEK: Final = "2026-07-20"


def _request(source: str) -> dict[str, Any]:
    """`source` 하나만 바꾼 최소 요청 — 다른 축이 red를 가리지 않게 한다."""
    return {
        "snapshot_meta": {
            "week_start": _WEEK,
            "snapshot_hash": "sha256:be-compat",
            "term_context": "normal",
            "classes": [{"class_ref": "cl_a1"}],
        },
        "students": [
            {
                "student_ref": "st_1",
                "class_ref": "cl_a1",
                "enrolled_weeks": 10,
                "status": "enrolled",
                "consent": "granted",
            }
        ],
        "learning_events": [
            {
                "record_id": "le_1",
                "student_ref": "st_1",
                "type": "solve",
                "occurred_at": f"{_WEEK}T19:00:00+09:00",
                "correct": True,
                "duration_sec": 60,
                "passage_word_count": 300,
                "source": source,
            }
        ],
        "alert_context": [],
    }


@pytest.mark.parametrize(
    ("source", "origin"),
    BACKEND_EMITTED_SOURCE_VALUES,
    ids=[value for value, _origin in BACKEND_EMITTED_SOURCE_VALUES],
)
def test_every_value_the_backend_emits_parses(source: str, origin: str) -> None:
    """🔴 **상대 발신값이 전부 파싱된다** — 하나라도 못 받으면 실왕복 첫 요청이 400이다.

    ⚠ `EventSource` 멤버 집합과 비교하지 않는다 — 그러면 *"우리가 허용하는 값"* 을 두 번
    적는 것이고, 상대가 새 값을 보내는 날 여전히 못 잡는다. **파싱이 되는가**를 묻는다.
    """
    parsed = DetectRequest.model_validate(_request(source))
    assert parsed.learning_events[0].source.value == source, (
        f"백엔드 발신값 «{source}»이 파싱되지 않았다 — 출처: {origin}"
    )


# ───────────────────────── 절단 가드 ─────────────────────────
#
# 🔴 이번 안건 자체가 「검사가 상대를 안 봐서 통과한 것」이다. 같은 형태를 새로 만들지 않는다.


def test_the_backend_value_set_is_not_empty() -> None:
    """🔴 대상이 0건이면 **「위반 없음」이 아니라 축을 잃은 것**이다."""
    assert BACKEND_EMITTED_SOURCE_VALUES, "상대 발신값 축을 잃었다"
    for value, origin in BACKEND_EMITTED_SOURCE_VALUES:
        assert value, "빈 발신값이 들어 있다"
        assert "::" in origin, f"«{value}»에 백엔드 심볼 출처가 없다 — 다음 사람이 지운다"


def test_the_event_source_enum_is_not_empty() -> None:
    """🔴 우리 enum이 0건이어도 위 검사가 「전부 통과」로 보이면 안 된다.

    ⚠ **집합 값 고정은 여기서 다시 하지 않는다** — `test_detection.py`의
    `test_event_source_values_frozen`이 이미 문다(중복 금지).
    """
    assert list(EventSource), "EventSource가 비었다"


# ═══════════════ classes 빈 배열 — 상대가 비울 수 있는 조건 ═══════════════
#
# 🔴 같은 형태가 `snapshot_meta.classes`에도 있다 — **아직 안 터졌을 뿐**이다
#    (전원 반 미배정인 강사가 없었다).
#
# 전제: 백엔드 `LearningRecordSnapshotService :: build`가 반 미배정 학생의
#       `cl_unassigned`를 classes에서 **거른다** ⇒ 전원 미배정인 강사는 **빈 배열**을 보낸다.
#
# ⚠ *"`min_length` 제약이 없나"* 를 묻지 않는다 — 스키마 자기 반영이다.
#   **그 조건에서 파싱이 되는가**를 묻는다.


def test_a_teacher_with_every_student_unassigned_parses() -> None:
    """🔴 `classes=[]`인 요청이 파싱된다.

    AI 판정은 `classes`를 **어디서도 읽지 않는다**(전수 실측 2026-08-13: 정의부 외 0건).
    여기서 400을 내면 **잃는 것만 있다.**
    """
    body = _request("trackB")
    body["snapshot_meta"]["classes"] = []
    parsed = DetectRequest.model_validate(body)
    assert parsed.snapshot_meta.classes == ()


def test_the_classes_key_itself_is_still_required() -> None:
    """🔴 **절단 가드** — 「안 보냈다」와 「빌 수 있다」는 **다른 사실**이다.

    ⚠ 백엔드는 **항상 키를 보내고 값만 빌 수 있다.** 기본값을 주거나 `Optional`로 만들면
    그 구분이 사라지고, 키가 통째로 빠진 요청도 조용히 통과한다.
    """
    body = _request("trackB")
    del body["snapshot_meta"]["classes"]
    with pytest.raises(ValidationError):
        DetectRequest.model_validate(body)
