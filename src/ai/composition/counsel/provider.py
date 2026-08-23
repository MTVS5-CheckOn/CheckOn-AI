"""counsel_pack의 LLM 접점 — plan·generate_draft Protocol + 결정론 Fake.

`probe/`의 `tools.py`·`planner.py` 자리에 해당한다(counsel_pack은 ReAct가 아니라 순차
그래프라 도구 호출이 없다). 실제 LLM 호출은 **전부 `llm/gateway` 경유**이며(03 §2),
이 모듈은 Protocol과 CI 기본값인 Fake만 둔다.

**redaction 경계:** gateway로 나가는 프롬프트는 전송 직전 `runtime/redaction.redact`를
거치고, `uncertain`이면 **전송하지 않는다**(fail-closed · 불변식 3). 이 규율은
`tests/ai/contract/test_composition_redaction.py`가 AST로 고정한다.

**불변식 1:** plan 노드는 확정 수치 안에서 강조점만 고른다 — 새 사실·새 수치를 만들지
않는다. 산출 강조점에는 근거 `record_id`가 동반돼야 한다(state 불변식 ①이 재검증).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Final, Protocol, runtime_checkable

from ai.composition.counsel.prompt import (
    PROMPT_ID,
    PROMPT_VERSION,
    assemble_prompt,
    tone_rule_for,
)
from ai.composition.determinism import deterministic_params
from ai.contracts.composition import DraftContext
from ai.contracts.execution import ExecutionContext
from ai.contracts.llm import (
    CallOutcome,
    LlmError,
    LLMRequest,
    LLMResult,
    ModelRole,
    ParseFailed,
    TokenUsage,
)
from ai.llm.gateway import LlmGateway
from ai.runtime.redaction import redact

logger = logging.getLogger(__name__)

#: 문장 하나의 글자 예산 — **실측에서 역산했다**(8/6 3차 산출 4건).
#:
#: | 산출 | 길이 | 문장 | 문장당 |
#: | --- | --- | --- | --- |
#: | S2-③ 초안 | 329자 | 7 | **47.0자** |
#: | S3-A1 | 243자 | 8 | 30.4자 |
#: | S3-A4 | 254자 | 9 | 28.2자 |
#: | S3-A1(1차) | 253자 | 8 | 31.6자 |
#:
#: 실측 최대 47.0자에 여유 계수 ≈1.3을 얹었다. ⚠ 종전 값 120은 실측 최대의 **2.6배**라
#: 상한이 사실상 없는 값이었는데, 그게 안 드러난 이유는 **문장 수 쪽에서 반대로 3배를
#: 깎고 있었기 때문**이다(아래 `max_chars_for` 참조) — 두 오차가 서로를 가렸다.
#: ⚠ 표본 4건이고 그중 초안 경로는 1건이다. 회차가 쌓이면 재역산한다.
CHARS_PER_SENTENCE = 60

#: 초안·plan 생성 파라미터 — 재현 축(temperature·seed)의 **정본은 `llm/determinism.py`**
#: (B 소유)이고 `composition/determinism.py`는 재수출이다(99 ㊼·ⓨ).
#: 워커가 AI_RUN.generation_params에 이 값을 적재한다.
COUNSEL_GEN_PARAMS: Final = deterministic_params()


def max_chars_for(context: DraftContext) -> int:
    """이 조합의 **초안 전체** 글자 상한 — tone_map에서 파생(03 §1).

    🔴 **종전에는 블록 하나분만 냈다**(`sentences_per_block × 120`). 그런데 게이트는
    `check_counsel_gate(text, …)`로 **본문 전체**를 받는다 — 프롬프트가 "블록 3개 ×
    블록당 3문장"을 지시하는 조합에서 상한은 **3문장분**이었다. 지시대로 쓰면 막힌다.

    **재현(8/6):** `narrative.anxious.attitude.frequent`(3블록 × 3문장 = 9문장 요구)에서
    실측 문장 길이(46.8자)로 9문장을 쓰면 421자 → `too_long:421>360`. 3차에 통과한 유일한
    초안은 LLM이 **지시를 덜 따라 7문장(329자)** 만 쓴 것이었다(99 ㉤).

    ⚠ 블록 단위로 쪼개는 쪽이 아니라 **상한을 전체 기준으로 맞추는 쪽**을 골랐다 —
    쪼개기는 `DraftBlock`·`BlockType`을 살리는 별건이고 **99 #31**에 미결로 있다.
    ⚠ **(8/9 정정) 종전에는 `99 ㊱`을 가리켰다** — ㊱은 **묶인 등재**라 표제(`draft_status`
    어휘)는 닫혔고 이 꼬리만 미결이었다. 그 꼬리를 #31로 갈라 냈다(99 #15).
    """
    rule = tone_rule_for(context)
    return len(rule.blocks) * rule.sentences_per_block * CHARS_PER_SENTENCE


def min_chars_for(context: DraftContext) -> int:
    """이 조합의 **초안 전체** 글자 하한 — 상한과 **같은 자리에서** 파생한다(03 §1 · 99 #13).

    🔴 **게이트 규칙 일곱이 전부 「있으면 안 되는 것」이었다** — 금칙어·기호·마스킹 토큰·
    근거 없는 수치… 그래서 `"네."` 두 글자가 `passed=True`로 떨어져 나와 학부모에게
    `generated`로 나갈 수 있었다. **금지 목록은 아무리 길어도 하한이 되지 않는다.**

    **값의 근거 — 「블록당 최소 한 문장」:**

        min = len(rule.blocks) × CHARS_PER_SENTENCE
        max = len(rule.blocks) × rule.sentences_per_block × CHARS_PER_SENTENCE

    ⚠ **지시한 길이를 강제하는 것이 아니다.** 톤 규칙은 블록당
    `sentences_per_block`문장을 지시하는데 하한은 그 **1/`sentences_per_block`** 이다 —
    *"각 블록이 최소 한 문장은 됐다"* 만 요구하고 나머지는 상한이 든다. 퇴화 산출을
    막는 바닥이지 품질 기준이 아니다.

    **실측 위에 놓았다**(4차·5차 실 LLM):

        실 초안 글자수   300 · 339 · 341 (5차 S2)  ·  358 / 361 (4차 S5)  ·  466 / 366 (5차 S5)
        관측 최소        300
        하한 최대값      240 (4블록 조합)          ⇒ 여유 60자
        24조합 창        [180, 360] ~ [240, 1200]  ⇒ 가장 좁은 창이 180자

    ⚠ **`gate_exhausted`가 두 회차 0건이라 지금 넣을 수 있었다** — 게이트가 이미 소진
    직전이었다면 하한이 그걸 무너뜨린다(99 ㉤ 재측정이 이 작업의 전제다).
    """
    rule = tone_rule_for(context)
    return len(rule.blocks) * CHARS_PER_SENTENCE


PLAN_PROMPT_ID: Final = "composition/counsel_plan"
PLAN_PROMPT_VERSION: Final = "0.2"
"""🔴 **0.1 → 0.2 (2026-08-24 · 99 #198).** 템플릿 문면이 바뀌었다 — 두 줄에서
`redact()` 가 **자기 지시문을 마스킹하고 있었다**:

    «아래 **학생별** 근거 데이터에서 …»   → `⟪이름1⟫ 학생별`
    «강조점은 **학생당** 최대 …»          → `강⟪이름2⟫ 학생당`

⚠ 🔴 원인은 `name_honorific` 의 «`[가-힣]{2,3}` + 호칭 `학생`» 이다 — **「… 학생」 앞에
2~3글자 한글이 오면 그 앞말이 이름으로 잡힌다.** 우리 지시문이 그 형태였다.
🔴 `plan` 도 `redacted.masked_text` 를 보내므로 **막히지 않고 문면이 바뀐 채 나갔다.**
⇒ 문면을 고쳤고, 템플릿이 바뀌었으므로 버전을 올린다."""

_PLAN_PROMPT_PATH: Final = (
    Path(__file__).resolve().parents[2]
    / "llm"
    / "prompts"
    / "templates"
    / "composition"
    / "counsel_plan.txt"
)


@lru_cache
def _plan_template() -> str:
    return _PLAN_PROMPT_PATH.read_text(encoding="utf-8")


def assemble_plan_prompt(
    contexts: Mapping[str, DraftContext],
    student_refs: Sequence[str],
    *,
    max_points: int,
) -> str:
    """plan 프롬프트 — 학생별 근거를 **record_id와 함께** 제시한다(결정론).

    record_id가 없는 fact(집계·기준선 파생)는 인용 대상이 아니므로 제시하지 않는다 —
    LLM에게 인용할 수 없는 근거를 보여주면 지어내게 된다.
    """
    blocks: list[str] = []
    for ref in student_refs:
        context = contexts.get(ref)
        if context is None:
            continue
        lines = [
            f"  - {fact.label}: {fact.value} (record_id={fact.record_id})"
            for fact in context.facts
            if fact.record_id
        ]
        blocks.append(f"{ref}\n" + ("\n".join(lines) if lines else "  - (인용 가능한 근거 없음)"))
    return _plan_template().format(
        max_points=max_points, student_blocks="\n".join(blocks)
    )


def parse_plan_response(text: str, student_refs: Sequence[str]) -> dict[str, list[str]]:
    """`학생참조 | 강조점1; 강조점2` 형식을 파싱한다 — 관용적이되 결정론.

    형식이 어긋난 줄·미지 학생은 조용히 버린다(근거 실존 검증이 뒤에서 한 번 더 걸러낸다).
    """
    known = set(student_refs)
    parsed: dict[str, list[str]] = {}
    for line in text.splitlines():
        if "|" not in line:
            continue
        ref, _, rest = line.partition("|")
        ref = ref.strip()
        if ref not in known:
            continue
        points = [p.strip() for p in rest.split(";") if p.strip()]
        if points:
            parsed[ref] = points
    return parsed


class RedactionBlockedError(LlmError):
    """마스킹 불확실 — 전송하지 않았다(fail-closed · 불변식 3).

    ⚠ **`contracts/llm.RedactionBlocked`와 이름이 겹친다. 같은 개념·다른 층이다**
    (99 ㉳ · 8/8 조사).

        이 예외      **전송 전** 차단 — `redact()`가 uncertain이면 `gateway.complete()`를
                     **부르기 전에** raise한다(`write():238` · `plan():288`)
        contracts    **게이트웨이 안** 차단 — 트레이스 마스킹 훅이 raise하고
                     (`runtime/trace_masking.py:84`) `_exception_outcome`이
                     `CallOutcome.REDACTION_BLOCKED`로 분류한다

    🔴 **「분류가 샌다」가 아니다** — 99 ㉳가 *"provider 쪽 예외는 그 분류를 못 받는다"* 로
    등재됐는데, **못 받는 게 아니라 그 층을 안 탄다.** 실측(8/8):

        정상 경로         LLM_CALL 1행 · outcome=ok
        이 예외 발생 시   LLM_CALL **0행** — 게이트웨이를 안 불렀으니 분류할 호출이 없다
                          (`REDACTION_BLOCKED`로 남은 건 0 · plan 경로도 동일)

    ⚠ **그럼 이 안전 사건은 어디 남는가** — **자기 층의 축에 남는다.** 그래프가 이 예외를
    잡아 학생 노드는 `fail_reason="redaction_blocked"`, plan 노드는
    `PlanOutcome.REDACTION_BLOCKED`로 적는다(#146이 만든 값). **원장의 `CallOutcome`이
    아니라 state·결과 레코드가 든다** — 층마다 자기 축에 기록하는 것이고 누락이 아니다.

    ⚠ **이름 통합은 하지 않았다** — `contracts/llm.py`가 양자 승인 파일이고, 통합해도
    위 층 구분은 그대로 남아 **오히려 한 이름이 두 층을 뜻하게 된다.**
    """


class PlanUnparsedError(LlmError):
    """plan 응답이 **왔는데 형식을 안 지켰다** — 파싱 결과가 0건이다(99 ㉲ · `unparsed`).

    🔴 **장애가 아니라 사유다.** 그래프는 이 예외를 잡아 `plan_outcome=unparsed`로 적고
    **무강조로 계속 진행**한다(plan은 부가정보다). 예외로 올리는 이유는 planner가 그래프에
    이 사실을 전할 **다른 경로가 없기** 때문이다 — `plan()`이 `dict`를 돌려주므로 "형식
    위반으로 0건"과 "모델이 비워 뒀다"가 반환값에서 같아진다.

    ⚠ **대안을 재 보고 골랐다.** 반환형을 `PlanReport`로 바꾸면 planner 대역 18곳이 딸려
    오고, planner에 `last_responded()` 같은 상태를 두면 **provider가 프로세스 공용 1개**라
    (`set_counsel_provider`) 동시 실행 잡끼리 값이 섞인다. 예외는 무상태이고 이 파일에 이미
    같은 어휘가 있다(`RedactionBlockedError` · writer의 *"응답이 비었다"* `LlmError`).
    """


@runtime_checkable
class CounselPlanner(Protocol):
    """plan 노드 — 학생별 강조점을 고른다(LLM 1회). 새 사실 생성 금지."""

    async def plan(
        self,
        *,
        contexts: Mapping[str, DraftContext],
        student_refs: Sequence[str],
        execution_context: ExecutionContext,
    ) -> dict[str, list[str]]:
        """student_ref → 강조점 목록(각 항목에 근거 `record_id` 동반)."""
        ...


@runtime_checkable
class DraftWriter(Protocol):
    """generate_draft 노드 — 블록 본문을 만든다."""

    async def write(
        self,
        *,
        context: DraftContext,
        execution_context: ExecutionContext,
        emphasis: Sequence[str] = (),
        gate_feedback: str = "",
        refine_instruction: str = "",
        previous_text: str = "",
    ) -> str:
        """조립·마스킹을 마친 프롬프트로 초안 본문을 받는다.

        🔴 `previous_text`는 **다듬기 전용 입력**이다 — 기본값이 빈 문자열이라 최초 생성
        경로는 안 넘기고, 그 경로의 프롬프트는 이 인자 때문에 바뀌지 않는다.
        ⚠ `refine_instruction`·`gate_feedback`이 이미 같은 형태로 이 시그니처에 살고 있다 —
        "다듬기 턴에만 있는 입력"의 자리가 여기라는 선례다. `DraftContext`에 넣지 않은 이유:
        컨텍스트는 **학생 스냅숏**(근거·라벨)이고 직전 본문은 **이 턴의 상태**다.

        `emphasis`는 근거 실존 검증을 통과한 강조점이다 — 비면 프롬프트가 현행과 동일하다.
        `gate_feedback`은 직전 게이트 실패의 수정 지시다(05 §6-2) — **1회차는 빈 문자열**
        이라 프롬프트가 바이트 동일하다. 문구는 그래프가 `gate_feedback.instruction_for`로
        파생해 넘긴다(사유 코드가 이 경계를 넘지 않는다).
        """
        ...


class GatewayDraftWriter:
    """실 경로 — `llm/gateway`를 role=counselor로 호출한다.

    프롬프트 조립 → **redaction(fail-closed)** → gateway 순서를 지킨다.
    재시도를 여기서 돌리지 않는다 — 게이트 실패 재생성은 그래프가 ≤3으로 관리하고,
    전송 재시도는 조립부가 `transport_retry`로 주입한다(브리핑 narrator와 동일하게 0회).
    """

    def __init__(self, gateway: LlmGateway) -> None:
        self._gateway = gateway

    async def write(
        self,
        *,
        context: DraftContext,
        execution_context: ExecutionContext,
        emphasis: Sequence[str] = (),
        gate_feedback: str = "",
        refine_instruction: str = "",
        previous_text: str = "",
    ) -> str:
        redacted = redact(
            assemble_prompt(
                context, emphasis, gate_feedback, refine_instruction, previous_text
            )
        )
        if redacted.uncertain:  # fail-closed — 불확실하면 LLM에 보내지 않는다
            raise RedactionBlockedError("상담 초안 프롬프트의 마스킹이 불확실하다")
        try:
            result = await self._gateway.complete(
                LLMRequest(
                    role=ModelRole.COUNSELOR,
                    prompt=redacted.masked_text,
                    prompt_id=PROMPT_ID,
                    prompt_version=PROMPT_VERSION,
                    generation_params=COUNSEL_GEN_PARAMS,
                ),
                execution_context,
            )
        except ParseFailed:
            #: 🔴 **여기가 축이다 — 반환된 `outcome`이 아니라 예외다.**
            #: 실측(8/19): 게이트웨이는 실패를 **예외로 re-raise**하고(`llm/gateway.py`)
            #: 어느 provider도 `outcome != OK`인 `LLMResult`를 **반환하지 않는다**
            #: (전부 `outcome=CallOutcome.OK`로 반환하고 실패는 던진다 — `briefing.py`의
            #: 주석이 그 규약을 이미 적어 뒀다). ⇒ 아래 `result.outcome` 검사들은
            #: **실 경로에서 도달하지 않는다.**
            #: ⚠ 그리고 `ParseFailed`는 `LlmError`의 **하위형**이라, 잡지 않으면
            #: `graph.py`의 `except LlmError`가 학생을 그 자리에서 종결한다 — 그게 #87이다.
            return ""
        # 🔴 **(8/19 · 99 #87) 왜 빈 본문으로 수렴시키나** — 닿았는데 내용이 없는 것은
        #    「전송 실패」가 아니라 **산출물 결함**이다.
        #    실 provider는 빈 content를
        #    `ParseFailed`로 올리고(`llm/providers/openai_compat.py`) 게이트웨이가
        #    `outcome=parse_fail`로 적재하는데, 그 주석 자신이 *"상위 소비자의 재시도
        #    예산(블록 ≤3)이 소진한다"* 고 **기대를 적어 뒀다.** counsel은 그 예산을
        #    **0회** 썼다 — 빈 문자열로 수렴시키면 게이트가 `empty`로 잡고
        #    `gate_feedback`을 붙여 재생성(≤`regen_max`)을 돈다.
        # ⚠ **대가를 숨기지 않는다**: 모델이 **체계적으로** 빈 응답을 내면 호출이 최대
        #   4배(초안 1 + 재생성 3)이고, 전송은 성공이라 **서킷이 안 열린다**(카운터가
        #   0으로 초기화된다). 그래도 상한이 3이라 유계이고 끝은 `gate_exhausted`라
        #   보이는 종단이다 — 종전에는 **일시적** 빈 응답도 못 살렸다.
        # ⚠ **99 #54가 닫히면 이 판단을 다시 재야 한다** — 어댑터가 `finish_reason`을
        #   읽어 절단(reasoning이 예산을 다 먹어 `content=""`)을 별도 outcome으로 남기면
        #   절단은 `parse_fail`에서 빠져나가 재시도 대상이 아니게 된다.
        # ⚠ **아래 두 검사는 현재 provider들로는 도달하지 않는다**(위 실측) — 그래도 지우지
        #   않는다. 게이트웨이 계약이 「실패를 결과로 돌려주는」 쪽으로 바뀌거나 다른
        #   provider가 그렇게 하면 **여기가 유일한 방어**다. 🔴 **다만 결말은 위 `except`와
        #   같게 맞춰 둔다** — 같은 사건이 어디서 잡히느냐에 따라 다른 결말이면 그 자체가
        #   갈림이다.
        if result.outcome is CallOutcome.PARSE_FAIL:
            return ""
        # **전송 장애**를 빈 문자열로 삼키면 장애가 게이트 실패(`gate_exhausted:empty`)로
        # **오분류**된다 — 그러면 서킷 카운터도 안 오르고 알럿이 뜨지 않는다.
        # LlmError로 승격해 `llm_failed` 경로(서킷 포함)로 태운다(error_codes §3).
        # 🔴 **문장을 좁힌 것이지 철회한 게 아니다** — `timeout`·`provider_error`·
        #    `redaction_blocked`에 대해서는 여전히 참이다. 위에서 갈라 나간 `parse_fail`만
        #    예외이고, 그 하나가 「모델에 못 닿았다」가 아닌 유일한 outcome이다.
        if result.outcome is not CallOutcome.OK:
            raise LlmError(f"counselor 호출 실패 outcome={result.outcome.value}")
        # 🔴 **(8/19) `outcome=OK`인데 본문이 빈 경우 — 실 provider로는 도달하지 않는다.**
        #   실측: `openai_compat.py`가 빈 content를 `ParseFailed`로 올리므로 여기 오기 전에
        #   위 `parse_fail` 가지가 잡는다. **대역·다른 provider에서만 온다.**
        #   ⚠ 그래도 지우지 않는다 — 도달 불가라고 **결말이 달라도 되는 건 아니다.**
        #   `parse_fail`과 **같은 사건**(닿았는데 내용이 없다)이라 **같은 결말**로 수렴시킨다.
        #   다르게 두면 *"어느 provider를 쓰는가"* 가 판정을 바꾼다.
        text = (result.text or "").strip()
        if not text:
            return ""
        return text



class GatewayPlanner:
    """실 plan 경로 — `GatewayDraftWriter`와 **같은 규율**이다.

    프롬프트 조립 → **redact(fail-closed)** → gateway(role=counselor) → 구조화 파싱.
    파싱 실패·빈 응답은 예외로 올리지 않고 **빈 강조점**으로 수렴한다 — plan은 부가정보이고
    잡을 죽일 사유가 아니다(그래프의 무강조 진행 경로로 이어진다).

    registry.yaml에 등재하지 않는다 — composition 프롬프트는 템플릿 파일 직접 읽기가 선례다
    (`briefing.txt`·`counsel_pack.txt`와 동일. registry는 B의 `pg.*` 전용).
    """

    def __init__(self, gateway: LlmGateway, *, max_points: int = 3) -> None:
        self._gateway = gateway
        self._max_points = max_points

    async def plan(
        self,
        *,
        contexts: Mapping[str, DraftContext],
        student_refs: Sequence[str],
        execution_context: ExecutionContext,
    ) -> dict[str, list[str]]:
        prompt = assemble_plan_prompt(
            contexts, student_refs, max_points=self._max_points
        )
        redacted = redact(prompt)
        if redacted.uncertain:  # fail-closed — 불확실하면 LLM에 보내지 않는다(불변식 3)
            raise RedactionBlockedError("plan 프롬프트의 마스킹이 불확실하다")
        try:
            result = await self._gateway.complete(
                LLMRequest(
                    role=ModelRole.COUNSELOR,
                    prompt=redacted.masked_text,
                    prompt_id=PLAN_PROMPT_ID,
                    prompt_version=PLAN_PROMPT_VERSION,
                    generation_params=COUNSEL_GEN_PARAMS,
                ),
                execution_context,
            )
        except ParseFailed:
            #: 🔴 **모델이 비워 뒀다 = 적법한 답이다** — 아래 ⓑ가 이미 그렇게 판정해 뒀는데
            #: 그 줄이 **실행되지 않았다**(실측 8/19: 미실행). 실 provider는 빈 content를
            #: `ParseFailed`로 올리고 게이트웨이는 그걸 **예외로 re-raise**하므로,
            #: `result.outcome` 검사에는 애초에 안 온다 — `GatewayDraftWriter`에서
            #: 확인한 것과 **정확히 같은 이유**다(99 #87 · 결정 로그 125).
            #: ⚠ 그동안 이 사건은 `graph.py`의 `except LlmError`로 떨어져
            #: **`plan_outcome=llm_failed`로 계상**됐다 — 정상 동작이 장애로 세어졌고,
            #: 99 ㉲가 셋을 가르려고 만든 작업의 **절반이 조용히 되돌아가 있었다.**
            #: 🔴 **`raise`가 아니라 `return {}`이다** — 판정을 새로 정하는 게 아니라
            #: 이미 적혀 있던 판정을 **실행 가능하게** 만드는 것이다.
            return {}
        # 🔴 **세 경우를 갈라 낸다**(종전에는 전부 `{}`였다 — 99 ㉲).
        # ⚠ **(8/19 실측) 아래 ⓐ·ⓑ 두 검사는 실 경로에서 도달하지 않는다** — 게이트웨이가
        #   실패를 **예외로 re-raise**하고 어느 provider도 `outcome != OK`인 `LLMResult`를
        #   **반환하지 않기 때문**이다. 빈 응답은 위 `except ParseFailed`가 먼저 잡는다.
        #   🔴 **그래도 지우지 않는다** — 게이트웨이 계약이 바뀌거나 다른 provider가
        #   결과로 돌려주면 **여기가 유일한 방어**다. **결말을 위와 같게 맞춰 둔 것**이지
        #   도달한다고 주장하는 게 아니다(도달 불가는 「지금 아무도 안 온다」이지
        #   「결말이 달라도 된다」가 아니다).
        #    ⓐ outcome≠OK = **장애**다. 빈 값으로 삼키면 `GatewayDraftWriter`가 경고한
        #      바로 그 오분류가 plan 쪽에서 일어난다("장애가 게이트 실패로 오분류").
        if result.outcome is not CallOutcome.OK:
            raise LlmError(f"plan 호출 실패 outcome={result.outcome.value}")
        text = (result.text or "").strip()
        #    ⓑ 응답이 비었다 = **모델이 비워 뒀다**. 프롬프트가 *"인용할 근거가 없으면 그
        #      학생은 비워 두세요"* 라고 지시하므로 빈 응답은 적법한 답이고 사유는 `ok`다.
        #      🔴 **(8/19) 종전 주석 «writer는 빈 응답을 `LlmError`로 올린다 — 비대칭이
        #      의도다»는 이제 거짓이다** — 99 #87이 writer도 빈 문자열로 수렴시켰다.
        #      ⚠ **그런데 결말은 여전히 다르고, 다른 이유는 그 주석의 뒷문장 그대로다:**
        #      writer의 빈 값은 게이트 `empty` → `gate_feedback` → **재생성(≤3)**으로 가고
        #      (본문이 결과물이라 비면 다시 만들어야 한다), plan의 빈 값은 **무강조 진행
        #      (재시도 0회)**이다("고를 것이 없다"가 유효한 결과라 다시 물을 이유가 없다).
        #      ⇒ **수렴 형태는 같아졌고 후속 처리가 다르다.**
        if not text:
            return {}
        parsed = parse_plan_response(text, student_refs)
        #    ⓒ 응답은 왔는데 파싱 0건 = **형식 위반**. 사유가 ⓑ와 다르므로 갈라 올린다.
        if not parsed:
            raise PlanUnparsedError(
                f"plan 응답이 형식을 지키지 않았다 — 파싱 0건(응답 {len(text)}자)"
            )
        return _mask_plan_output(parsed)


def _mask_plan_output(parsed: dict[str, list[str]]) -> dict[str, list[str]]:
    """plan 산출을 **포착 시점에** 마스킹한다 (99 #25).

    🔴 **출력측이다.** 위 `redact(prompt)`는 *"보내도 되는가"* 를 묻는 fail-closed
    검사이고, 이것은 *"받은 것을 그대로 남겨도 되는가"* 를 묻는 **변형**이다.
    **선례가 같은 위험에 이미 판정을 냈다** — `db/repositories/llm_payload.py`가
    응답을 *"어떤 게이트도 통과하지 않았다 ⇒ 저장 전 변형"* 으로 다루고
    *"마스킹은 포착 시점에 한다(저장 시점이 아니다)"* 를 못 박는다.
    `emphasis_points`는 **같은 성질의 값인데 다른 저장소(팩 스냅숏)로 간다** —
    같은 값에 두 규율이 서 있었다.

    **왜 파싱 뒤인가** — 응답 전문에 걸면 구조(`학생참조 |`)까지 변형 대상이 되어
    별칭이 마스킹되면 매칭이 깨진다. 파싱 뒤에는 구조가 이미 확보돼 있고 **값만** 바뀐다.

    **왜 저장 직전이 아닌가** — 그 사이 구간에 원문이 state·체크포인트로 앉는다.
    `llm_payload`가 없애려고 규약을 세운 바로 그 구간이다.

    ⚠ **`uncertain`으로 막지 않는다.** 입력측의 fail-closed는 *"안 보낸다"* 인데 여기는
    **이미 받은 값**이라 막을 대상이 없다. `⟪확인필요⟫`로 치환된 문면은 원문보다 안전하고,
    드롭하면 근거만 사라진다(안전은 안 늘어난다).

    ⚠ **재입력에서 2차 마스킹이 나면 안 된다** — 이 값은 `graph.py`가 writer 프롬프트에
    다시 싣고 그 프롬프트가 `redact()`를 또 탄다. **그 경로는 이미 지원 대상**이다:
    트립와이어와 `LLM_PAYLOAD` 저장 훅이 마스킹 통과본을 재검사하므로
    `test_redaction_idempotence.py`가 코퍼스 전건 멱등성을 세워 뒀다.

    🔴 **이 처방은 fail-closed가 아니다** — `redact()`는 호칭·조사 같은 **문맥 신호**가
    있어야 인명을 확정하고, `"김민준"` 단독은 안 잡는다(실측 · 99 #28). **잡히는 형태가
    늘어난 것**이고 *"실명이 못 들어온다"* 가 아니다. 그 한계를 #28로 갈라 등재했다.
    """
    masked: dict[str, list[str]] = {}
    for ref, points in parsed.items():
        replaced = [redact(point).masked_text for point in points]
        if replaced != points:
            # ⚠ 조각을 남기지 않는다 — 로그에 원문을 실으면 마스킹한 값이 로그로 샌다.
            logger.info("plan 산출 마스킹 적용 student_ref=%s points=%d", ref, len(points))
        masked[ref] = replaced
    return masked


class CompositeCounselProvider:
    """plan·write를 **한 객체**로 묶는다 — 라우터·워커가 그 형태로 받는다.

    `GatewayPlanner`와 `GatewayDraftWriter`는 노드가 다르고 프롬프트도 달라 따로 있는 게
    맞지만, 주입 지점은 하나다(`set_counsel_provider`). 그 간극을 메우는 어댑터다.

    ⚠ 이 클래스는 평가 러너(`evaluation/counsel_llm_smoke.py`)의 `_CompositeProvider`를
    **승격**한 것이다 — 러너가 실 경로를 돌리려고 먼저 만들었는데, 프로덕션 조립 루트가
    같은 모양을 필요로 했다. 두 벌로 두면 따로 늙는다(99 ⑰·㉚ 패턴)."""

    def __init__(self, planner: CounselPlanner, writer: DraftWriter) -> None:
        self._planner = planner
        self._writer = writer

    async def plan(
        self,
        *,
        contexts: Mapping[str, DraftContext],
        student_refs: Sequence[str],
        execution_context: ExecutionContext,
    ) -> dict[str, list[str]]:
        return await self._planner.plan(
            contexts=contexts,
            student_refs=student_refs,
            execution_context=execution_context,
        )

    async def write(
        self,
        *,
        context: DraftContext,
        execution_context: ExecutionContext,
        emphasis: Sequence[str] = (),
        gate_feedback: str = "",
        refine_instruction: str = "",
        previous_text: str = "",
    ) -> str:
        return await self._writer.write(
            context=context,
            execution_context=execution_context,
            emphasis=emphasis,
            gate_feedback=gate_feedback,
            refine_instruction=refine_instruction,
            previous_text=previous_text,
        )


class FakeCounselProvider:
    """결정론 Fake — 시나리오 주입식. CI·테스트 기본값.

    LLM을 호출하지 않고 주입된 시나리오를 순서대로 소비한다. 시계·난수를 쓰지 않는다.
    """

    def __init__(
        self,
        *,
        drafts: Sequence[str | Exception] = (),
        emphasis: Mapping[str, list[str]] | None = None,
    ) -> None:
        self._drafts = tuple(drafts)
        self._emphasis = dict(emphasis or {})
        self.write_calls: list[str] = []
        self.plan_calls: list[tuple[str, ...]] = []
        #: 시도별 수정 지시 — 재생성 피드백(05 §6-2)이 실제로 전달됐는지 볼 수 있게 남긴다.
        self.gate_feedbacks: list[str] = []
        #: 턴별로 받은 직전 본문 — 다듬기 누적이 실제로 전달되는지 본다.
        self.previous_texts: list[str] = []

    async def plan(
        self,
        *,
        contexts: Mapping[str, DraftContext],
        student_refs: Sequence[str],
        execution_context: ExecutionContext,
    ) -> dict[str, list[str]]:
        self.plan_calls.append(tuple(student_refs))
        if self._emphasis:
            return {ref: list(self._emphasis.get(ref, [])) for ref in student_refs}
        # 기본 시나리오 — 컨텍스트가 실제로 제공한 근거로만 강조점을 만든다(새 사실 없음).
        planned: dict[str, list[str]] = {}
        for ref in student_refs:
            context = contexts.get(ref)
            if context is None or not context.facts:
                continue
            fact = context.facts[0]
            planned[ref] = [f"{fact.label} {fact.value} (record_id=le_{ref})"]
        return planned

    async def write(
        self,
        *,
        context: DraftContext,
        execution_context: ExecutionContext,
        emphasis: Sequence[str] = (),
        gate_feedback: str = "",
        refine_instruction: str = "",
        previous_text: str = "",
    ) -> str:
        # Fake는 강조점·지시를 소비하지 않는다 — 시나리오 순서로만 응답한다.
        del emphasis, refine_instruction
        #: 직전 본문이 실제로 전달됐는지는 관측한다(누적 배선의 회귀 감지).
        self.previous_texts.append(previous_text)
        self.gate_feedbacks.append(gate_feedback)  # 소비는 안 하되 전달 여부는 관측한다
        index = len(self.write_calls)
        self.write_calls.append(context.student_ref)
        if index < len(self._drafts):
            step = self._drafts[index]
        elif self._drafts:
            step = self._drafts[-1]
        else:
            # 🔴 대역도 **초안처럼 생겨야 한다**(99 #14) — 종전에는
            #   `fallback_text`(~19자)를 그대로 냈고 그게 하한 미만이었다.
            step = gate_floor_draft(context.fallback_text)
        if isinstance(step, Exception):
            raise step
        return step


#: plan 프롬프트의 학생 블록 머리 — `assemble_plan_prompt`가 만드는 형식이다.
#: `{ref}` 다음 줄부터 `  - {label}: {value} (record_id=…)`가 이어진다.
_PLAN_BLOCK_LINE: Final = re.compile(
    r"^(?P<ref>\S+)\n\s+- (?P<label>[^:]+): (?P<value>.+?) \(record_id=(?P<rid>[\w-]+)\)",
    re.MULTILINE,
)

#: 🔴 **대역 초안을 하한 위로 올리는 중립 꼬리** (99 #13·#14).
#:
#: 게이트에 최소 길이 하한이 생기자 **63건이 깨졌다** — 원인은 하한 값이 아니라 **대역
#: 초안이 2~25자**였던 것이다(실 초안은 300~466자). 대역이 실물의 1/20~1/100이었고,
#: **스위트 전체가 「초안이 초안처럼 생겼는가」를 한 번도 안 봤다.** 게이트에 하한이
#: 없던 것과 **같은 뿌리**다(로그 61 *"대역은 계약의 일부"*).
#:
#: ⚠ **지켜야 할 것 넷** — `tests/ai/unit/composition/test_counsel_text_fixture.py`가 단정한다:
#:   ⓐ **숫자 0개** — `ungrounded_number`가 `allowed_numbers()`와 EXACT 대조라
#:      숫자가 하나라도 들어가면 **모든 대역이 그 사유로 막힌다**
#:   ⓐ′ 🔴 **redaction이 `uncertain`을 안 내야 한다.** 처음 쓴 문면이 `가정에서도`·
#:      `정리하는`에서 걸렸다 — 「성씨 1자 + 이름 2자」 휴리스틱이 평범한 활용형을
#:      인명 후보로 잡는다(5차 리포트 §7-a가 같은 조각을 관측했다). 걸리면
#:      **전송 전 fail-closed**라 대역이 500을 낸다. **숫자만 보고 마스킹을 안 본 것이
#:      이 PR에서 실제로 난 실수다.**
#:   ⓑ **상한 안** — 가장 좁은 조합의 `max_chars_for`가 360이다(꼬리 239 + 접두 ≤ 264)
#:   ⓒ 금칙어·내부 용어·기호·마스킹 토큰 없음 — 게이트 일곱을 다 통과해야 한다
#:   ⓓ **실 LLM 산출을 복붙하지 않았다** — 합성이다(옮기면 마스킹·실명 위험이 레포로 온다)
#:
#: 🔴 **정본은 여기 하나다.** 대역이 `src`에 살아서 테스트가 여기서 가져간다
#: (`tests/ai/fakes/counsel_text.py`가 재수출) — 두 곳에 두면 갈린다(#02 부류).
GATE_FLOOR_TAIL: Final = (
    " 제출 흐름은 지난 기간과 비슷하게 이어지고 있습니다."
    " 수업 중 참여 태도도 꾸준한 편이라 지금 흐름을 유지하면 좋겠습니다."
    " 댁에서도 같은 방향으로 지켜봐 주시면 도움이 됩니다."
    " 다음 기간에는 오답을 다시 짚는 시간을 조금 더 늘려 보려고 합니다."
    " 궁금한 점이 있으시면 언제든 편하게 말씀해 주세요."
    " 아이가 스스로 짚어 보는 힘이 붙고 있어 그 부분을 계속 지지해 주시면 좋겠습니다."
    " 학원에서도 같은 흐름으로 도와 나가겠습니다."
)


def gate_floor_draft(text: str) -> str:
    """대역 초안 — **뜻은 그대로 두고 길이만** 실물 수준으로 올린다.

    🔴 **앞을 안 자르고 뒤에 붙인다.** 테스트가 심는 것(금칙어·특정 수치·내부 용어)이
    `text`에 있고 **그게 검사 대상**이다 — 바꾸면 그 테스트가 검증하던 것이 사라진다.
    ⚠ **빈 문자열은 그대로** — `empty` 사유를 보는 테스트가 있고 꼬리를 붙이면 그 케이스가
    사라진다.
    """
    return f"{text}{GATE_FLOOR_TAIL}" if text else text


_DEFAULT_FAKE_TEXT: Final = gate_floor_draft("이번 주 학습 상황을 정리해 드립니다.")


def fake_plan_response(prompt: str) -> str:
    """plan 프롬프트를 읽고 **파서가 먹는 형식**으로 답한다 — CI 대역용(결정론).

    🔴 **"Fake가 프롬프트를 파싱한다"는 결합이 목적이다.** plan 프롬프트 형식이 바뀌면 이
    함수가 깨지고 CI가 알려 준다 — 우연한 결합이 아니라 **의도된 계약**이다. 종전 Fake는
    `request`를 통째로 무시해 plan 응답과 write 응답이 같았고, 그래서
    `parse_plan_response → ground_emphasis → 프롬프트 주입` 체인이 CI에서 **한 번도 돌지
    않았다**(㉪ · ㉦과 같은 유형).

    ⚠ **근거가 0건인 학생은 줄을 내지 않는다.** 그 학생은 강조점 0건이 정답이고,
    `ground_emphasis`가 드롭할 재료를 억지로 만들면 `all_dropped`가 거짓으로 뜬다 —
    관측을 고치려다 관측을 오염시키는 꼴이다(`(인용 가능한 근거 없음)` 블록은 이 정규식에
    애초에 안 걸린다).

    결정론: 학생별 **첫 라벨** 하나만 쓴다. 시계·난수를 쓰지 않는다.
    """
    lines = [
        f"{m['ref']} | {m['label']} {m['value']} (record_id={m['rid']})"
        for m in _dedupe_by_ref(_PLAN_BLOCK_LINE.finditer(prompt))
    ]
    return "\n".join(lines)


def _dedupe_by_ref(matches: Iterable[re.Match[str]]) -> list[re.Match[str]]:
    """학생당 첫 매치만 — 근거가 여러 건이어도 강조점은 하나로 고정한다(결정론)."""
    seen: set[str] = set()
    kept: list[re.Match[str]] = []
    for match in matches:
        if match["ref"] in seen:
            continue
        seen.add(match["ref"])
        kept.append(match)
    return kept


class FakeCounselLlmProvider:
    """`LLMProvider` 대역 — gateway 경로의 CI 기본값(실 벤더 호출 없음).

    `composition/provider.FakeBriefProvider`와 같은 자리다. 결정론이며 시계·난수를 쓰지 않는다.

    🔴 **무인자일 때만 plan 형식으로 답한다**(8/8 · ㉪). 명시 `text`가 주어지면 예전처럼
    그대로 낸다 — 소비처 12곳 중 대부분이 write 경로 검증용으로 특정 문자열을 주입하므로
    (`FakeCounselLlmProvider("정답률은 62%였습니다.")`), 그 동작이 바뀌면 남의 테스트가
    **이유 없이** 깨진다.

    ⚠ **가르는 축은 `prompt_id`다 — role이 아니다.** plan도 write도
    `ModelRole.COUNSELOR`라(:227·:277) role로는 못 가른다.
    """

    def __init__(self, text: str | None = None) -> None:
        #: 명시 주입 여부를 **보존**한다 — 기본값과 "우연히 기본값과 같은 문자열"을
        #: 구분해야 분기가 정확해진다.
        self._pinned = text

    @property
    def name(self) -> str:
        return "fake-counsel"

    async def complete(
        self, request: LLMRequest, context: ExecutionContext
    ) -> LLMResult:
        del context
        if self._pinned is not None:
            text = self._pinned
        elif request.prompt_id == PLAN_PROMPT_ID:
            text = fake_plan_response(request.prompt)
        else:
            text = _DEFAULT_FAKE_TEXT
        return LLMResult(
            outcome=CallOutcome.OK,
            text=text,
            provider=self.name,
            model="template",
            usage=TokenUsage(tokens_in=0, tokens_out=0, cost_usd=0.0),
            latency_ms=0,
        )


__all__ = [
    "COUNSEL_GEN_PARAMS",
    "fake_plan_response",
    "PlanUnparsedError",
    "PLAN_PROMPT_ID",
    "PLAN_PROMPT_VERSION",
    "GatewayPlanner",
    "assemble_plan_prompt",
    "parse_plan_response",
    "CHARS_PER_SENTENCE",
    "CompositeCounselProvider",
    "CounselPlanner",
    "DraftWriter",
    "FakeCounselLlmProvider",
    "FakeCounselProvider",
    "GatewayDraftWriter",
    "RedactionBlockedError",
    "max_chars_for",
]
