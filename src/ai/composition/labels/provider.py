"""라벨 제안 생성기 — 프롬프트 조립 → LLM → **행 파싱** → 게이트로 넘긴다(99 #191).

🔴 **파서가 게이트를 대신하지 않는다.** 여기서는 «형식이 맞는가» 만 보고, «인용이 실존하는가»
는 `grounding.py` 가 본다 — 두 층을 섞으면 «파서가 통과시켰으니 근거가 있다» 가 된다.

⚠ 🔴 **`--provider fake` 로 도는 것까지가 이 회차다**(2026-08-22 · 실 LLM 0회).
콜당 상한은 **전역이 받는다** — `llm/call_timeouts.yaml` 의 라벨 자리는 **실측 뒤**에 채운다
(#191). ⚠ 값을 지어내서 넣지 않는다.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from typing import Final, Protocol

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from ai.composition.labels.grounding import keep_sendable_history
from ai.composition.labels.prompt import PROMPT_ID, PROMPT_VERSION, assemble_prompt
from ai.contracts.composition import CommStyle, Frequency, Interest, Sensitivity
from ai.contracts.counsel import LabelSuggestion
from ai.contracts.execution import ExecutionContext, GenerationParams
from ai.contracts.labels import (
    MIN_HISTORY,
    EvidenceQuote,
    HistoryItem,
    SuggestedLabel,
)
from ai.contracts.llm import LLMProvider, LLMRequest, ModelRole
from ai.db.repositories.run_store import default_llm_call_collector
from ai.llm.gateway import LlmGateway
from ai.runtime.env_files import ENV_FILES
from ai.runtime.errors import LlmUpstreamDown, RedactionUncertain
from ai.runtime.redaction import redact
from ai.runtime.trace_masking import RedactionTripwireTraceHook

logger = logging.getLogger(__name__)

#: 생성기 미구현 사유 — `error_codes.md` 축의 `detail.reason`.
GENERATOR_NOT_IMPLEMENTED: Final = "label_generator_not_implemented"

#: 프롬프트가 마스킹 문지기에 걸린 사유 — 🔴 **이력 본문이 원인**이고
#: 그건 `redact()` 오탐일 수도 있다(99 #104·#123). 사유를 갈라 둬야 원인을 센다.
PROMPT_REDACTION_BLOCKED: Final = "prompt_redaction_blocked"

#: 🔴 **축 → 허용 값**의 정본은 `contracts/composition.py` 의 enum 넷이다.
#: 여기서 값을 나열하지 않는다 — 나열하면 그 목록이 갈린다(#02).
_AXIS_VALUES: Final[dict[str, type[CommStyle | Sensitivity | Interest | Frequency]]] = {
    "comm": CommStyle,
    "sensitivity": Sensitivity,
    "interest": Interest,
    "frequency": Frequency,
}

#: 출력 한 줄 — `축 | 값 | confidence | record_id | 인용문`.
#: ⚠ 인용문에 `|` 가 들어갈 수 있어 **앞 넷만 가른다**(`maxsplit=4`).
_LINE_FIELDS: Final = 5

_GEN_PARAMS: Final = GenerationParams(temperature=0.0)
"""🔴 **결정론**(불변식 8) — 같은 이력이면 같은 제안이어야 강사가 두 번 열었을 때 안 흔들린다."""


class LabelSuggestProvider(Protocol):
    """제안 생성 — 순수 계약. 구현이 LLM 을 쓰든 안 쓰든 라우터는 모른다."""

    async def suggest(
        self,
        *,
        guardian_ref: str,
        history: Sequence[HistoryItem],
        context: ExecutionContext,
    ) -> tuple[SuggestedLabel, ...]:
        """🔴 **`context` 를 받는다** — 원장 키가 라우터의 실행과 같아야 한다(불변식 8).

        대역은 안 쓰더라도 계약에 둔다: 없으면 실 provider 만 다른 시그니처가 되고,
        그때 라우터가 **둘을 다르게 부르게** 된다.
        """
        ...


def parse_suggestions(text: str, *, guardian_ref: str) -> tuple[SuggestedLabel, ...]:
    """LLM 출력을 제안으로 — 🔴 **형식이 틀린 줄은 조용히 버린다**(불변식 4).

    ⚠ 예외를 올리면 **한 줄의 형식 오류가 나머지 제안까지 죽인다** — counsel 의 강조점
    드롭과 같은 결이다. 다만 **몇 줄을 버렸는지는 로그로 남긴다**(조용한 드롭 금지).
    🔴 **본문·인용문을 로그에 싣지 않는다**(불변식 3 · 99 #80) — 수까지다.
    """
    kept: list[SuggestedLabel] = []
    dropped = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|", _LINE_FIELDS - 1)]
        if len(parts) != _LINE_FIELDS:
            dropped += 1
            continue
        axis, value, confidence, record_id, quote = parts
        enum = _AXIS_VALUES.get(axis)
        if enum is None:
            dropped += 1
            continue
        try:
            enum(value)
            kept.append(
                SuggestedLabel(
                    suggestion_id=uuid.uuid4(),
                    guardian_ref=guardian_ref,
                    label=LabelSuggestion(axis=axis, value=value),
                    confidence=float(confidence),
                    evidence_quotes=(EvidenceQuote(record_id=record_id, quote=quote),),
                )
            )
        except (ValueError, ValidationError):
            #: 🔴 **값이 열거형 밖**이거나 confidence 가 수가 아니거나 필드가 비었다.
            #: 04 §3.7 이 «4축 enum 만 · 자유 텍스트 라벨은 스키마상 불가» 라 적는다.
            dropped += 1
    if dropped:
        logger.info("라벨 제안 줄 드롭 guardian=%s 수=%d", guardian_ref, dropped)
    return tuple(kept)


class MissingLabelSuggestProvider:
    """🔴 **생성기가 없다는 사실을 그대로 낸다** — 빈 배열로 위장하지 않는다.

    ⚠ 🔴 **(8/22) 이 대역은 남는다.** 생성기가 생겼어도 «생성기가 없다» 와 «제안할 근거가
    없다» 를 가르는 규율은 그대로이고, **provider 를 못 만드는 상황**(설정 미비 등)에서
    이것이 돈다. 04 §3.7 이 빈 배열을 «게이트가 전량 드롭했다» 로 정의하기 때문이다.
    """

    async def suggest(
        self,
        *,
        guardian_ref: str,
        history: Sequence[HistoryItem],
        context: ExecutionContext,
    ) -> tuple[SuggestedLabel, ...]:
        del guardian_ref, history, context
        raise LlmUpstreamDown(
            "라벨 제안 생성기 미구현",
            {
                "reason": GENERATOR_NOT_IMPLEMENTED,
                "detail": "프롬프트·생성 경로가 아직 없다 — 99 #191",
            },
        )


class GatewayLabelSuggestProvider:
    """게이트웨이 경유 생성기 — **모든 LLM 호출은 gateway 를 지난다**(03 §2 · 01 §5).

    ⚠ 🔴 **이 회차에 실 LLM 으로 안 돌렸다**(2026-08-22 · 0회). 배선만 서 있고, 콜당 실측과
    상한은 다음 회차다(#191). ⚠ 지금은 **전역 90s** 가 받는다.
    """

    def __init__(self, gateway: LlmGateway) -> None:
        self._gateway = gateway

    async def suggest(
        self,
        *,
        guardian_ref: str,
        history: Sequence[HistoryItem],
        context: ExecutionContext,
    ) -> tuple[SuggestedLabel, ...]:
        #: 🔴 **이력 한 건씩 걸러 낸다**(99 #192 ⓑ · 8/22) — 종전에는 이력을 **통째로**
        #: 조립해 선검사해서 **한 건의 오탐이 제안 전체를 500 으로 죽였다.**
        #: ⚠ 🔴 실 LLM 측정 **전에** 하는 이유: 걸리는 콜이 500 이면 그 콜이 **표본에서
        #: 빠진다** — #110(«상한이 표본을 자른다»)이 «마스킹이 자른다» 로 나타난다.
        sendable, dropped_history = keep_sendable_history(history)
        #: 🔴 **판정 대기 구간**(99 #194) — 걸러서 `MIN_HISTORY` 미만이 되면 무엇을 낼지
        #: 아직 안 정했다. 「없다」(빈 배열)로 내면 04 §3.7 이 정의한 «게이트가 전량
        #: 드롭했다» 와 **증거상 같아 보인다** ⇒ **지금은 종전 동작(전체 조립 → 500)을
        #: 그대로 둔다.** 결정된 축만 고치고 안 정한 축은 **안 건드린다.**
        if len(sendable) < MIN_HISTORY:
            #: 🔴 **조용히 넘어가지 않는다** — 이 줄이 판정을 기다리는 경로다(99 #194).
            logger.warning(
                "라벨 이력이 걸러서 미달 남음=%d 뺌=%d 최소=%d — "
                "판정 전이라 전체를 태운다(99 #194)",
                len(sendable),
                len(dropped_history),
                MIN_HISTORY,
            )
            sendable = tuple(history)
        prompt = assemble_prompt(sendable)
        #: 🔴 **전송 전 선검사 — fail-closed**(불변식 3 · `classify/classifier.py` 선례).
        #: `history[].text` 는 **BE 1차 마스킹 통과본이지 우리 기준의 통과분이 아니다**
        #: (04 Open-4d) ⇒ 우리 문지기를 다시 지나야 한다.
        #: ⚠ 🔴 **트립와이어에만 기대면 안 된다** — 그건 전송 직전의 **마지막** 관문이고,
        #: 거기서 막히면 원인이 «게이트웨이 어딘가» 로만 보인다. 여기서 걸러야
        #: **어느 이력이 문제인지**가 이 층의 사실로 남는다.
        #: 🔴 **`uncertain` 만 보지 않는다** — 트립와이어는 `findings or uncertain` 으로
        #: 막으므로, `uncertain` 만 보면 «조립은 통과인데 전송이 죽는다» 가 된다
        #: (99 #83 이 실측한 그 갈림). ⚠ 실측(8/22 · 99 #104 계열): `성적표를`·`문제집을` 이
        #: `findings` 를 내고 `uncertain=False` 다 — 딱 그 틈이다.
        outcome = redact(prompt)
        if outcome.uncertain or outcome.findings:
            raise RedactionUncertain(
                "라벨 제안 프롬프트가 마스킹 문지기를 못 지났다",
                {
                    "reason": PROMPT_REDACTION_BLOCKED,
                    "guardian_ref": guardian_ref,
                    #: ⚠ 걸러도 미달이라 전체를 태웠다는 뜻 — 판정 대기 구간(99 #194).
                    "dropped_history": len(history) - len(sendable),
                },
            )
        result = await self._gateway.complete(
            LLMRequest(
                role=ModelRole.COUNSELOR,
                prompt=prompt,
                prompt_id=PROMPT_ID,
                prompt_version=PROMPT_VERSION,
                generation_params=_GEN_PARAMS,
            ),
            context,
        )
        return parse_suggestions(result.text or "", guardian_ref=guardian_ref)


class FakeLabelSuggestProvider:
    """결정론 대역 — 🔴 **프롬프트를 실제로 조립해 보고**, 이력에서 한 줄을 인용한다.

    ⚠ 🔴 **게이트를 흉내 내지 않는다** — 인용을 **첫 이력에서 그대로** 떠오므로 실존
    게이트를 자연히 통과하고, 그 통과는 **게이트가 판정한 것**이지 대역이 정한 것이 아니다.
    ⚠ 🔴 **프롬프트 조립을 부른다** — 안 부르면 «대역으로는 도는데 템플릿이 깨져 있다» 를
    못 잡는다(로그 129 — «대역이 우리 코드를 흉내 내면 원본을 안 잰다»).
    """

    async def suggest(
        self,
        *,
        guardian_ref: str,
        history: Sequence[HistoryItem],
        context: ExecutionContext,
    ) -> tuple[SuggestedLabel, ...]:
        del context
        prompt = assemble_prompt(history)
        if not history or "제안:" not in prompt:
            return ()
        first = history[0]
        #: ⚠ 실 LLM 이 낼 형태 그대로 만들어 **같은 파서**를 태운다.
        line = f"comm | data | 0.5 | {first.record_id} | {first.text}"
        return parse_suggestions(line, guardian_ref=guardian_ref)


def build_label_gateway(provider: LLMProvider | None = None) -> LlmGateway:
    """라벨 게이트웨이 — briefing·counsel 과 같은 조립 규약.

    🔴 `trace_masking_hook` 을 **조립부가 주입한다** — 미주입이면 `LANGSMITH_TRACING=true`
    에서 게이트웨이 생성이 실패한다(09 §2-16 P1′ 기동 가드). ⚠ 그 훅이 **전송 직전 마지막
    관문**이다(불변식 3) — 이력 본문이 오탐에 걸리면 여기서 막힌다(99 #104 와 같은 자리).
    """
    from ai.llm.providers.openai_compat import (  # noqa: PLC0415 — 벤더는 조립부에서만
        build_openai_compat_provider,
    )

    return LlmGateway(
        {ModelRole.COUNSELOR: provider or build_openai_compat_provider()},
        recorder=default_llm_call_collector(),
        transport_retry={ModelRole.COUNSELOR: 0},
        trace_masking_hook=RedactionTripwireTraceHook(),
    )


class LabelSettings(BaseSettings):
    """`LLM_PROVIDER` 하나만 본다 — 다른 축과 같은 규약."""

    model_config = SettingsConfigDict(env_file=ENV_FILES, extra="ignore")

    llm_provider: str = "fake"


def build_label_suggest_provider(
    settings: LabelSettings | None = None,
) -> LabelSuggestProvider:
    """조립부 — `LLM_PROVIDER` 로 고른다(counsel·briefing 과 같은 규약)."""
    resolved = settings or LabelSettings()
    if resolved.llm_provider == "openai_compat":
        return GatewayLabelSuggestProvider(build_label_gateway())
    return FakeLabelSuggestProvider()


__all__ = [
    "GENERATOR_NOT_IMPLEMENTED",
    "PROMPT_REDACTION_BLOCKED",
    "FakeLabelSuggestProvider",
    "GatewayLabelSuggestProvider",
    "LabelSettings",
    "LabelSuggestProvider",
    "MissingLabelSuggestProvider",
    "build_label_suggest_provider",
    "parse_suggestions",
]
