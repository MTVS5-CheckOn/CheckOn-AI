"""브리핑 LLM provider 선택 — settings로 fake↔openai_compat (store_backend 선례).

브리핑은 `contracts.llm.LLMProvider` Protocol에만 의존한다(양자 파일 무변경). 실 벤더
어댑터(openai_compat)는 별도 PR 소유 — 이 파일은 settings 스위치로 선택만 한다.
CI·테스트·데모 기본은 fake.

FakeBriefProvider는 결정론 — 프롬프트의 "신호 유형" 라벨을 읽어 `detection/brief.py`의
**기본 템플릿**을 반환한다. v2는 프롬프트에서 엔진 초안을 제거(모사 방지)했으므로 초안을
echo할 수 없다 — fake는 signal_type 기본 템플릿(수치·detail 없는 성격 문장)을 돌려준다.
따라서 fake 경로의 brief는 결정론이되 v1(초안 echo)과 달라 데모 골든이 재생성된다
(판정 필드는 무변 — API==순수엔진은 brief를 뺀 전수 대조로 유지).
"""

from __future__ import annotations

import re
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from ai.contracts.detection import DISPLAY_LABELS, SignalType
from ai.contracts.execution import ExecutionContext
from ai.contracts.llm import (
    CallOutcome,
    LLMProvider,
    LLMRequest,
    LLMResult,
    ModelRole,
    TokenUsage,
)
from ai.db.repositories.llm_payload import capture_payloads
from ai.db.repositories.run_store import default_llm_call_collector
from ai.detection.brief import build_brief
from ai.llm.gateway import LlmCallRecorder, LlmGateway
from ai.runtime.env_files import ENV_FILES
from ai.runtime.trace_masking import RedactionTripwireTraceHook

#: 브리핑 전송 재시도 = 0 — LLM 실패 시 결정론 템플릿으로 즉시 폴백(재시도 없음).
#: 04 §2.4 예산(브리핑 45s·호출당 15s)에 재시도가 얹히면 병렬 예산이 깨진다. 09 §1-10 ①.
_NARRATOR_TRANSPORT_RETRY = 0

#: ━━ 🔴 **detect 축 예산 관계의 단일 정본** (99 #124) ━━
#:
#: counsel 이 콜당 상한을 45 → **90s** 로 올렸을 때(99 ㉪) **이 축도 같이 움직였는데
#: 아무도 안 봤다** — `OPENAI_TIMEOUT_S` 는 provider 전역이라 브리핑도 그 값을 탄다.
#: 실측(8/20): 예산 검사는 **호출 전에만** 돌므로(`briefing.py` — 시작한 호출을 안 끊는다)
#: `t=44.9s` 에 시작한 콜이 90s 를 쓰면 **총 ~135s** 다 ⇒ 04 의 BE 60s 를 끊어 먹는다.
#: 🔴 **그때가 정확히 장애 때다** — 폴백 문구조차 BE 에 못 간다.

#: 브리핑 **콜당** LLM 상한(초) — 🔴 provider 전역(`OPENAI_TIMEOUT_S`)을 **안 쓴다.**
#:
#: ⚠ **왜 전역과 다른 값을 쓰나** — counsel 은 4문단 글이고 브리핑은 **문장 하나**다
#: (실측: narrator 응답 토큰 중앙 **28**). 같은 상한을 쓸 이유가 없고, 쓰면 예산이 깨진다.
#: 🔴 **근거는 원장 실측이다**(2026-08-20 · `local_data/*llm_smoke_raw.json` s1 · **n=147** ·
#: 7회차): 콜당 min 0.19s · p50 **0.97s** · p90 1.56s · p95 2.02s · **max 5.76s**.
#: ⇒ 15s 는 실측 max 의 **2.6배**다.
#: ⚠ **표본 절단을 명시적으로 배제했다**(99 #110 의 순환): 이 회차들은 상한 15s 시절인데
#: **15s 초과 0/147** 이고 `llm_failed` 21건은 지연 p50 **0.22s**(벤더 미도달 — 즉시 실패)라
#: **타임아웃으로 잘린 표본이 아니다.** 예산 소진(`budget_exhausted`) 은 **0건**이다.
BRIEFING_CALL_TIMEOUT_S: int = 15

