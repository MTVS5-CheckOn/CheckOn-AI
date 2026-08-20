"""호출 자리별 콜당 상한 표 — 스키마와 「모르는 자리는 안 조인다」 계약 검사."""

from pathlib import Path

import pytest

from ai.llm.call_timeouts import (
    CallTimeoutLoadError,
    CallTimeoutTable,
    load_call_timeouts,
)


def test_the_shipped_table_loads_and_validates() -> None:
    """정본 `call_timeouts.yaml`이 스키마를 통과한다."""

    table = load_call_timeouts()

    assert table.schema_version == "llm-call-timeouts.v1"


def test_every_registered_cap_is_positive() -> None:
    """정본에 값이 생기더라도 0·음수는 못 들어온다 — 뜻이 안 되는 값이다.

    ⚠ 이 검사는 표가 빈 지금도 서고, 값이 채워진 뒤에도 그대로 선다.
    「지금 비어 있다」를 단언하지 않는 이유가 그것이다 — 값을 채우는 정당한 변경이
    검사를 깨뜨리면, 다음 사람은 검사를 고치는 게 아니라 **지워 버린다.**
    """

    for prompt_id, seconds in load_call_timeouts().call_timeouts.items():
        assert seconds > 0, f"{prompt_id}의 상한이 0 이하다"


def test_an_unregistered_prompt_is_not_capped() -> None:
    """🔴 모르는 `prompt_id`는 `None`이다 — 예외가 아니다.

    이 표는 **선택적 조임**이지 프롬프트 정본이 아니다. counsel·detect의 `prompt_id`는
    `prompts/registry.yaml`에도 없다 ⇒ 여기서 던지면 **표에 없는 자리가 전부 죽는다.**
    조이려고 만든 장치가 안 조인 자리를 깨뜨리는 것은 처방이 아니라 사고다.
    """

    assert load_call_timeouts().get("등록된 적 없는 prompt_id") is None


def test_a_zero_or_negative_cap_is_refused() -> None:
    """경계 — 0과 음수는 「즉시 만료」라 상한이 아니다."""

    with pytest.raises(ValueError, match="0보다 커야 한다"):
        CallTimeoutTable(call_timeouts={"composition/briefing": 0.0})

    with pytest.raises(ValueError, match="0보다 커야 한다"):
        CallTimeoutTable(call_timeouts={"composition/briefing": -1.0})


def test_an_unknown_key_is_refused() -> None:
    """오타가 조용히 무시되면 「상한을 걸었다」는 착각이 남는다."""

    with pytest.raises(ValueError):
        CallTimeoutTable.model_validate(
            {"schema_version": "llm-call-timeouts.v1", "call_timeout": {}}
        )


def test_a_missing_table_file_fails_loudly(tmp_path: Path) -> None:
    """실패 — 파일이 없으면 조용히 빈 표로 떨어지지 않는다."""

    with pytest.raises(CallTimeoutLoadError, match="읽을 수 없다"):
        load_call_timeouts(tmp_path / "없는파일.yaml")


def test_a_malformed_table_fails_loudly(tmp_path: Path) -> None:
    """실패 — 스키마가 깨지면 기동에서 죽는다(조용한 무시 금지)."""

    broken = tmp_path / "call_timeouts.yaml"
    broken.write_text(
        "schema_version: llm-call-timeouts.v1\ncall_timeouts:\n  a: 문자열\n",
        encoding="utf-8",
    )

    with pytest.raises(CallTimeoutLoadError, match="스키마 오류"):
        load_call_timeouts(broken)


def test_every_key_names_a_call_site_that_actually_exists() -> None:
    """🔴 표의 키는 **실재하는 `prompt_id`** 여야 한다 — 오타는 조용히 아무것도 안 한다.

    상한을 걸었다고 적어 두고 키가 한 글자 틀리면 `get()` 이 `None` 을 돌려주고
    호출은 전역 상한으로 그대로 간다 — **「걸었다」와 「걸렸다」가 갈린다.**
    fail-open 은 모르는 자리에 쓰라고 둔 것이지 오타를 덮으라고 둔 게 아니라,
    표에 **적은** 키만은 여기서 센다.

    ⚠ 지금 표가 비어 있어 이 검사는 공회전이다. 그래도 미리 달아 둔다 —
    값을 처음 넣는 사람이 그때 오타를 낸다.
    """

    source_root = Path(__file__).resolve().parents[4] / "src" / "ai"
    #: 🔴 **경로가 틀리면 haystack이 비고 검사가 「전부 오타」로 뒤집힌다** — 그런데 표가
    #:  비어 있으면 그때도 통과한다(공회전). 실제로 `parents[3]`으로 한 번 틀렸고 표가
    #:  비어서 green이었다 — 이 절이 경고하는 그 실패 모양을 이 검사 자신이 냈다.
    #:  ⇒ 경로 존재를 먼저 단언해 「안 재는 상태」를 red로 만든다.
    assert source_root.is_dir(), f"src/ai를 못 찾았다: {source_root}"
    #: 🔴 **표 자신을 빼지 않으면 검사가 자족한다** — 키는 언제나 자기 파일에 적혀 있으므로
    #:  haystack에 `call_timeouts.yaml`이 들어가면 **무슨 오타를 넣어도 green**이다.
    #:  실제로 그렇게 짰다가 가짜 키 `bogus.prompt.that.does.not.exist`로 재 보고서야 알았다.
    #:  ⇒ 「재는 것처럼 보이지만 안 재는 검사」는 없는 것보다 나쁘다(99 #124).
    table_file = source_root / "llm" / "call_timeouts.yaml"
    haystack = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in source_root.rglob("*")
        if path.suffix in {".py", ".yaml"} and path.is_file() and path != table_file
    )

    unknown = sorted(
        prompt_id
        for prompt_id in load_call_timeouts().call_timeouts
        if prompt_id not in haystack
    )

    assert not unknown, f"src/ai 어디에도 없는 prompt_id 가 표에 있다: {unknown}"
