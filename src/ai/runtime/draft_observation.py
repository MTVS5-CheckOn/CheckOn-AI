"""게이트를 **통과한** 초안 본문의 관측 — 🔴 **차단하지 않는다. 세기만 한다** (99 #80·#81).

━━ 왜 「세기만」인가 (결정 2026-08-19) ━━

🔴 **초안 본문에 `redact()`를 걸어 차단·변형하지 않는다.**

    HITL이 있다             강사 승인 뒤 발송이라 오탐 1건의 비용이 미탐 1건보다 크다
    `redact()` 오탐이 실측돼 있다  `가정에서도`·`정리하는`가 `uncertain`을 낸다(99 #77)
    ⇒ 차단하면 정상 초안이 죽고, 변형하면 강사가 안 쓴 문장이 나간다

**대신 계수만 남긴다.** 99 #28(출력측 redaction)의 여는 조건이 *"출력측 실명 실측 1건"* 인데
지금 **0건**이라, 관측이 없으면 **그 조건이 영원히 안 채워진다.**

━━ 🔴 그리고 이것이 B군 게이트를 여는 **유일한 길**이다 ━━

완충 사전 B군은 지금 **프롬프트 주입까지만** 섰다(99 #79). 나머지 절반인 **게이트 검출**을
넣으려면 *"그 어휘가 실제로 얼마나 나오나"* 를 알아야 재생성 폭증 여부를 판단할 수 있는데,
🔴 **그 빈도를 잴 표본이 저장소에 구조적으로 없다**(99 #80) — 실 LLM 산출을 레포로 옮기지
않는 규율 때문이고 **그 규율은 옳다**(마스킹·실명 위험). ⇒ **기다리는 대신 만든다.**

⚠ **여기서 나오는 숫자가 다음 회차의 판정 재료다.** 이 사실을 안 적으면 다음 사람이
*"로그만 찍고 아무도 안 본다"* 로 지운다.

━━ 읽는 자리 ━━

⚠ `runtime/metrics.py`는 **아직 없다**(99 ⓔ) ⇒ **지금은 로그가 읽는 자리다.**
로그 59 규약 — *"관측 장치를 만들 때 읽는 자리를 같이 만들지 않으면 없는 것과 같다"*.
메트릭 축이 서면 이 함수가 그리로 옮겨 간다.

━━ 🔴 본문을 로그에 싣지 않는다 (불변식 3) ━━

    싣는다   길이 · execution_id · tenant_id · **적중한 사전 어휘**
    안 싣는다 본문 · 주변 문장 · 인용

⚠ 적중 어휘 자체는 `buffer_lexicon.yaml`에 있는 값이라 개인정보가 아니다. **주변 문장을
싣는 순간 개인정보가 된다** — 그래서 어휘만 센다.
"""

from __future__ import annotations

import logging
from typing import Final

from ai.composition.buffer_lexicon import find_forbidden, load_buffer_lexicon
from ai.runtime.redaction import redact

logger = logging.getLogger(__name__)

#: 관측 지점 이름 — 자리가 둘이라(최초 생성·refine 반영) 로그에서 갈라 읽을 수 있어야 한다.
ORIGIN_DRAFT: Final = "draft"
ORIGIN_REFINE: Final = "refine"


def observe_gated_draft(
    text: str, *, origin: str, tenant_id: str, execution_id: str
) -> tuple[str, ...]:
    """게이트를 통과한 본문을 **관측만** 한다 — 적중한 B군 어휘를 돌려준다.

    🔴 **본문을 바꾸지 않는다. 예외를 올리지 않는다.** 관측이 산출을 막으면 그건 관측이
    아니라 게이트다(그리고 그 판정은 아직 안 섰다 · 99 #79).

    ⚠ **자리가 둘이라 이 함수 하나로 모았다** — 최초 생성(`graph.py`)과 refine 반영
    (`refine.py`). 같은 것이 두 곳에 살면 하나가 낡는다(99 #02).

    돌려주는 값은 **검사가 세기 위한 것**이다 — 프로덕션 호출자는 무시해도 된다.
    """
    replacements = tuple(item.source for item in load_buffer_lexicon().replacements)
    hits = find_forbidden(text, replacements)
    if hits:
        logger.warning(
            "초안 완충어휘 적중 — origin=%s tenant=%s execution=%s len=%d terms=%s "
            "(차단 안 함 · 게이트 검출 판정의 표본 · 99 #79·#80)",
            origin,
            tenant_id,
            execution_id,
            len(text),
            ",".join(hits),
        )
    if redact(text).uncertain:
        logger.warning(
            "초안 출력측 마스킹 불확실 — origin=%s tenant=%s execution=%s len=%d "
            "(🔴 차단하지 않는다 · 99 #28의 여는 조건 표본)",
            origin,
            tenant_id,
            execution_id,
            len(text),
        )
    return hits


__all__ = ["ORIGIN_DRAFT", "ORIGIN_REFINE", "observe_gated_draft"]