#: 브리핑 문장화 **총** 예산(초) — 04 §2.4 `/detect` 정본. `api/routers/detect.py` 가 쓴다.
BRIEFING_BUDGET_S: float = 45.0

#: `/detect` 의 BE read timeout(초) — 04 §2.4 정본.
DETECT_HTTP_TIMEOUT_S: int = 60

#: 🔴 **관계: `총 예산 + 콜당 상한 ≤ BE 타임아웃`** (45 + 15 = 60 ≤ 60).
#: 예산 검사가 **시작만 막으므로** 마지막 콜은 예산 직전에 시작해 상한만큼 더 쓴다.
#: 좌변이 `총 예산`만이면 그 꼬리를 빼먹는다 — 그게 이 결함의 형태였다.
#: ⚠ **경계에 붙어 있다**(여유 0) — 그리고 `deadline` 은 **감지·조립 뒤에** 시작하므로
#: 실제 HTTP 총 시간은 여기에 **감지 시간이 더 얹힌다.** 그건 콜당 90s 와 무관하게
#: 종전 설계부터 그랬고, 04 의 60s 를 올리는 것은 승우님 통보 축이라 이 회차가 안 건드렸다
#: (99 #124 에 등재).

_FAKE = "fake"
_OPENAI_COMPAT = "openai_compat"
_SIGNAL_LABEL_RE = re.compile(r"신호 유형:\s*(.+)")
#: display_label → SignalType 역인덱스(라벨은 유일 — Signal.validate_display_label 보장).
_LABEL_TO_TYPE: dict[str, SignalType] = {
    label: signal_type for signal_type, label in DISPLAY_LABELS.items()
}
#: fake가 라벨을 못 읽은 방어 문장(결정론·비공백·게이트 통과 — 수치·기호 없음).
_FAKE_DEFAULT = "확인이 필요한 학습 신호가 있어요."


class BriefingSettings(BaseSettings):
    """브리핑 provider 설정 — env `LLM_PROVIDER`로 주입(기본 fake)."""

    model_config = SettingsConfigDict(env_file=ENV_FILES, extra="ignore")

    llm_provider: str = _FAKE
    """"fake"(기본·CI·데모) | "openai_compat"(실 벤더 — 어댑터 PR 머지 후 활성)."""


@lru_cache
def get_briefing_settings() -> BriefingSettings:
    return BriefingSettings()


class FakeBriefProvider:
    """결정론 fake — 프롬프트의 '신호 유형' 라벨로 엔진 기본 템플릿을 반환한다(v2).

    v2 프롬프트엔 초안이 없으므로 echo 대신 signal_type 기본 템플릿(수치·detail 없음)을
    돌려준다. 숫자·기호·금칙어·토큰이 없어 게이트를 통과하며, CI·데모를 결정론화한다.
    """

    @property
    def name(self) -> str:
        return "fake-brief"

    async def complete(
        self, request: LLMRequest, context: ExecutionContext
    ) -> LLMResult:
        match = _SIGNAL_LABEL_RE.search(request.prompt)
        signal_type = _LABEL_TO_TYPE.get(match.group(1).strip()) if match else None
        text = build_brief(signal_type).text if signal_type is not None else _FAKE_DEFAULT
        return LLMResult(
            outcome=CallOutcome.OK,
            text=text,
            provider=self.name,
            model="template",
            usage=TokenUsage(tokens_in=0, tokens_out=0, cost_usd=0.0),
            latency_ms=0,
        )


