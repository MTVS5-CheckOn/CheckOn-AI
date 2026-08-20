"""LLM JSON 구조화 출력의 엄격한 Pydantic 파서."""

import json

from pydantic import BaseModel, ValidationError

from ai.contracts.llm import FieldMissing, ParseFailed


def _strip_json_code_fence(text: str) -> str:
    candidate = text.strip()
    lines = candidate.splitlines()
    if len(lines) >= 3 and lines[0] == "```json" and lines[-1] == "```":
        return "\n".join(lines[1:-1])
    return candidate


def parse[ModelT: BaseModel](
    text: str,
    model_cls: type[ModelT],
    *,
    context: object | None = None,
) -> ModelT:
    """JSON 문자열을 모델로 검증하며 파싱 단계와 필드 검증 실패를 구분한다."""

    candidate = _strip_json_code_fence(text)
    try:
        parsed: object = json.loads(candidate)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ParseFailed("LLM 구조화 출력이 유효한 JSON이 아니다.") from exc

    try:
        return model_cls.model_validate(parsed, context=context)
    except ValidationError as exc:
        raise FieldMissing("LLM 구조화 출력이 응답 스키마를 충족하지 않는다.") from exc
