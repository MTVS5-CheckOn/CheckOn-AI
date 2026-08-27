"""LLM JSON 구조화 출력의 엄격한 Pydantic 파서."""

import json
import logging

from pydantic import BaseModel, ValidationError

from ai.contracts.llm import FieldMissing, ParseFailed

logger = logging.getLogger(__name__)

#: 완결된 JSON 뒤에 남아도 **뜻을 만들 수 없는** 문자 — 공백과 닫는 괄호뿐이다.
#: 🔴 여기를 넓히지 마라. 설명 문장·두 번째 JSON 은 **실패로 남아야 한다**(아래 참조).
_HARMLESS_TRAILING = frozenset("}] \t\r\n")


def _strip_json_code_fence(text: str) -> str:
    candidate = text.strip()
    lines = candidate.splitlines()
    if len(lines) >= 3 and lines[0] == "```json" and lines[-1] == "```":
        return "\n".join(lines[1:-1])
    return candidate


def _decode_tolerating_stray_brackets(candidate: str, model_name: str) -> object:
    """첫 완결 JSON 값을 읽고, 뒤에 **닫는 괄호만** 남았으면 흘려보낸다.

    🔴 **왜 필요한가**(2026-08-27 실측). `gpt-5.6-luna` 가 8천 자짜리 프롬프트에 답하며
    완결된 JSON 뒤에 `}` 를 하나 더 붙였다 — 두 건 다 `finish_reason=stop` 이라 잘림이
    아니고, 앞부분은 멀쩡한 문항이었다. 종전 `json.loads` 는 후행 문자 한 글자에도
    `Extra data` 로 죽어서 **문항 전체가 버려지고 생성 시도 예산을 태웠다**
    (실측 통과율: 생성 8건 중 6건 · ParseFailed 2건).

    ⚠ **관대함의 상한은 「뜻을 만들 수 없는 문자」다.** 닫는 괄호가 남는 것은 모델이
    중첩을 잘못 센 흔적이지 새 정보가 아니다. 반면 설명 문장이나 두 번째 JSON 객체가
    붙었다면 **모델이 계약을 다르게 이해한 것**이고, 그건 흘려보내면 안 된다 —
    조용히 통과시키면 「무엇을 받았는지」가 원장에서 갈린다(불변식 8).

    ⚠ **본문을 로그에 남기지 않는다** — 문항 본문이고 마스킹 경계 밖이다(불변식 3).
    남기는 것은 스키마 이름과 버린 문자 수뿐이다.
    """

    decoder = json.JSONDecoder()
    parsed, end = decoder.raw_decode(candidate)
    trailing = candidate[end:]
    if not trailing:
        return parsed
    if set(trailing) - _HARMLESS_TRAILING:
        raise ParseFailed("LLM 구조화 출력 뒤에 해석할 수 없는 데이터가 남았다.")
    logger.warning(
        "llm.structured 후행 잉여 괄호를 버렸다 schema=%s dropped_chars=%d",
        model_name,
        len(trailing),
    )
    return parsed


def parse[ModelT: BaseModel](
    text: str,
    model_cls: type[ModelT],
    *,
    context: object | None = None,
) -> ModelT:
    """JSON 문자열을 모델로 검증하며 파싱 단계와 필드 검증 실패를 구분한다."""

    candidate = _strip_json_code_fence(text)
    try:
        parsed = _decode_tolerating_stray_brackets(candidate, model_cls.__name__)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ParseFailed("LLM 구조화 출력이 유효한 JSON이 아니다.") from exc

    try:
        return model_cls.model_validate(parsed, context=context)
    except ValidationError as exc:
        raise FieldMissing("LLM 구조화 출력이 응답 스키마를 충족하지 않는다.") from exc
