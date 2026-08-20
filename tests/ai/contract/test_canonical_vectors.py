"""canonical 입력 벡터 — 드리프트 가드 + 성질 단언 (04 부록 A · 99 #43).

🔴 **해시 값을 단언하지 않는다.** 값을 박으면 백엔드와 «각자 계산 → 동시 공개» 합의를
어기는 것이고, 값이 바뀌었을 때 *"규칙이 바뀐 건가 버그인가"* 가 안 갈린다. 여기서 재는
것은 **성질**이다.

⚠ `_HASH_LEGACY` 류를 이 파일에 복제하지 마라 — 정본이 둘이 된다.
그 셋은 `test_detection_evidence_contract.py` 가 지킨다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ai.contracts.detection import DetectRequest
from ai.detection.canonical import canonical_snapshot_hash, canonical_snapshot_payload
from ai.evaluation.canonical_vectors import (
    _VECTOR_DIR,
    PENDING,
    VECTORS,
    render_readme,
)

_NAMES = [name for name, _why, _body in VECTORS]


def _body(name: str) -> dict[str, Any]:
    for candidate, _why, body in VECTORS:
        if candidate == name:
            return body
    raise AssertionError(f"벡터가 없다: {name}")


def _request(name: str) -> DetectRequest:
    return DetectRequest.model_validate(_body(name))


# ─────────────────────────── ① 드리프트 가드 ───────────────────────────


@pytest.mark.parametrize("name", _NAMES)
def test_the_committed_file_matches_the_generator(name: str) -> None:
    """🔴 커밋된 JSON 이 생성기 출력과 같다 — 손으로 고치면 여기서 잡힌다.

    재생성: `WRITE_CANONICAL_VECTORS=1 python -m ai.evaluation.canonical_vectors`
    """
    path: Path = _VECTOR_DIR / f"{name}.json"
    assert path.is_file(), f"벡터 파일이 없다: {path} — 생성기를 한 번 돌려라"
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored == _body(name), (
        f"{name}.json 이 생성기와 갈렸다.\n"
        "🔴 정본은 canonical_vectors.py 의 파이썬 상수다 — JSON 을 직접 고치지 마라."
    )


def test_the_readme_matches_the_generator() -> None:
    """README 도 생성기가 만든다 — 표가 낡으면 백엔드가 없는 벡터를 찾는다."""
    path = _VECTOR_DIR / "README.md"
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == render_readme()


def test_pending_vectors_are_not_silently_dropped() -> None:
    """🔴 미착수분이 목록에서 사라지면 «안 한 것»이 «없는 것»이 된다(로그 149와 같은 형태)."""
    assert PENDING, "미착수 벡터를 지우지 마라 — 다 만들었으면 이 검사를 같이 지운다"
    assert not (set(_NAMES) & {name for name, _ in PENDING})


# ─────────────────────── ② 모델이 받는 입력인가 ───────────────────────


@pytest.mark.parametrize("name", _NAMES)
def test_every_vector_validates(name: str) -> None:
    """🔴 모델이 못 받는 입력을 «벡터»라 부르지 않는다 — 백엔드가 400 을 재게 된다."""
    request = _request(name)
    assert canonical_snapshot_hash(request).startswith("sha256:")


# ───────────────────────── ③④⑤ 성질 단언 ─────────────────────────


def test_v02_shows_two_utc_shapes_in_one_payload() -> None:
    """🔴 UTC 시각이 한 payload 안에서 `+00:00` 과 `Z` 로 갈린다.

    ⚠ **이 단언은 현행 동작을 드러내는 것이지 계약 확정이 아니다.** 백엔드와 표기를 정하면
    **이 검사가 먼저 red 가 되어야 한다** — 그때 벡터와 규칙 문서를 같이 고친다.

        detection_evidence[].at      canonical.py `_evidence_at` → `.isoformat()` → "+00:00"
        alert_context[].resolved_at  pydantic model_dump(mode="json")             → "Z"
    """
    payload = canonical_snapshot_payload(_request("v02_time_utc"))
    assert payload["detection_evidence"][0]["at"].endswith("+00:00")
    assert payload["alert_context"][0]["resolved_at"].endswith("Z")


def test_v06_input_order_does_not_change_the_hash() -> None:
    """역순 입력과 정순 입력의 해시가 같다 — Java 가 같은 키로 정렬해야 맞는다."""
    reversed_body = _body("v06_array_order")
    sorted_body = {
        **reversed_body,
        "students": list(reversed(reversed_body["students"])),
        "learning_events": list(reversed(reversed_body["learning_events"])),
        "alert_context": list(reversed(reversed_body["alert_context"])),
        "detection_evidence": list(reversed(reversed_body["detection_evidence"])),
    }
    assert canonical_snapshot_hash(
        DetectRequest.model_validate(reversed_body)
    ) == canonical_snapshot_hash(DetectRequest.model_validate(sorted_body))


def test_v01_keeps_null_keys_in_the_canonical_payload() -> None:
    """🔴 안 보낸 optional 필드가 **`null` 키로 남는다** — 규칙 문서 §2-3.

    Jackson 이 `@JsonInclude(NON_NULL)` 이면 이 키들이 통째로 빠져 바이트가 갈린다.
    `learning_events` 가 있는 **거의 모든 실요청**이 이 자리를 지난다.
    """
    payload = canonical_snapshot_payload(_request("v01_null_vs_absent"))
    event = payload["learning_events"][0]
    for field in ("correct", "duration_sec", "passage_word_count"):
        assert field in event, f"{field} 키가 사라졌다"
        assert event[field] is None
    assert payload["alert_context"][0]["resolved_at"] is None
    #: ⚠ `passage_ref` 는 **애초에 canonical 대상이 아니다**(canonical.py `exclude`) —
    #:   null 로 남는 것이 아니라 키 자체가 없다. 위 셋과 성질이 다르다.
    assert "passage_ref" not in event


def test_v00_carries_passage_ref_but_the_hash_ignores_it() -> None:
    """실요청형에는 `passage_ref` 가 실려 있고, canonical 은 그것을 뺀다.

    ⇒ 백엔드가 그 필드를 보내든 말든 해시가 같다. 벡터를 두 벌 만들 필요가 없는 근거다.
    """
    body = _body("v00_realistic")
    assert body["learning_events"][0]["passage_ref"] == "pg_7"

    without = json.loads(json.dumps(body))
    del without["learning_events"][0]["passage_ref"]
    assert canonical_snapshot_hash(
        DetectRequest.model_validate(body)
    ) == canonical_snapshot_hash(DetectRequest.model_validate(without))