def build_brief_provider(settings: BriefingSettings | None = None) -> LLMProvider:
    """settings로 provider 선택. 기본 fake(CI·데모) — "openai_compat"이면 OpenAI API.

    벤더 독립 유지: openai import는 어댑터(llm/providers/openai_compat) 안에만 있고,
    여기선 그 구현을 선택만 한다(지연 import — fake 경로는 openai를 건드리지 않는다).
    어댑터 PR(#15) develop 머지로 배선 활성(99 15).
    """
    settings = settings or get_briefing_settings()
    if settings.llm_provider == _OPENAI_COMPAT:
        from ai.llm.providers.openai_compat import (  # noqa: PLC0415 — 벤더 지연 import
            build_openai_compat_provider,
            get_llm_settings,
        )

        #: 🔴 **브리핑 전용 상한을 주입한다** — 전역 `OPENAI_TIMEOUT_S`(counsel 기준 90s)를
        #: 그대로 타면 총 예산 45s + 90s = 135s 로 04 의 BE 60s 를 넘는다(99 #124).
        #: ⚠ `model_copy` 로 **타임아웃만** 바꾼다 — 키·URL·모델을 이 파일이 다시 읽거나
        #: 옮겨 적지 않는다(값을 만지지 않는 것이 유출 표면을 안 넓힌다 · 99 #122).
        #: ⚠ 선례: `problem_generation/provider.py` 도 verifier 에 자기 `OpenAiSettings` 를 준다.
        narrator = get_llm_settings().model_copy(
            update={"openai_timeout_s": float(BRIEFING_CALL_TIMEOUT_S)}
        )
        return build_openai_compat_provider(settings=narrator)
    return FakeBriefProvider()


def build_brief_gateway(
    provider: LLMProvider | None = None,
    *,
    recorder: LlmCallRecorder | None = None,
) -> LlmGateway:
    """브리핑 문장화 게이트웨이(조립부) — narrator role로 provider를 감싼다(gateway 경유).

    모든 LLM 호출은 gateway 경유(03_coding_rules §2 · 01 §5) — 어댑터 직결을 종료한다.
    전송 재시도는 narrator=0으로 등록(재시도 값은 여기서 주입 — 하드코딩 금지).

    **`recorder`는 기본이 공용 수집기다**(8/5 · 99 ㊻ⓐ 해소). 종전에는 기본 no-op이라
    실 LLM 스모크 1회의 호출 34건·토큰 19,814가 전량 폐기됐다 — "조립부가 recorder를
    넘기는 것을 잊었다"가 사고의 형태였으므로 **기본값을 뒤집었다.** 수집분은 소비자가
    `record_calls`로 영속한다(수집 후 영속 — `db/repositories/run_store.py`).

    ⚠ **㉒-a 추적 가드를 여기 걸지 않는다** — 실측상 briefing은 span 0건이라 위험 표면이
    아니다(`part_a/11` §3: 그래프도 Runnable도 아니고 `openai_compat`이 `wrap_openai`를
    쓰지 않아 briefing만 돌리면 LangSmith 프로젝트조차 생성되지 않았다). 가드는 LangGraph
    워커 조립부(counsel_pack·mapping_probe)에만 있다.

    `trace_masking_hook`도 **조립부가 주입한다** — `transport_retry`와 같은 규약이다
    (01 §5 "게이트웨이는 값을 모른다"). 미주입이면 `LANGSMITH_TRACING=true`에서
    게이트웨이 생성이 실패한다(09 §2-16 P1′ 기동 가드).
    """
    return LlmGateway(
        # `capture_payloads`가 provider를 감싸 **전송 본문**을 포착한다(99 ㉝) — 게이트웨이가
        # 마스킹 훅을 통과시킨 요청을 그대로 provider에 넘기므로 이 자리에서 요청·응답을
        # 둘 다 볼 수 있고 `llm/gateway.py`를 건드리지 않는다. `name`은 위임된다.
        {ModelRole.NARRATOR: capture_payloads(provider or build_brief_provider())},
        recorder=recorder or default_llm_call_collector(),
        transport_retry={ModelRole.NARRATOR: _NARRATOR_TRANSPORT_RETRY},
        trace_masking_hook=RedactionTripwireTraceHook(),
    )
