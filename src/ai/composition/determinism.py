"""결정론 생성 파라미터 — composition 전 경로의 단일 정본 (불변식 8 · 99 ㊼).

소유: 박진희 (composition). 브리핑·상담 초안·plan·분류가 **같은 seed**를 쓴다.

🔴 **왜 한 곳인가 — `temperature=0.0`만으로는 재현되지 않는다.**
실 LLM 스모크(8/4)에서 `temperature=0.0`인데 같은 입력이 다른 출력을 냈다(99 ㊼). 원인은
`seed` 미전달이었다: 어댑터(`llm/providers/openai_compat.py:150`)는 `seed`가 오면 서버로
넘기게 이미 돼 있는데, `GenerationParams`에 값을 넣는 쪽이 없었다. 경로별로 각자 상수를
두면 브리핑과 초안의 재현 조건이 갈리므로 여기 하나만 둔다.

⚠ **이 값을 바꾸면 모든 LLM 출력이 바뀐다.** 재현 키라서 상수로 고정한다 — env로 흔들면
"같은 버전 세트인데 출력이 다르다"가 되어 AI_RUN 재현이 무의미해진다(불변식 8). 값 자체는
AI_RUN.generation_params에 적재되므로 과거 실행의 seed는 원장에서 읽는다.

⚠ **서버가 seed를 존중하는지는 아직 실측 전이다**(99 ㊼ 부분 해소). 여기까지는 "요청에
seed가 실린다"의 보증이고, 동일 입력 → 동일 출력의 보증은 실서버 재측정이 필요하다.
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
