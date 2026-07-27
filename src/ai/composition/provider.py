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
from ai.detection.brief import build_brief
from ai.llm.gateway import LlmGateway

#: 브리핑 전송 재시도 = 0 — LLM 실패 시 결정론 템플릿으로 즉시 폴백(재시도 없음).
#: 04 §2.4 예산(브리핑 45s·호출당 15s)에 재시도가 얹히면 병렬 예산이 깨진다. 09 §1-10 ①.
_NARRATOR_TRANSPORT_RETRY = 0

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

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
    """settings로 provider 선택. 기본 fake(CI·데모) — "openai_compat"이면 실 로컬 LLM.

    벤더 독립 유지: openai import는 어댑터(llm/providers/openai_compat) 안에만 있고,
    여기선 그 구현을 선택만 한다(지연 import — fake 경로는 openai를 건드리지 않는다).
    어댑터 PR(#15) develop 머지로 배선 활성(99 15).
    """
    settings = settings or get_briefing_settings()
    if settings.llm_provider == _OPENAI_COMPAT:
        from ai.llm.providers.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider()
    return FakeBriefProvider()


def build_brief_gateway(provider: LLMProvider | None = None) -> LlmGateway:
    """브리핑 문장화 게이트웨이(조립부) — narrator role로 provider를 감싼다(gateway 경유).

    모든 LLM 호출은 gateway 경유(03_coding_rules §2 · 01 §5) — 어댑터 직결을 종료한다.
    전송 재시도는 narrator=0으로 등록(재시도 값은 여기서 주입 — 하드코딩 금지). 원가 기록
    recorder는 기본 no-op(LLM_CALL DB 적재는 후속 — 99 등록).
    """
    return LlmGateway(
        {ModelRole.NARRATOR: provider or build_brief_provider()},
        transport_retry={ModelRole.NARRATOR: _NARRATOR_TRANSPORT_RETRY},
    )
