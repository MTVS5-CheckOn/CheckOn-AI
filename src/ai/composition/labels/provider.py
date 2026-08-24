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

from ai.composition.labels.counters import LabelLayerCounts
from ai.composition.labels.grounding import keep_sendable_history
from ai.composition.labels.prompt import PROMPT_ID, PROMPT_VERSION, assemble_prompt
from ai.contracts.composition import CommStyle, Frequency, Interest, Sensitivity
from ai.contracts.counsel import LabelSuggestion
from ai.contracts.execution import ExecutionContext
from ai.contracts.labels import (
    EvidenceQuote,
    HistoryItem,
    SuggestedLabel,
)
from ai.contracts.llm import LLMProvider, LLMRequest, ModelRole
from ai.db.repositories.run_store import default_llm_call_collector
from ai.llm.determinism import deterministic_params
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

#: 🔴 **이력이 전부 걸려 조립할 것이 0건** — «제안할 근거가 없다»(빈 배열)와 **다른 사실**이다.
HISTORY_ALL_BLOCKED: Final = "history_all_blocked"


class LabelHistoryUnusable(RedactionUncertain):
    """🔴 **«없다»가 아니라 «못 한다»** — 이력이 전부 마스킹 문지기에 걸렸다(99 #194).

    ⚠ 🔴 **`RedactionUncertain` 을 상속한다 — 새 상태코드를 만들지 않았다.** 같은 원인
    (마스킹 불확실)이고 `error_codes.md` §4 가 그 예외에 **500 · 상세 미노출**을 못박아 뒀다.
    ⇒ 상태·detail 정책을 **그대로 물려받고**, 갈라진 것은 **사유 이름과 로그**다.

    🔴 **강사 화면에 무엇을 보여줄지는 아직 판정 전이다**(99 #194) — 200 응답 스키마에
    `reason` 자리가 **없고**(실측 8/24 · `LabelSuggestResponse.suggestions` 하나뿐), 새 필드는
    **BE 가 읽어야 할 계약 표면**이라 준영님·승우님 접점이 생긴다. ⇒ 지금은 **종전과 같은
    500** 이고, 달라진 것은 «빈 배열로 위장하지 않는다» 와 **우리 로그가 두 경우를 가른다** 는 것.
    ⚠ 그 판정이 나면 이 클래스가 그 자리를 받는다.
    """

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

_GEN_PARAMS: Final = deterministic_params()
"""🔴 **재현 축의 정본은 `llm/determinism.py` 다** — 여기서 값을 만들지 않는다.

⚠ 🔴 **(8/24 실측) 종전에는 `GenerationParams(temperature=0.0)` 를 직접 만들었고, 실 LLM
4콜이 전부 `400` 이었다.** 원인이 저장소에 **이미 적혀 있었다**
(`llm/providers/openai_compat.py` · 99 #51 · 2026-08-13 직접 curl):

    Unsupported value: 'temperature' does not support 0 with this model.
    Only the default (1) value is supported.

🔴 **재현을 만드는 것은 `seed` 이지 `temperature` 가 아니다**(8/4 실측 — `temperature=0.0`
인데 같은 입력이 다른 출력을 냈다). `deterministic_params()` 가 그 판단의 정본이고,
counsel·briefing 이 **이미 그것을 쓴다** — 라벨만 자기 값을 만들었다.
⚠ 🔴 **그 함수가 보증하는 것은 «요청이 흔들리지 않는다» 까지다**(8/13 실측: 같은 seed
8회에 3종) — «같은 제안이 나온다» 가 아니다. §E 의 결정론 측정이 그 위에서 읽힌다."""


class LabelSuggestProvider(Protocol):
    """제안 생성 — 순수 계약. 구현이 LLM 을 쓰든 안 쓰든 라우터는 모른다."""

    async def suggest(
        self,
        *,
        guardian_ref: str,
        history: Sequence[HistoryItem],
        context: ExecutionContext,
        counts: LabelLayerCounts | None = None,
    ) -> tuple[SuggestedLabel, ...]:
        """🔴 **`context` 를 받는다** — 원장 키가 라우터의 실행과 같아야 한다(불변식 8).

        대역은 안 쓰더라도 계약에 둔다: 없으면 실 provider 만 다른 시그니처가 되고,
        그때 라우터가 **둘을 다르게 부르게** 된다.
        """
        ...


