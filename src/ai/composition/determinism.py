"""결정론 생성 파라미터 — **`llm/determinism.py`의 재수출** (불변식 8 · 99 ㊼·ⓨ).

🔴 **정본은 `ai.llm.determinism`(B 소유)다.** 여기는 이름만 다시 내보낸다.
composition 코드는 계속 `ai.composition.determinism`을 부른다 — 소비처 import 경로를
바꾸지 않는 것이 재수출의 목적이다(A가 `llm/`을 직접 부르는 자리를 늘리지 않는다).

**왜 재수출인가.** #115로 B가 공용 정본을 신설하면서 같은 상수 2개 + 같은 함수 1개가
**두 모듈에 병존**했다(8/5~8/6). 값이 우연히 같았을 뿐이고, 한쪽만 고치면 **아무 테스트도
안 깨진 채** 브리핑·초안·분류와 문항 생성의 재현 조건이 갈린다 — 재현 키가 두 곳에 사는
것이 불변식 8이 가장 싫어하는 모양이다. 단일성은 `tests/ai/contract/test_llm.py`가
**동일성(`is`)** 으로 잠근다 — 값 비교(`==`)는 "각자 정의했는데 마침 같다"를 통과시킨다.

🔴 **`temperature=0.0`만으로는 재현되지 않는다 — 이 근거를 여기 남긴다.**
실 LLM 스모크(8/4)에서 `temperature=0.0`인데 같은 입력이 다른 출력을 냈다. 원인은 `seed`
미전달이었다: 어댑터(`llm/providers/openai_compat.py`)는 `seed`가 오면 서버로 넘기게 이미
돼 있는데 `GenerationParams`에 값을 넣는 쪽이 없었다. **다음 사람이 "seed 왜 있지"에서
출발하면 같은 함정을 다시 판다** — 상세 경위는 99 ㊼.

⚠ **값을 바꾸면 모든 LLM 출력이 바뀐다.** 재현 키라 상수로 고정한다 — env로 흔들면 "같은
버전 세트인데 출력이 다르다"가 되어 AI_RUN 재현이 무의미해진다. 과거 실행의 seed는
`AI_RUN.generation_params`에서 읽는다.
⚠ **서버가 seed를 존중하는지는 미확정이다**(99 ㊼ ◐ · 추적 중단). 여기까지는 "요청에
seed가 실린다"의 보증이고, 동일 입력 → 동일 출력의 보증이 아니다.

🔴 **(8/13) 그 단서가 이제 더 크게 작용한다 — 재현 축이 `seed` 하나로 줄었다.**
`gpt-5.6-luna`가 기본값 외 `temperature`를 400으로 거부해(99 #51) 어댑터가
「값이 없으면 안 보낸다」로 흡수했고, 정본 `deterministic_params()`가 온도를 주지 않는다.
**위 8/4 기록이 그 판단의 근거다** — 재현을 만든 것은 seed였지 temperature가 아니었다.

🔴 **그리고 8/13 실측으로 위 「미확정」이 닫혔다 — 서버는 seed를 존중하지 않는다.**
`gpt-5.6-luna`에 같은 seed로 8회 보내 **문장 3종**(6/1/1). seed 없이 3회는 3종이고
다른 seed는 다른 분포라 **영향은 주지만 고정은 못 한다.** `system_fingerprint`는 `None`.
⇒ **현재 재현 축은 없다.** 온도를 되살려도 이 모델에서는 400이라 얻는 것이 없다.
⚠ 이 모듈은 재수출이라 값·구조를 여기서 바꾸지 않는다. 정본은 `ai.llm.determinism`이다.
"""

from __future__ import annotations

from ai.llm.determinism import (
    DETERMINISTIC_TEMPERATURE,
    LLM_SEED,
    deterministic_params,
)

#: 🔴 재수출은 `__all__` 등재로 **명시**한다 — mypy가 이것만 명시적 재수출로 인정하고
#: (`no-implicit-reexport`), ruff의 F401(미사용 import)도 이 덕에 안 뜬다. 셋 중 하나라도
#: 빠지면 CI가 적색이거나 — 더 나쁘게 — 소비처가 조용히 `ImportError`가 된다.
__all__ = ["DETERMINISTIC_TEMPERATURE", "LLM_SEED", "deterministic_params"]
