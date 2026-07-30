"""외부 트레이싱 활성 판정 — **langsmith import는 이 파일에서만**.

`runtime/`엔 `redaction.py`(개인정보 경로)가 산다. 03 §1b "개인정보 경로엔 외부 전송
라이브러리 금지"의 정신대로, 판정 술어 하나를 쓰려고 langsmith 클라이언트를 패키지 전역에
퍼뜨리지 않는다 — `llm/providers/` 안에서만 openai SDK를 허용한 선례와 같은 결이다.
이 격리는 `tests/ai/failure/test_tracing_synonym_guard.py`의 AST 계약이 강제한다.

**왜 라이브러리 판정을 쓰는가(env 목록을 손으로 복제하지 않는가).**
`tracing_is_enabled()`는 env 4개(`{LANGSMITH,LANGCHAIN}_{TRACING,TRACING_V2}`)뿐 아니라
컨텍스트 변수·진행 중 run tree·전역 fallback까지 본다. 우리가 env 이름만 베끼면 그
경로들을 놓치고, 라이브러리가 이름을 늘리면 조용히 뚫린다 — 03 §1b 바퀴 재발명 금지.
"""

from __future__ import annotations

import os
from typing import Final

from langsmith.utils import get_env_var, tracing_is_enabled

#: `tracing_is_enabled()`가 읽는 env 전수 — **에러 메시지에 어떤 이름이 켜졌는지 알리는 용도**
#: 이고 판정의 근거가 아니다(판정은 라이브러리가 한다). `utils.py:141` × `:423`의 곱.
TRACING_ENV_SYNONYMS: Final = (
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING",
)


def external_tracing_active() -> bool:
    """외부 트레이싱(LangSmith)이 활성인가 — **라이브러리 판정이 정본**.

    `tracing_is_enabled()`는 `True|False|"local"`을 낼 수 있다. `"local"`도 수집이 도는
    상태이므로 활성으로 본다(fail-closed — 애매하면 막는 쪽).
    """
    # env는 lru_cache된다(`utils.get_env_var`) — 기동 시점의 실제 값으로 판정하려면 비운다.
    get_env_var.cache_clear()  # type: ignore[attr-defined]  # lru_cache 래핑 — 스텁 미반영
    return tracing_is_enabled() is not False


def active_tracing_env_names() -> tuple[str, ...]:
    """켜져 있는 env **이름**만 돌려준다 — ⚠ **값은 절대 담지 않는다.**

    가드가 막으려던 유출을 에러 메시지가 하면 안 된다(근처에 `LANGSMITH_API_KEY`가 있다).
    이름만으로도 "내가 뭘 켰는지"를 즉시 알 수 있어 디버깅에 충분하다.
    """
    return tuple(name for name in TRACING_ENV_SYNONYMS if os.environ.get(name))


def require_tracing_disabled(worker_kind: str) -> None:
    """추적이 활성이면 워커 기동을 거부한다(fail-closed · 불변식 3).

    P2(LangSmith 클라이언트 은닉·체크포인터 serde — 99 D ⑳)가 구현되기 전까지, LangGraph
    워커는 마스킹 전 state를 노드 경계로 내보낸다(`part_a/11` §1.1 — `emphasis_points`에
    근거 라벨·수치·`record_id` 문면). 그래서 "추적이 켜져 있으면 아예 안 돈다".

    briefing 경로에는 걸지 않는다 — 실측상 span 0건이라 위험 표면이 아니다(`11` §3).
    """
    if not external_tracing_active():
        return
    detected = active_tracing_env_names()
    names = ", ".join(detected) if detected else "(env 아님 — 컨텍스트·전역 설정)"
    raise ValueError(
        f"외부 트레이싱이 활성인데 P2 은닉이 구현되지 않았다 — {worker_kind} 워커를 "
        "기동할 수 없다. 마스킹 전 state(emphasis_points의 근거 라벨·수치·record_id)가 "
        "외부 SaaS로 나간다(불변식 3). "
        f"감지된 env: {names}. "
        "추적을 끄거나(위 이름 전부 미설정) P2 은닉을 먼저 구현하라. "
        "docs/part_a/11_langsmith_trace_probe.md · 99 D ⑳."
    )


__all__ = [
    "TRACING_ENV_SYNONYMS",
    "active_tracing_env_names",
    "external_tracing_active",
    "require_tracing_disabled",
]