def parse_suggestions(
    text: str,
    *,
    guardian_ref: str,
    counts: LabelLayerCounts | None = None,
) -> tuple[SuggestedLabel, ...]:
    """LLM 출력을 제안으로 — 🔴 **형식이 틀린 줄은 조용히 버린다**(불변식 4).

    ⚠ 예외를 올리면 **한 줄의 형식 오류가 나머지 제안까지 죽인다** — counsel 의 강조점
    드롭과 같은 결이다. 다만 **몇 줄을 버렸는지는 로그로 남긴다**(조용한 드롭 금지).
    🔴 **본문·인용문을 로그에 싣지 않는다**(불변식 3 · 99 #80) — 수까지다.

    ⚠ 🔴 **(8/24 정정 · 99 #206) 여기서 병합하지 않는다.** №70 이 `merge_duplicate_axes` 를
    이 함수 **안**에 넣었더니 `ground_suggestions` 의 뜻이 조용히 바뀌었다 —
    병합된 제안은 인용을 여럿 들고 게이트는 «하나라도 실패하면 전체 드롭» 이라,
    🔴 **«근거 실존» 이 «근거 묶음 **전부** 실존» 이 됐다.** 두 함수 다 안 고쳤는데 뜻이 바뀌었다.
    ⇒ **병합은 게이트 뒤**다(조립부가 부른다).
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
    if counts is not None:
        counts.record_parse(parsed=len(kept), dropped=dropped)
    return tuple(kept)


def merge_duplicate_axes(
    suggestions: Sequence[SuggestedLabel],
    *,
    counts: LabelLayerCounts | None = None,
) -> tuple[SuggestedLabel, ...]:
    """🔴 같은 `(축, 값)` 이 여러 번 나오면 **합친다 — 버리지 않는다**(99 #204).

    ⚠ 🔴 **실측(8/24 · 실 LLM)**: 한 콜이 `(interest, attitude)` 를 **3번** 냈고
    **파서도 게이트도 안 막았다** — 둘 다 그 축이 아니다(파서는 형식, 게이트는 인용 실존).
    ⇒ 강사 화면에 **같은 칩이 세 개** 뜬다.

    🔴 **버리는 방식을 안 쓴다**:
        ❌ 먼저 나온 것만 남긴다        → **근거가 사라진다**
        ❌ confidence 최고만 남긴다      → **나머지 인용이 사라진다**
        ✅ **`evidence_quotes` 를 모은다** — 강사가 «이 라벨의 근거가 셋» 을 본다

    ⚠ `confidence` 는 **가장 높은 것**을 쓴다 — 평균이면 «재서 정한 값» 이 되는데
    우리가 잰 것이 아니다. **그 판단의 최대 확신**이 맞다.
    ⚠ 인용은 **중복 제거**한다(같은 `record_id` + 같은 문장이면 하나).
    ⚠ `suggestion_id` 는 **첫 것**을 유지한다 — 합쳐진 것이 새 제안은 아니다.
    🔴 **순서는 첫 등장 순**이다 — 정렬하면 모델의 우선순위가 사라진다.
    """
    merged: dict[tuple[str, str], SuggestedLabel] = {}
    for suggestion in suggestions:
        key = (suggestion.label.axis, suggestion.label.value)
        previous = merged.get(key)
        if previous is None:
            merged[key] = suggestion
            continue
        quotes = list(previous.evidence_quotes)
        seen = {(q.record_id, q.quote) for q in quotes}
        for quote in suggestion.evidence_quotes:
            if (quote.record_id, quote.quote) not in seen:
                quotes.append(quote)
                seen.add((quote.record_id, quote.quote))
        merged[key] = previous.model_copy(
            update={
                "confidence": max(previous.confidence, suggestion.confidence),
                "evidence_quotes": tuple(quotes),
            }
        )
    if len(merged) != len(suggestions):
        #: 🔴 **조용히 합치지 않는다** — 몇 건이 합쳐졌는지 남긴다(본문 미기재).
        logger.info(
            "라벨 제안 중복 병합 %d → %d", len(suggestions), len(merged)
        )
    if counts is not None:
        counts.record_merge(before=len(suggestions), after=len(merged))
    return tuple(merged.values())


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
        counts: LabelLayerCounts | None = None,
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
        counts: LabelLayerCounts | None = None,
    ) -> tuple[SuggestedLabel, ...]:
        #: 🔴 **이력 한 건씩 걸러 낸다**(99 #192 ⓑ · 8/22) — 종전에는 이력을 **통째로**
        #: 조립해 선검사해서 **한 건의 오탐이 제안 전체를 500 으로 죽였다.**
        #: ⚠ 🔴 실 LLM 측정 **전에** 하는 이유: 걸리는 콜이 500 이면 그 콜이 **표본에서
        #: 빠진다** — #110(«상한이 표본을 자른다»)이 «마스킹이 자른다» 로 나타난다.
        sendable, dropped_history = keep_sendable_history(history, counts=counts)
        #: 🔴 **조립 하한을 두지 않는다 — 1건 이상이면 조립한다**(8/24 판정 · 99 #194).
        #: 근거 넷:
        #:   ① 04 §3.7 의 「5건 이상」은 **BE 의 대상 선정 조건**이다 [읽음 `04:665` —
        #:      «소통 이력 5건 이상 + 라벨 미설정인 대상만»]. AI 의 조립 조건이 아니다.
        #:   ② 🔴 **라벨은 이력 하나하나가 독립 신호다** — counsel 은 fact 가 엮여 하나를
        #:      빼면 근거가 무너지지만(99 #195) 라벨은 그렇지 않다.
        #:   ③ 🔴 **게이트가 이미 재고 있다** — `ground_suggestions` 가 인용 실존(id + 본문
        #:      대조)을 본다. 근거가 약하면 거기서 떨어진다. 건수 하한을 또 두는 것은
        #:      **같은 것을 두 번 재는 것**이다.
        #:   ④ 프롬프트가 «근거 없으면 아무것도 내지 마라 · confidence 를 낮게» 로 이미
        #:      지시한다(불변식 2).
        #: ⚠ 🔴 **실측 없는 값을 정하지 않는다**(№58 규율) — 조립 하한의 실측은 **0**이고,
        #: 종전에 `MIN_HISTORY`(=요청 하한)를 그 자리에 쓴 것이 **실측 없이 정한 값**이었다.
        if not sendable:
            #: 🔴 **0건은 «못 한다»이지 «없다»가 아니다** — 빈 배열로 내면 04 §3.7 이 정의한
            #: «게이트가 전량 드롭했다» 와 **증거상 같아 보인다**(№63 §E ③ · №64 §E 규율).
            #: ⚠ 4xx 는 안 쓴다 — **입력은 조건을 만족했고 우리가 걸러서 0이 된 것**이라
            #: 강사가 «내가 잘못 보냈나» 로 읽는다(№66 §3-2).
            raise LabelHistoryUnusable(
                "이력이 전부 마스킹 문지기에 걸렸다",
                {
                    "reason": HISTORY_ALL_BLOCKED,
                    "guardian_ref": guardian_ref,
                    "dropped_history": len(dropped_history),
                },
            )
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
        return parse_suggestions(
            result.text or "", guardian_ref=guardian_ref, counts=counts
        )


class FakeLabelSuggestProvider:
    """결정론 대역 — 🔴 **프롬프트를 실제로 조립해 보고**, 이력에서 한 줄을 인용한다.

    ⚠ 🔴 **게이트를 흉내 내지 않는다** — 인용을 **첫 이력에서 그대로** 떠오므로 실존
    게이트를 자연히 통과하고, 그 통과는 **게이트가 판정한 것**이지 대역이 정한 것이 아니다.
    ⚠ 🔴 **프롬프트 조립을 부른다** — 안 부르면 «대역으로는 도는데 템플릿이 깨져 있다» 를
    못 잡는다(로그 129 — «대역이 우리 코드를 흉내 내면 원본을 안 잰다»).

    ⚠ 🔴 **(8/24 실측) 이 대역은 마스킹 층을 안 탄다** — `keep_sendable_history` 를 안
    부르므로 층별 수에서 `history_kept=-`(미측정)로 나온다. 🔴 그건 결함이 아니라 사실이다:
    대역은 **아무것도 전송하지 않아** 그 층이 필요 없다. 다만 그래서 **대역으로 도는 종단
    검사는 마스킹 배선을 재지 못한다** ⇒ §A 종단 검사는 `GatewayLabelSuggestProvider`
    (+ 대역 LLM)로 돈다(99 #208 — «층을 직접 부르면 배선이 안 잼힌다»의 같은 결).
    """

    async def suggest(
        self,
        *,
        guardian_ref: str,
        history: Sequence[HistoryItem],
        context: ExecutionContext,
        counts: LabelLayerCounts | None = None,
    ) -> tuple[SuggestedLabel, ...]:
        del context
        prompt = assemble_prompt(history)
        if not history or "제안:" not in prompt:
            return ()
        first = history[0]
        #: ⚠ 실 LLM 이 낼 형태 그대로 만들어 **같은 파서**를 태운다.
        line = f"comm | data | 0.5 | {first.record_id} | {first.text}"
        return parse_suggestions(line, guardian_ref=guardian_ref, counts=counts)


def build_label_gateway(provider: LLMProvider | None = None) -> LlmGateway:
    """라벨 게이트웨이 — briefing·counsel 과 같은 조립 규약.

    🔴 `trace_masking_hook` 을 **조립부가 주입한다** — 미주입이면 `LANGSMITH_TRACING=true`
    에서 게이트웨이 생성이 실패한다(09 §2-16 P1′ 기동 가드). ⚠ 그 훅이 **전송 직전 마지막
    관문**이다(불변식 3) — 이력 본문이 오탐에 걸리면 여기서 막힌다(99 #104 와 같은 자리).
    """
    from ai.llm.providers.openai_compat import (  # noqa: PLC0415 — 벤더는 조립부에서만
        build_openai_compat_provider,
    )

    #: 🔴 **의도적으로 `capture_payloads` 를 안 감싼다**(2026-08-24 · 99 #205).
    #: 04 §3.7 «제안 API 는 아무것도 저장하지 않는다» 를 **LLM 원장에도** 적용한다 —
    #: 🔴 이력 본문이 프롬프트에 실리므로 원장에 남기면 **그 정책이 무너진다**(불변식 3).
    #: ⚠ counsel(`counsel/assembly.py`)·briefing(`composition/provider.py`)은 감싼다 —
    #: **그 축은 산출물을 저장하는 축이라 다르다.**
    #: 🔴 **언제 바뀌나**: 라벨에 저장 정책이 생기면(확정 경로 집계 회차).
    #: ⚠ `LLM_CALL` 계수는 남는다(`recorder`) — **본문만** 안 남는다.
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
    "LabelLayerCounts",
    "merge_duplicate_axes",
    "parse_suggestions",
]
