"""문의 분류 파이프라인 — `POST /v1/classify`의 조립부 (04 §3.5 · `part_a/01` §4-ⓑ).

소유: 박진희 (composition — `part_a/01` §4-ⓑ가 "문의 분류 (**composition** · Phase 1)"로
소유를 명시한다).

**흐름:** redaction(fail-closed) → 프롬프트 렌더(순수) → gateway → 구조화 파싱(enum 강제)
→ `ClassifyResult`.

🔴 **redaction이 이 모듈의 안전 축이다.** 요청의 `body_text`는 **원문**이라(counsel의
`text_masked`와 다르다) LLM에 그대로 갈 수 없다. `redact()`를 통과한 `masked_text`만
프롬프트에 들어간다 — 프롬프트 지시가 아니라 **경로 자체**로 막는다(masking_redaction §4).

⚠ **`uncertain`이어도 중단하지 않는다 — 소비자마다 fail-closed 기준이 다르다.**
`masking_redaction`:40은 "**소비자가** fail-closed 판단"이라 규정한다. 초안 생성은
학부모에게 나갈 문장을 만드니 중단이 맞지만, **분류는 산출물을 만들지 않고 판정만 한다**
— 이름은 분류에 필요 없고 `⟪확인필요⟫`가 든 텍스트는 이미 가려진 상태다. 다음 소비자
(리포트 등)를 만들 때 이 판단을 그대로 베끼지 말고 **자기 산출물 기준으로 다시 정하라.**

**분류 실패는 500이 아니다.** `error_codes` §2.5(:213)가 "분류 실패 | 인박스 | 정렬 없이
시간순 표시"로 이미 정의했다 — `classified=False`로 정직하게 응답한다(서두 원칙:
"AI가 안 하기로 판단한 것은 정상 상태").
"""

from __future__ import annotations

import logging
from typing import Final

from ai.contracts.classify import (
    AxisConfidence,
    ClassifyLlmOutput,
    ClassifyRequest,
    ClassifyResult,
)
from ai.contracts.counsel import InquirySentiment, InquiryTopic, InquiryUrgency
from ai.contracts.execution import ExecutionContext, GenerationParams, VersionSet
from ai.contracts.llm import (
    LLMRequest,
    LLMResult,
    ParseFailed,
    RedactionBlocked,
)
from ai.llm.prompts.loader import load_prompt_template
from ai.llm.structured import parse
from ai.runtime.redaction import redact

logger = logging.getLogger(__name__)

PROMPT_ID: Final = "classify.inquiry.v1"
PROMPT_VERSION: Final = "v1"

#: 파싱 실패 재시도 상한 — 불변식 6("모든 루프에 상한"). 전송 오류 재시도는 gateway가
#: 이미 하므로 여기서 중복하지 않는다(`transport_retry`).
MAX_PARSE_RETRY: Final = 2

#: 결정론 설정 — 불변식 8. `temperature=0.0`으로 샘플링을 끄고 `seed`를 고정한다.
#: ⚠ **서버가 seed를 존중하는지는 프로바이더에 달렸다** — 어댑터는 값을 그대로 넘기지만
#: (`llm/providers/openai_compat.py`의 `_build_kwargs`), 로컬 서버가 무시하면 같은 입력에
#: 다른 출력이 나올 수 있다. 99 D ㊼가 이미 열려 있는 항목이다.
_GEN_PARAMS: Final = GenerationParams(temperature=0.0, seed=20260805, max_tokens=256)

#: 전송 직전 트립와이어가 잔여 흔적을 발견해 막았다 — **장애가 아니라 미분류**다.
#: ⚠ `uncertain`은 여기 없다(아래 `classify()` 참조) — 성격이 다르다.
_FALLBACK_TRIPWIRE: Final = "tripwire_blocked"
_FALLBACK_PARSE: Final = "parse_exhausted"


def render_prompt(masked_text: str) -> str:
    """마스킹 통과분으로 프롬프트를 조립한다 — **순수 함수**(골든 스냅숏 대상).

    ⚠ 인자 이름이 `masked_text`인 것은 계약이다 — 원문(`body_text`)을 여기 넘기면
    안 된다. 호출부는 `classify()` 하나뿐이고 거기서 `redact()`를 먼저 탄다.
    """
    return load_prompt_template(PROMPT_ID).render({"body_text": masked_text})


def classify_versions() -> VersionSet:
    """이 엔드포인트의 버전 세트 — 실패 응답에도 실린다(04 §2.2).

    🔴 **`prompt_version`을 반드시 채운다.** counsel이 `prompt=null`로 나가 관측 잔여로
    지적된 선례가 있다(점검보고 B-5) — 새 LLM 축에서 같은 실수를 반복하지 않는다.
    """
    return VersionSet(
        pipeline_version="0.1.0",
        engine_version="classify-0.1",
        schema_version="0.1",
        contract_version="0.1",
        prompt_version=PROMPT_VERSION,
    )


