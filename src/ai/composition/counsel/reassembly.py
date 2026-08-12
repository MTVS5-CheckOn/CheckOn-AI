"""늦게 끝난 잡의 결과를 **되살리는** 파생 (㉻ · 지시서 73 §6).

🔴 **문제는 「본문이 없다」가 아니라 「원 요청이 없다」였다.** `_refresh_view`가 phase만
갱신하고 `result=None`을 내보낸 이유를 종전 docstring이 이렇게 적었다:

    "결과 본문을 다시 조립하려면 원 요청(citations·labels_applied의 출처)이 필요한데
     그건 영속 설계 몫이다"

그 영속이 섰다 — `COUNSEL_CONTEXT_BUNDLE`이 워커 입력을 들고 있고 그 안의 `DraftContext`가
**요청에서 파생된 전부**다. 그래서 이 모듈은 *"요청 없이 같은 값을 만드는 법"* 을 정의한다.

⚠ **새 파생을 발명하지 않는다** — 최초 응답과 **바이트 같은 값**이어야 한다. 그래서
`labels_applied`는 `labels.labels_applied_of()` 한 함수가 만들고, `citations`는 여기
한 함수가 만들며 **라우터의 요청 경로도 같은 함수를 쓴다**(99 #02).
"""

from __future__ import annotations

from ai.contracts.composition import DraftContext
from ai.contracts.counsel import Citation


def citations_of_context(context: DraftContext) -> tuple[Citation, ...]:
    """`DraftContext` → 계약 `citations` (§4-③ ≥1).

    🔴 **요청 경로와 같은 함수다.** 종전 `_citations_of(request)`는
    `request.context.citable_facts()`(= `record_id`가 있는 fact 전수)를 1부터 세어
    `L{n}`을 붙였다. `_draft_context()`가 그 fact들을 `EvidenceFact(label="근거",
    value=summary, record_id=record_id)`로 옮기므로 **여기서 같은 목록이 나온다.**

    ⚠ **`record_id`가 없는 fact를 세지 않는다** — 세면 뒤 항목의 `cite_id`가 밀려
    같은 잡의 두 응답이 다른 앵커를 갖는다.
    """
    return tuple(
        Citation(cite_id=f"L{index}", record_id=fact.record_id, summary=fact.value)
        for index, fact in enumerate(
            (fact for fact in context.facts if fact.record_id), start=1
        )
        if fact.record_id
    )


__all__ = ["citations_of_context"]
