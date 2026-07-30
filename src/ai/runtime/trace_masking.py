"""트레이스 마스킹 훅 (b) — `redaction.py`(a)의 옆자리.

소유: 박진희 (runtime — `docs/02_ownership.md` §3). B의 `llm/gateway.py`가 정의한
`TraceMaskingHook` Protocol의 A측 구현이며, B의 private 기본값(`_NoOpTraceMaskingHook`)을
가져다 쓰지 않는다. 09 §1-10 ③이 나눈 두 훅 중 **(b)** 자리다((a)=전송 redaction).

━━ 이 자리에서 할 수 있는 일과 할 수 없는 일 (2026-07-30 실측) ━━

**할 수 없다 — 트레이스를 가리는 것.** 이름이 `trace_masking_hook`이지만 이 자리에서
LangSmith 기록을 바꿀 수 없다. `09_integration_proposals.md` §2-16 P1' 부속 확인으로
실측한 근거(상세: `docs/part_a/11_langsmith_trace_probe.md`, langsmith 0.10.2 시점):

1. **반환 요청이 그대로 provider로 간다** (`llm/gateway.py:195-206` —
   `masked_request`가 `_complete_once`에 전달된다). 프롬프트를 지우면 LLM이 빈
   프롬프트를 받는다. 즉 "전송은 원문, 트레이스는 마스킹"이 구조적으로 불가능하다.
   이는 B가 의도한 계약이다(Protocol docstring이 전달 보장을 명시한다).
2. **훅이 프롬프트를 바꿔도 LangSmith 기록은 변하지 않는다.** 탐침 훅으로 프롬프트에
   마커를 넣고 counsel_pack 그래프를 돌렸을 때 수집된 **span 16개 전부에 마커가
   등재되지 않았다.** LangSmith 자동 계측이 잡는 것은 LangGraph 노드 경계이고,
   노드 입출력에는 프롬프트가 실리지 않는다(state는 `context_ref`·`context_hash`
   포인터만 든다 — `langgraph_state.md` §1.2 ⑨).
3. **briefing 경로는 애초에 트레이스되지 않는다.** `gateway.complete` 직접 호출은
   Runnable/그래프가 아니고 `openai_compat`도 `langsmith.wrappers.wrap_openai`를
   쓰지 않아서, briefing만 돌리면 **span이 0건이고 LangSmith 프로젝트조차 생성되지
   않았다.**

⇒ **실제 은닉의 제어점은 P2다** — LangSmith `Client(hide_inputs=·hide_outputs=·
anonymizer=)` 또는 env `LANGSMITH_HIDE_INPUTS=true`, 그리고 체크포인터 serde.
이 PR 범위 밖이며 `part_a/11` §5에 근거를 남겼다.

**할 수 있다 — (a)가 빠진 것을 막는 것.** 가리는 게 아니라 **차단**은 이 자리에서
실제로 가능하다. gateway로 나가는 모든 요청이 이 훅을 지나므로, (a) redaction을
건너뛴 프롬프트를 전송 직전에 fail-closed로 세운다(불변식 3). AST 계약 테스트
(`test_composition_redaction.py`)와 층이 다르다 — 그쪽은 "소스에 `redact()` 호출이
있는지"를 정적으로 보고, 이쪽은 **실제 프롬프트 문자열**을 런타임에 본다. `redact()`를
불렀지만 결과를 안 쓴 경우·조립 순서가 뒤집힌 경우·마스킹 뒤 원문을 다시 이어붙인
경우는 AST가 잡지 못한다.

**알려진 한계 2개:**
- 훅은 `_complete_once` **바깥**(`gateway.py:195`)에서 돌기 때문에 여기서 차단하면
  `LlmCallRecord`가 남지 않는다(관측 공백). 예외 종류로는 구분되므로 상위에서
  로깅되지만, `LLM_CALL` 원장에는 시도 기록이 안 남는다.
- **IP(지문·문항 본문) 유출은 이 훅으로 해결되지 않는다.** `redact()`는 개인정보
  패턴 탐지기이고 저작물 본문은 대상이 아니다. 그쪽도 P2·별건이다.
"""

from __future__ import annotations

from ai.contracts.execution import ExecutionContext
from ai.contracts.llm import LLMRequest, RedactionBlocked
from ai.runtime.redaction import redact

__all__ = ["RedactionTripwireTraceHook"]


class RedactionTripwireTraceHook:
    """(a) redaction이 빠진 프롬프트를 전송 전에 세우는 훅 — 트레이스는 가리지 않는다.

    모듈 docstring이 근거를 든다: 이 자리에서 트레이스 은닉은 구조적으로 불가능하고
    (실측), 대신 fail-closed 차단은 가능하다.

    **요청을 변형하지 않는다.** 통과분은 입력 요청을 그대로 돌려주므로
    `_TRACE_IDENTITY_FIELDS`(`role`·`prompt_id`·`prompt_version`·
    `response_schema_name`)는 물론 프롬프트·생성 파라미터까지 바이트 동일하다
    (`gateway._validate_trace_identity`가 식별 4종 변경을 ValueError로 막는다).

    비용은 정규식 1패스뿐이다 — 실측 0.245ms(counsel 837자)·0.034ms(briefing 32자)로
    호출당 예산 15s(04 §2.4)의 0.002% 수준이다.
    """

    def mask(self, request: LLMRequest, context: ExecutionContext) -> LLMRequest:
        """(a)를 통과한 프롬프트면 그대로, 개인정보 흔적이 남아 있으면 차단한다.

        `RedactionBlocked`를 **재사용**한다(새 예외를 만들지 않는다) — gateway가 이를
        `CallOutcome.REDACTION_BLOCKED`로 매핑하고 09 §1-10 ③이 **전송 재시도 대상
        제외**로 못박아 뒀다. 새 예외를 발명하면 그 매핑이 끊겨 정책 차단이 일시
        오류처럼 재시도된다.

        사유 상세는 메시지에 싣지 않는다 — 원문 조각 노출 위험(error_codes §4).
        """
        del context  # 판정에 쓰지 않는다 — 프롬프트 문자열만 본다(결정론).
        outcome = redact(request.prompt)
        if outcome.findings or outcome.uncertain:
            raise RedactionBlocked(
                "전송 프롬프트에 개인정보 흔적이 남아 있다 — (a) redaction 누락"
            )
        return request