def _unclassified(reason: str) -> ClassifyResult:
    """분류하지 못했다 — **정직한 미분류**다.

    `etc`를 확신 있는 판정처럼 내보내지 않는다. confidence 0.0 + `classified=False`가
    그 표현이고, BE는 이때 정렬을 적용하지 않는다(`error_codes` :213).
    """
    return ClassifyResult(
        topic=InquiryTopic.ETC,
        sentiment=InquirySentiment.NORMAL,
        urgency=InquiryUrgency.NORMAL,
        confidence=AxisConfidence(topic=0.0, sentiment=0.0, urgency=0.0),
        classified=False,
        fallback_reason=reason,
    )


async def classify(
    request: ClassifyRequest,
    gateway: object,
    *,
    context: ExecutionContext,
) -> ClassifyResult:
    """문의 1건을 3축으로 분류한다. `gateway`는 `complete(request, context)` 동형이면 된다.

    ⚠ `LlmUnavailable`·`LlmTimeout`은 **여기서 삼키지 않는다** — 장애는 폴백이 아니라
    503이다(`error_codes` §4). 라우터의 예외 핸들러가 받는다.
    """
    # ① 🔴 전송 전 redaction — 원문은 이 줄 뒤로 넘어가지 않는다.
    redacted = redact(request.body_text)
    if redacted.uncertain:
        # 🔴 **여기서 중단하지 않는다**(8/5 판단 · C). `⟪확인필요⟫`가 든 텍스트는
        # **이미 가려진 상태**라 원문이 새지 않는다. `masking_redaction`:40이
        # "**소비자가** fail-closed 판단"이라 규정한 대로, 판단은 소비자마다 다르다:
        #   · 초안 생성 — 학부모에게 나갈 **문장을 만든다** ⇒ 중단이 맞다(그 경로는 그대로)
        #   · 분류      — 산출물을 만들지 않고 **판정만** 한다. 이름은 분류에 필요 없고
        #                 "⟪확인필요⟫ 요즘 힘들어합니다"로도 topic이 나온다 ⇒ 계속한다
        # 중단을 유지하면 인명 검출을 강화할수록(성씨 패턴) classify가 대부분 폴백으로
        # 떨어져 **기능 자체가 사라진다**. 가려진 텍스트로 계속하는 편이 안전하고 유용하다.
        logger.info(
            "classify.redaction_uncertain_continue inquiry_ref=%s findings=%d",
            request.inquiry_ref,
            len(redacted.findings),
        )

    prompt = render_prompt(redacted.masked_text)
    llm_request = LLMRequest(
        role=load_prompt_template(PROMPT_ID).role,
        prompt=prompt,
        prompt_id=PROMPT_ID,
        prompt_version=PROMPT_VERSION,
        generation_params=_GEN_PARAMS,
    )

    # ② 파싱 실패만 재시도한다(≤2). 같은 요청을 다시 보내는 것이고, 전송 재시도는
    #    gateway가 이미 했다 — 여기서 또 돌리면 상한이 곱해진다.
    for attempt in range(MAX_PARSE_RETRY + 1):
        try:
            result: LLMResult = await gateway.complete(llm_request, context)  # type: ignore[attr-defined]
        except RedactionBlocked:
            # 🔴 트립와이어 차단은 **폴백을 유지한다** — `uncertain`과 성격이 다르다.
            # 이건 "가려지지 않은 게 남았다"는 신호라 그대로 보내면 안 된다.
            # 재시도해도 같은 프롬프트라 즉시 수렴한다(09 §1-10 ③ 재시도 대상 제외).
            logger.info("classify.tripwire_blocked inquiry_ref=%s", request.inquiry_ref)
            return _unclassified(_FALLBACK_TRIPWIRE)
        try:
            output = parse(result.text or "", ClassifyLlmOutput)
        except ParseFailed:
            # ⚠ 예외 메시지·로그에 본문을 싣지 않는다 — masked_text조차 남기지 않는다(B-3).
            logger.warning(
                "classify.parse_failed inquiry_ref=%s attempt=%d",
                request.inquiry_ref,
                attempt + 1,
            )
            continue
        return ClassifyResult(
            topic=output.topic,
            sentiment=output.sentiment,
            urgency=output.urgency,
            confidence=output.confidence,
        )
    return _unclassified(_FALLBACK_PARSE)


__all__ = [
    "MAX_PARSE_RETRY",
    "PROMPT_ID",
    "PROMPT_VERSION",
    "classify",
    "classify_versions",
    "render_prompt",
]
