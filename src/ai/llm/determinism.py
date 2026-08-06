"""결정론 생성 파라미터의 공용 정본.

소유는 member-B의 `llm/`에 있고 이 모듈이 정본이다. `composition/determinism.py`는
member-A가 이 모듈의 재수출로 전환을 완료했으며, 동일 상수와 함수는 여기서만 정의된다.
"""

from __future__ import annotations

from typing import Final

from ai.contracts.execution import GenerationParams

#: 재현 seed — 전 경로 공용. 값의 유래는 확정일(2026-08-05)이며 의미는 없다(재현 키).
LLM_SEED: Final = 20260805

#: 결정론 온도. 0.0은 "가장 확률 높은 토큰만" — seed와 **함께** 있어야 재현이 선다.
DETERMINISTIC_TEMPERATURE: Final = 0.0


def deterministic_params(*, max_tokens: int | None = None) -> GenerationParams:
    """결정론 파라미터 — 경로별로 `max_tokens`만 다르다.

    `max_tokens`는 성능 제어(과생성·지연 억제)라 경로마다 다를 수 있지만, 재현 축
    (temperature·seed)은 갈리면 안 된다. 그래서 재현 축은 이 함수가 고정하고 성능 축만
    인자로 받는다.
    """
    return GenerationParams(
        temperature=DETERMINISTIC_TEMPERATURE,
        seed=LLM_SEED,
        max_tokens=max_tokens,
    )


__all__ = ["DETERMINISTIC_TEMPERATURE", "LLM_SEED", "deterministic_params"]
