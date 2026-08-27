"""구조화 출력 파서의 엄격한 JSON·Pydantic 실패 분류 검사."""

import pytest
from pydantic import BaseModel, ConfigDict, Field

from ai.contracts.llm import FieldMissing, ParseFailed
from ai.llm.structured import parse


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    score: int = Field(ge=0)


def test_parse_valid_json() -> None:
    assert parse('{"label":"정상","score":3}', _Payload) == _Payload(label="정상", score=3)


def test_parse_json_code_fence() -> None:
    text = '```json\n{"label":"정상","score":3}\n```'
    assert parse(text, _Payload) == _Payload(label="정상", score=3)


def test_invalid_json_raises_parse_failed() -> None:
    with pytest.raises(ParseFailed):
        parse('{"label":"정상","score":}', _Payload)


def test_missing_field_raises_field_missing() -> None:
    with pytest.raises(FieldMissing):
        parse('{"label":"정상"}', _Payload)


def test_validation_failure_raises_field_missing() -> None:
    with pytest.raises(FieldMissing):
        parse('{"label":"정상","score":-1}', _Payload)


def test_non_json_code_fence_is_not_tolerated() -> None:
    with pytest.raises(ParseFailed):
        parse('```\n{"label":"정상","score":3}\n```', _Payload)


# ── 후행 잉여 괄호 (2026-08-27 실측) ──────────────────────────
#
# `gpt-5.6-luna` 가 완결된 JSON 뒤에 `}` 를 하나 더 붙였다. 두 건 다
# `finish_reason=stop` 이라 잘림이 아니었고, 종전 파서는 그 한 글자에 문항 전체를
# 버리고 생성 시도 예산을 태웠다. 아래 두 경계 문면은 그때 실제로 받은 끝부분이다.


@pytest.mark.parametrize(
    "trailing",
    [
        "} ",  # 실측 1회차 — `...}]} }`
        "}",  # 실측 3회차 — `...}]}}`
        "\n}\n",
        "]",
    ],
)
def test_stray_closing_brackets_are_dropped(trailing: str) -> None:
    """닫는 괄호는 뜻을 만들 수 없다 — 흘려보내고 문항을 살린다."""

    text = '{"label":"정상","score":3}' + trailing

    assert parse(text, _Payload) == _Payload(label="정상", score=3)


def test_dropping_stray_brackets_is_recorded(caplog: pytest.LogCaptureFixture) -> None:
    """🔴 조용히 넘기지 않는다 — 무엇을 버렸는지 관측에 남는다.

    ⚠ 본문은 남기지 않는다(불변식 3). 스키마 이름과 버린 문자 수뿐이다.
    """

    with caplog.at_level("WARNING", logger="ai.llm.structured"):
        parse('{"label":"정상","score":3}}', _Payload)

    assert "_Payload" in caplog.text
    assert "dropped_chars=1" in caplog.text
    assert "정상" not in caplog.text


@pytest.mark.parametrize(
    "trailing",
    [
        " 위 JSON 이 요청하신 문항입니다.",  # 설명 문장
        '{"label":"두번째","score":1}',  # 두 번째 JSON 객체
        ", ",  # 배열의 조각
    ],
)
def test_meaningful_trailing_data_still_fails(trailing: str) -> None:
    """🔴 관대함의 상한 — 뜻을 가진 후행 데이터는 여전히 실패다.

    흘려보내면 「무엇을 받았는지」가 원장에서 갈린다(불변식 8).
    """

    with pytest.raises(ParseFailed):
        parse('{"label":"정상","score":3}' + trailing, _Payload)
