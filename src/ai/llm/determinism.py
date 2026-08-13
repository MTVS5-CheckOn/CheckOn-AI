"""결정론 생성 파라미터의 공용 정본.

소유는 member-B의 `llm/`에 있고 이 모듈이 정본이다. `composition/determinism.py`는
member-A가 이 모듈의 재수출로 전환을 완료했으며, 동일 상수와 함수는 여기서만 정의된다.

🔴 **(8/13) 재현 축이 하나 줄었다 — 지금 요청에 실리는 것은 `seed` 하나다.**
`gpt-5.6-luna`가 기본값(1) 외 `temperature`를 **400으로 거부**한다(직접 curl 실측 ·
`param="temperature"` · 99 #51). 어댑터가 「값이 없으면 안 보낸다」로 흡수하고
`deterministic_params()`는 온도를 주지 않는다.

⚠ **약속한 동작과 현재 동작을 같이 적는다**(99 ㊩). 약속: *"동일 입력 → 동일 출력"*.
현재: **요청에 `seed`가 실린다**까지만 보증한다 — **서버가 `seed`를 존중하는지는 미확정**
이고(99 ㊼ ◐) 온도를 뺀 뒤로는 그것이 **유일한** 재현 축이다. 실측 전에는
`deterministic_params`라는 이름이 약속하는 것보다 실제 보증이 좁다.
"""

from __future__ import annotations

from typing import Final

from ai.contracts.execution import GenerationParams

#: 재현 seed — 전 경로 공용. 값의 유래는 확정일(2026-08-05)이며 의미는 없다(재현 키).
LLM_SEED: Final = 20260805

#: 결정론 온도. 0.0은 "가장 확률 높은 토큰만" — seed와 **함께** 있어야 재현이 선다.
#:
#: 🔴 **(8/13) v1 미사용 — `deterministic_params()`가 더는 이 값을 싣지 않는다**(99 #51).
#: **그래도 지운 게 아니라 남긴 이유** 셋:
#:   ① 이름으로 검색해 오는 사람이 **왜 사라졌는지**를 여기서 읽는다(지우면 흔적이 없다)
#:   ② `composition/determinism.py`가 이 이름을 재수출하고 `tests/ai/contract/test_llm.py`가
#:      **동일성(`is`)으로 잠근다** — 지우면 재수출 구조를 건드려야 한다
#:   ③ **벤더가 온도 고정을 다시 허용하면 복귀 지점**이다(모델은 바뀐다 — 그게 이 안건이다)
#: ⚠ 다시 쓰려면 `_build_kwargs`에 폴백 상수를 두지 말고 **호출자가 값을 주는** 형태여야 한다.
DETERMINISTIC_TEMPERATURE: Final = 0.0


def deterministic_params(*, max_tokens: int | None = None) -> GenerationParams:
    """결정론 파라미터 — 경로별로 `max_tokens`만 다르다.

    `max_tokens`는 성능 제어(과생성·지연 억제)라 경로마다 다를 수 있지만, 재현 축은
    갈리면 안 된다. 그래서 재현 축은 이 함수가 고정하고 성능 축만 인자로 받는다.

    🔴 **(8/13) 재현 축은 `seed` 하나다** — 종전 괄호는 `(temperature·seed)`였다.
    벤더가 온도를 거부해 어댑터가 흡수했고(99 #51), 애초에 **재현을 만든 것도 seed였다**
    (8/4 실측 — `temperature=0.0`인데 같은 입력이 다른 출력을 냈다).
    ⚠ **이름이 약속하는 것보다 현재 보증이 좁다** — 서버의 `seed` 존중은 미확정이다
    (99 ㊼ ◐). 실측이 끝나면 이 문단을 결과로 갱신한다.
    """
    return GenerationParams(
        #: 🔴 **temperature를 안 싣는다**(8/13). 재현 축의 정본은 `seed`다 — 8/4 실 LLM
        #:   스모크에서 `temperature=0.0`인데 같은 입력이 **다른 출력**을 냈고 원인은
        #:   `seed` 미전달이었다. **재현을 만든 것은 seed였지 temperature가 아니다.**
        #: ⚠ `gpt-5.6-luna`는 기본값(1) 외 temperature를 **400으로 거부**한다
        #:   (2026-08-13 직접 curl · `param="temperature"` · 99 #51). 어댑터가
        #:   「값이 없으면 안 보낸다」로 흡수하므로 여기서는 주지 않는다.
        #: ⚠ **불변식 8은 안 깨진다** — 바이트 동일성 대상은 결정론 경로이고 LLM
        #:   문장화는 애초에 그 대상이 아니다(불변식 1). 품질 하한은 게이트가 지킨다.
        temperature=None,
        seed=LLM_SEED,
        max_tokens=max_tokens,
    )


__all__ = ["DETERMINISTIC_TEMPERATURE", "LLM_SEED", "deterministic_params"]
