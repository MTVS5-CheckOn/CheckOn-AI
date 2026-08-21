"""canonical 입력 벡터 — 드리프트 가드 + 성질 단언 (04 부록 A · 99 #43).

🔴 **해시 값을 단언하지 않는다.** 값을 박으면 백엔드와 «각자 계산 → 동시 공개» 합의를
어기는 것이고, 값이 바뀌었을 때 *"규칙이 바뀐 건가 버그인가"* 가 안 갈린다. 여기서 재는
것은 **성질**이다.

⚠ `_HASH_LEGACY` 류를 이 파일에 복제하지 마라 — 정본이 둘이 된다.
그 셋은 `test_detection_evidence_contract.py` 가 지킨다.
"""

from __future__ import annotations

import json
import re
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


def test_pending_is_empty_because_every_vector_exists() -> None:
    """🔴 **아홉 종을 다 만들어 `PENDING` 이 비었다**(8/21).

    ⚠ 종전 검사(`test_pending_vectors_are_not_silently_dropped`)는 `assert PENDING` 이었고
    그 docstring 이 *"다 만들었으면 이 검사를 같이 지운다"* 라고 적어 뒀다. 지우는 대신
    **뜻을 뒤집어 남긴다** — 누가 벡터를 빼고 `PENDING` 에 도로 넣으면 여기서 걸린다.
    ⚠ 서로게이트는 **입력 계약에 자유 텍스트 필드가 없어 만들 자리가 없다**(§39 판정) —
    「안 만든 것」이 아니라 「만들 수 없는 것」이라 `PENDING` 이 아니다.
    """
    assert PENDING == (), f"미착수분이 다시 생겼다: {PENDING}"


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


# ─────────────────── ⑥ 잔여 다섯이 각자 한 규칙을 든다 ───────────────────


def _serialized(name: str) -> str:
    """🔴 `canonical_snapshot_hash` 와 **같은 인자**로 만든다.

    ⚠ 다른 인자로 만들면 다른 것을 재게 된다 — 이 파일이 재려는 것은
    「해시가 보는 그 문자열」이지 「보기 좋은 JSON」이 아니다.
    """
    payload = canonical_snapshot_payload(_request(name))
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def test_v03_keeps_the_fractional_seconds() -> None:
    """소수 초 6자리가 보존된다 — 🔴 **표기(Z/+00:00)는 안 잰다**(v02 가 이미 잰다).

    🔴 왜 갈리나: Java `OffsetDateTime.toString()` 은 **후행 0 을 트림한다** —
    `.100000` 이 `.1` 이 되면 바이트가 다르다(규칙 문서 §2-1 ㉡).
    ⚠ 표기까지 여기서 단언하면 같은 것을 두 번 재고, 표기가 정해질 때 둘 다 고쳐야 한다.
    """
    assert ".100000" in _serialized("v03_time_fraction")


def test_v04_writes_hangul_as_utf8_not_escapes() -> None:
    """`ensure_ascii=False` 라 한글이 원문으로 나간다 — 유니코드 이스케이프가 아니다."""
    text = _serialized("v04_nonascii")
    assert "비문학 독서 과제" in text
    assert "\\u" not in text, "비ASCII 가 이스케이프됐다"


def test_v05_omits_the_detection_evidence_key() -> None:
    """🔴 `detection_evidence` **만** 키 자체가 없다 — 나머지 배열은 남는다.

    ⚠ 빈 배열도 같은 결과를 낸다(`canonical.py` 의 `if request.detection_evidence:`).
    그 **동등성**은 기존 `test_an_explicit_empty_array_hashes_like_an_omitted_field` 가
    잰다 — 여기서는 **키가 없다**는 것만 든다.
    """
    payload = canonical_snapshot_payload(_request("v05_empty_containers"))
    assert "detection_evidence" not in payload
    assert payload["learning_events"] == []
    assert payload["alert_context"] == []


def test_v07_does_not_escape_the_solidus() -> None:
    """`/` 를 이스케이프하지 않는다 — 🔴 **`/` 만** 든다.

    ⚠ 제어문자는 **일부러 안 넣었다**(실요청에 올 수 없는 값을 계약처럼 만들지 않는다) ·
    서로게이트는 **만들 자리가 없다**(§39). ⇒ 이 검사를 「이스케이프 전반」으로 읽지 마라.
    """
    text = _serialized("v07_escape")
    assert "8/2주차" in text
    assert "8\\/2주차" not in text


def test_v08_writes_integers_without_a_trailing_zero() -> None:
    """정수에 후행 `.0` 이 없다 — Java 가 `double` 로 읽으면 `10.0` 이 되어 갈린다."""
    text = _serialized("v08_integers")
    assert re.search(r":\s*-?\d+\.0\b", text) is None, f"후행 .0 이 있다: {text[:200]}"
    for value in ("10", "1800", "950", "21"):
        assert f":{value}" in text
