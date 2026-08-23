"""라벨 제안 생성기 자리 — 🔴 **v1 에는 생성기가 없다**(프롬프트 미작성).

⚠ 🔴 **「없다」를 「빈 배열」로 돌려주지 않는다.** 04 §3.7 은 `suggestions=[]` 를
**«인용 실존 게이트가 전량 드롭했다»** 로 정의한다 — 생성기가 없어서 비는 것과
**증거상 같아 보이면** 강사가 «이 학부모는 제안할 게 없구나» 로 읽는다.
⇒ 생성기가 없는 경로는 **503 으로 정직하게 실패**한다(`LlmUpstreamDown`).

🔴 **다음 회차 몫**: 프롬프트 + 실 LLM 콜당 실측 → `llm/call_timeouts.yaml`(99 #191).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Protocol

from pydantic_settings import BaseSettings, SettingsConfigDict

from ai.contracts.labels import HistoryItem, SuggestedLabel
from ai.runtime.env_files import ENV_FILES
from ai.runtime.errors import LlmUpstreamDown

#: 생성기 미구현 사유 — `error_codes.md` 축의 `detail.reason`.
GENERATOR_NOT_IMPLEMENTED: Final = "label_generator_not_implemented"


class LabelSuggestProvider(Protocol):
    """제안 생성 — 순수 계약. 구현이 LLM 을 쓰든 안 쓰든 라우터는 모른다."""

    async def suggest(
        self, *, guardian_ref: str, history: Sequence[HistoryItem]
    ) -> tuple[SuggestedLabel, ...]: ...


class MissingLabelSuggestProvider:
    """🔴 **생성기가 없다는 사실을 그대로 낸다** — 빈 배열로 위장하지 않는다."""

    async def suggest(
        self, *, guardian_ref: str, history: Sequence[HistoryItem]
    ) -> tuple[SuggestedLabel, ...]:
        del guardian_ref, history
        raise LlmUpstreamDown(
            "라벨 제안 생성기 미구현",
            {
                "reason": GENERATOR_NOT_IMPLEMENTED,
                "detail": "프롬프트·생성 경로가 아직 없다 — 99 #191",
            },
        )


class LabelSettings(BaseSettings):
    """`LLM_PROVIDER` 하나만 본다 — 다른 축과 같은 규약."""

    model_config = SettingsConfigDict(env_file=ENV_FILES, extra="ignore")

    llm_provider: str = "fake"


def build_label_suggest_provider(
    settings: LabelSettings | None = None,
) -> LabelSuggestProvider:
    """조립부 — 🔴 **지금은 어느 값이든 「미구현」이다.**

    ⚠ `settings` 를 받아 두는 것은 **다음 회차에 실 provider 가 붙을 자리**를 지금
    만들어 두기 위해서다. 🔴 값에 따라 다르게 행동하는 **분기를 지금 만들지 않았다** —
    죽은 분기가 생기고, 그건 이 저장소가 `openai_disable_thinking` 에서 한 번 겪었다.
    """
    del settings
    return MissingLabelSuggestProvider()


__all__ = [
    "GENERATOR_NOT_IMPLEMENTED",
    "LabelSettings",
    "LabelSuggestProvider",
    "MissingLabelSuggestProvider",
    "build_label_suggest_provider",
]
