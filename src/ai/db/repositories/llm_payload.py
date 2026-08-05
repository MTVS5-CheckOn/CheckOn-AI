"""LLM 전송 본문 포착 — `LLM_PAYLOAD` (불변식 3·8 · 99 ㉝).

사양: `docs/06_erd.md` LLM_PAYLOAD · `docs/policies/masking_redaction.md` §3 훅 위치 표.
소유: 박진희 (db.repositories). ㊻(#92)의 **수집 후 영속** 구조에 본문을 얹는다.

㊻이 남긴 것은 **누가·언제·얼마**(role·prompt_id@version·tokens·latency·outcome)다.
비어 있던 것은 **무엇을 보냈는지**다 — 같은 `prompt_id@version`에서도 조립 결과는 달라진다
(`emphasis`·`gate_feedback`·`refine_instruction`이 컨텍스트 파생이라 `PROMPT_VERSION`이
움직이지 않는다 — `counsel/prompt.py:assemble_prompt`가 그렇게 규정한다). 그 차이는
본문에만 남으므로 본문이 없으면 재현이 반쪽이다.

━━ 🔴 gateway 무접촉 — provider 래핑으로 간다 ━━

`gateway.complete`가 `trace_masking_hook.mask()`로 만든 **`masked_request`를 그대로**
`_complete_once(provider, masked_request, context)`에 넘긴다(B의 Protocol docstring이
전달을 보장한다). ⇒ `LLMProvider`를 감싸면 **전송된 요청과 받은 응답을 둘 다** 볼 수 있고
`llm/gateway.py` diff가 0줄이다. `LlmCallRecord`에 본문 필드를 추가하는 길은 택하지
않았다 — 그 레코드는 "비민감 관측 메타데이터"이고 성격을 깨면 이를 소비하는 모든 곳이
민감 데이터를 다루게 된다.

━━ 요청·응답의 마스킹 취급이 다르다(같게 만들면 하나가 틀린다) ━━

**요청** — 이미 fail-closed 게이트를 **두 번** 통과했다: ① 조립부의 `redact()`
② `RedactionTripwireTraceHook`(`redact(prompt).findings or uncertain`이면 전송 차단).
⇒ provider에 도달한 프롬프트는 `redact` 결과가 완전히 깨끗함이 보증된다. 그래서 저장 시
**변형하지 않고 전송분을 그대로** 넣고, 저장 직전 검사는 **검증**으로만 쓴다.

🔴 **왜 변형하면 안 되는가 — `redact()`는 멱등이 아니다.** 실측(8/6)::

    "김서연 학생의 어머니 010-…"  →1차→  "⟪이름1⟫ 학생의 어머니 ⟪연락처1⟫로 …"
                                  →2차→  "⟪이름1⟫ ⟪이름1⟫ 어머니 ⟪연락처1⟫로 …"

2차가 `학생의`를 인명으로 잡는다. 2차 산출을 저장하면 **보낸 적 없는 문면이 원장에
남아** 재현이 깨진다. (위 예시는 트립와이어가 전송 자체를 막으므로 실제로는 저장 대상이
되지 않지만, "저장본 = 전송분"을 규약으로 못 박는 근거다.)

**응답** — 어떤 게이트도 통과하지 않았다. 프롬프트가 마스킹됐으니 정상 경로에선 마스킹
토큰만 나오지만 **환각으로 실명을 만들 수 있다**. ⇒ 저장 전 `redact()`로 **변형**한다
(masking_redaction의 "오탐 감수·미탐 최소" · 대상에 미성년자 정보 포함).
⚠ 컬럼명이 `response_raw`인데 마스킹 통과본을 넣는다 — `db/models.py`가 양자 승인
파일이라 이름을 바꾸지 않고 규약을 주석·§3 표·ERD에 명시했다.

⚠ **마스킹은 포착 시점에 한다**(저장 시점이 아니다). 원문 응답이 영속 버퍼에 앉아 있는
구간을 없애기 위해서다 — 버퍼에는 마스킹 통과본만 들어간다(§4 "도구 반환값도 마스킹
통과분만"과 같은 규율). 저장 직전 훅은 그 통과본을 **검증**한다.

⚠ **저장 거부는 LLM 호출을 죽이지 않는다**(fail-open) — ㊻ `run_store`와 같은 철학이다.
거부·드롭은 구조화 로그 + 카운터로 남긴다(조용한 누락 금지).
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Final, NamedTuple
from uuid import UUID

from ai.contracts.execution import ExecutionContext
from ai.contracts.llm import LLMProvider, LLMRequest, LLMResult
from ai.db.models import LlmPayload
from ai.db.settings import DbSettings, get_db_settings
from ai.runtime.redaction import redact

logger = logging.getLogger(__name__)

#: 저장 거부 사유 — 로그·카운터용 어휘(닫힌 집합).
REFUSED_REQUEST_RESIDUE: Final = "request_residue"
REFUSED_RESPONSE_UNCERTAIN: Final = "response_uncertain"


class CapturedBody(NamedTuple):
    """포착된 본문 1건 — **마스킹 통과본만** 담는다(원문은 여기 오지 않는다).

    `oversized`면 본문을 버렸다는 표시다(메타 기록은 그대로 남는다 — 아래 참조).
    """

    request_masked: str
    response_masked: str
    response_uncertain: bool
    oversized: bool = False


#: 상한 초과 표시 — 본문을 버리고 사실만 남긴다.
_OVERSIZED: Final = CapturedBody("", "", False, oversized=True)

#: 🔴 **태스크 로컬** 인계 슬롯. provider 래퍼가 넣고 `LlmCallCollector`가 꺼낸다.
#:
#: 왜 ContextVar인가 — 래퍼는 `provider.complete` 끝에서, 수집기는 그 직후
#: `gateway._record`에서 돈다. 둘 사이에 `await`가 없어 전역 변수로도 "지금은" 안전하지만,
#: 브리핑은 신호별 호출을 **동시에 3개**까지 돌린다(`detect.py` 세마포어). `asyncio` 태스크는
#: 생성 시 컨텍스트를 복사하므로 ContextVar면 격리가 **구조적으로** 보장된다 —
#: "await가 없으니 괜찮다"는 논증에 의존하지 않는다.
_CAPTURED: ContextVar[CapturedBody | None] = ContextVar(
    "llm_payload_captured", default=None
)


class CollectedPayload(NamedTuple):
    """LLM_CALL 행에 매달릴 본문 — `call_id`는 수집기가 발급한 그 id다."""

    call_id: UUID
    request_masked: str
    response_masked: str
    response_uncertain: bool


def max_payload_chars(settings: DbSettings | None = None) -> int:
    """본문 1건당 문자 상한 — 초과분은 **버린다**(절단하지 않는다).

    🔴 **절단하면 재현이 깨진다.** 잘린 프롬프트는 "재현 가능해 보이지만 실제로는 아닌"
    기록이 되어 조용히 틀린다 — 행이 아예 없는 편이 낫다(부재는 눈에 보인다).
    """
    return (settings or get_db_settings()).llm_payload_max_chars


def capture_payloads(
    provider: LLMProvider, *, settings: DbSettings | None = None
) -> LLMProvider:
    """provider를 본문 포착 래퍼로 감싼다 — 조립부가 부른다.

    🔴 **이중 래핑을 하지 않는다.** 두 겹이면 바깥 래퍼가 인계 슬롯을 나중에 덮어써
    안쪽 판정(길이 상한 등)이 조용히 무시된다. 이미 감싼 provider는 그대로 돌려준다 —
    합성 루트와 조립부가 각자 감싸는 조합이 실제로 생길 수 있다.
    """
    if isinstance(provider, PayloadCapturingProvider):
        return provider
    return PayloadCapturingProvider(provider, settings=settings)


class PayloadCapturingProvider:
    """`LLMProvider` 래퍼 — 전송된 요청과 받은 응답을 태스크 로컬 슬롯에 남긴다.

    `name`을 위임하는 것이 계약이다 — 게이트웨이의 `_validate_provider_assignment`가
    provider 이름으로 generator↔verifier 동일 패밀리를 막는데, 래퍼가 자기 이름을 내면
    그 검사가 무력해진다.

    **호출 실패도 포착한다.** 응답이 없어도 "무엇을 보냈는지"는 남아야 한다 — 타임아웃·
    컨텍스트 한도 초과는 프롬프트를 봐야 진단된다. 그때 `response_masked`는 빈 문자열이고
    "응답 없음"과 "빈 응답"의 구분은 `LLM_CALL.outcome`이 한다(㊻의 tokens 0 규약과 동일).
    """

    def __init__(
        self, inner: LLMProvider, *, settings: DbSettings | None = None
    ) -> None:
        self._inner = inner
        self._max_chars = max_payload_chars(settings)

    @property
    def name(self) -> str:
        return self._inner.name

    async def complete(
        self, request: LLMRequest, context: ExecutionContext
    ) -> LLMResult:
        try:
            result = await self._inner.complete(request, context)
        except Exception:
            _CAPTURED.set(self._body(request.prompt, None))
            raise
        _CAPTURED.set(self._body(request.prompt, result.text))
        return result

    def _body(self, prompt: str, response: str | None) -> CapturedBody:
        """포착 시점 마스킹 — 원문 응답이 영속 버퍼에 들어가지 않게 여기서 가린다."""
        if len(prompt) > self._max_chars or len(response or "") > self._max_chars:
            return _OVERSIZED
        if not response:
            return CapturedBody(prompt, "", False)
        masked = redact(response)
        return CapturedBody(prompt, masked.masked_text, masked.uncertain)


def take_captured() -> CapturedBody | None:
    """슬롯을 **비우고** 꺼낸다 — 다음 호출이 앞 호출의 본문을 물려받지 않게."""
    body = _CAPTURED.get()
    if body is not None:
        _CAPTURED.set(None)
    return body


def reset_captured() -> None:
    """테스트 격리용 — 슬롯을 비운다."""
    _CAPTURED.set(None)


def payload_refusal_reason(payload: CollectedPayload) -> str | None:
    """**저장 직전 훅**(masking_redaction §3) — 거부 사유 또는 None.

    요청과 응답의 판정이 다르다(모듈 docstring 참조):
      · 요청 — `findings`가 하나라도 있으면 거부다. 트립와이어가 이미 완전 무흔적을
        보증하므로 여기서 걸리는 것은 **그 게이트를 우회한 경로가 있다는 뜻**이다
      · 응답 — 마스킹 통과본이라 `findings`는 정상이다. `uncertain`(`⟪확인필요⟫ 잔존)만
        거부한다 — "가렸지만 확신 없음"을 원장에 남기지 않는다
    """
    residue = redact(payload.request_masked)
    if residue.findings or residue.uncertain:
        return REFUSED_REQUEST_RESIDUE
    if payload.response_uncertain:
        return REFUSED_RESPONSE_UNCERTAIN
    return None


def payload_orm(payload: CollectedPayload) -> LlmPayload:
    """CollectedPayload → LLM_PAYLOAD 행.

    ⚠ `response_raw` 컬럼에 **마스킹 통과본**을 넣는다(모듈 docstring · §3 표).
    """
    return LlmPayload(
        call_id=payload.call_id,
        request_masked=payload.request_masked,
        response_raw=payload.response_masked,
    )


__all__ = [
    "REFUSED_REQUEST_RESIDUE",
    "REFUSED_RESPONSE_UNCERTAIN",
    "CapturedBody",
    "CollectedPayload",
    "PayloadCapturingProvider",
    "capture_payloads",
    "max_payload_chars",
    "payload_orm",
    "payload_refusal_reason",
    "reset_captured",
    "take_captured",
]
