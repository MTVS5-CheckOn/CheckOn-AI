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
