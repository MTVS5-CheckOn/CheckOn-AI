"""호출 자리별 콜당 상한 표 — 값은 `call_timeouts.yaml`이 정본이다.

🔴 **왜 만들었나.** 콜당 상한이 자리마다 흩어져 있었다. 전역 `OPENAI_TIMEOUT_S`(운영 90초)
하나로는 성격이 다른 호출을 같이 묶을 수 없어서, 필요한 자리마다 **국소 주입**이 생겼다 —
`composition/provider.py`의 `BRIEFING_CALL_TIMEOUT_S`(브리핑 전용 15초)가 그것이고,
`problem_generation/provider.py`가 verifier 전용 설정을 따로 든 것도 같은 형태다.
⇒ A(박진희)가 2026-08-20에 «국소 주입이 늘어나면 어느 자리가 어떤 상한을 쓰는지
흩어진다 — 언젠가는 한 곳에 모이는 게 맞다»고 제기했고, 그 한 곳이 여기다.

🔴 **축은 `role`이 아니라 `prompt_id`다.** A의 요청 문면은 「role별 상한」이었는데
**role로는 못 가른다** — counsel의 plan과 write가 둘 다 `ModelRole.COUNSELOR`이고
(`composition/counsel/provider.py`가 이미 그렇게 적어 뒀다) 그 둘의 실측 지연은 5배
차이다(plan p95 5.4s · write p95 26.8s). role 축으로 표를 만들면 **가르려던 두 자리가
한 칸에 들어간다.** `prompt_id`는 `LLMRequest`에 이미 있어 계약 변경도 없다.

⚠ **표가 비면 오늘과 같다.** 없는 `prompt_id`는 `None`이고, 그때 게이트웨이는 상한을
씌우지 않아 provider의 전역 상한이 그대로 간다. 지금 `call_timeouts.yaml`은 **비어 있다** —
값은 실측 p95에서 나오는데 pg·classify 자리는 콜당 실측이 없고, counsel·detect 자리의
값은 A 소유이기 때문이다(§2-29).
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml  # type: ignore[import-untyped]  # PyYAML은 공식 타입 정보를 제공하지 않는다.
from pydantic import BaseModel, ConfigDict, Field, ValidationError

_DEFAULT_TABLE_PATH = Path(__file__).resolve().parent / "call_timeouts.yaml"


class CallTimeoutLoadError(ValueError):
    """콜당 상한 표를 안전하게 읽을 수 없음."""


class CallTimeoutTable(BaseModel):
    """`prompt_id` → 콜당 상한(초). 등록되지 않은 자리는 전역 상한을 따른다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["llm-call-timeouts.v1"] = "llm-call-timeouts.v1"
    call_timeouts: dict[str, float] = Field(default_factory=dict)
    """🔴 값은 **양수**여야 한다 — 0·음수는 「즉시 만료」라 뜻이 안 되는 값이다."""

    def model_post_init(self, _context: object) -> None:
        invalid = sorted(
            prompt_id
            for prompt_id, seconds in self.call_timeouts.items()
            if not seconds > 0
        )
        if invalid:
            raise ValueError(f"콜당 상한은 0보다 커야 한다: {', '.join(invalid)}")

    def get(self, prompt_id: str) -> float | None:
        """등록된 상한 또는 `None`.

        🔴 **모르는 `prompt_id`에 예외를 던지지 않는다.** 이 표는 *선택적 조임*이지
        프롬프트 정본이 아니다(정본은 `prompts/registry.yaml`이고, counsel·detect는
        거기에도 없다). 여기서 던지면 **표에 없는 자리가 전부 죽는다** — 조이려고 만든
        장치가 안 조인 자리를 깨뜨리는 셈이다.
        """

        return self.call_timeouts.get(prompt_id)


@lru_cache
def load_call_timeouts(table_path: Path = _DEFAULT_TABLE_PATH) -> CallTimeoutTable:
    """YAML 표를 읽고 스키마를 검증한다 — 게이트웨이 생성마다 다시 읽지 않는다."""

    try:
        raw: object = yaml.safe_load(table_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise CallTimeoutLoadError(
            f"콜당 상한 표를 읽을 수 없다: {table_path}"
        ) from error
    except yaml.YAMLError as error:
        raise CallTimeoutLoadError(f"콜당 상한 표 YAML 오류: {error}") from error

    try:
        return CallTimeoutTable.model_validate(raw)
    except ValidationError as error:
        raise CallTimeoutLoadError(f"콜당 상한 표 스키마 오류: {error}") from error
