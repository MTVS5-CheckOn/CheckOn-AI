"""브리핑 문장 품질 리뷰 — 실 LLM 생성물을 사람이 읽고 평가할 markdown 리포트.

소유: 박진희 (A · 평가 격리 — 프로덕션 경로 아님). local/llm-smoke 전용, push 금지.

**v1(다듬기) vs v2(근거 작성) 나란히 비교.** 데모 스냅숏(6규칙 전부 발화)의 신호마다
- v1: 엔진 초안을 넣어 "다듬는" 옛 프롬프트(이 파일에 재현 — 평가용)
- v2: 구조화 근거만 주고 "직접 작성"하는 현 프로덕션 프롬프트(composition.briefing)
를 각각 **실 LLM으로 3회** 생성한다. 두 변형 모두 현 프로덕션 게이트(check_brief_gate)로
판정해 apples-to-apples로 비교한다 — 사용자가 "다듬기 vs 근거 작성" 차이를 직접 평가한다.

**게이트·금칙어·프롬프트는 스모크 통과를 위해 손대지 않는다** — 걸리면 걸린 대로 보고.
LLM 원문은 가공 없이 기록하되, 저장 전 redaction으로 실명 잔존 0을 확인한다.

실행:
    LLM_PROVIDER=openai_compat uv run python -m ai.evaluation.briefing_preview
    (.env의 OPENAI_* 사용 — 키가 유효해야 함)
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import re
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final
from uuid import UUID

from ai.composition.briefing import (
    BRIEF_GEN_PARAMS,
    MAX_REGEN,
    PROMPT_ID,
    PROMPT_VERSION,
    _assemble_prompt,
)
from ai.composition.briefing_context import BriefingContext, build_contexts, render_evidence_block
from ai.composition.briefing_gate import MAX_BRIEF_LENGTH, check_brief_gate
from ai.composition.provider import BriefingSettings, build_brief_provider
from ai.contracts.detection import Signal
from ai.contracts.execution import Capability, ExecutionContext, GenerationParams, VersionSet
from ai.contracts.llm import LlmError, LLMProvider, LLMRequest, ModelRole
from ai.detection.engine import detect
from ai.evaluation.demo_snapshot import build_demo_request
from ai.runtime.redaction import redact

#: ━━ 🔴 **브리핑 품질 기준선**(99 #27 · 2026-08-25 · №102) ━━
#:
#: 🔴 **왜 여기인가** — 측정치를 **재는 코드 옆**에 둔다. `call_timeouts.yaml` 형식
#: (①실측 ②왜 그 값 ③대가)이 «다음 사람이 읽는다» 를 이미 견뎠고, 그 형식을 따른다.
#: ⚠ 🔴 `local_data/` 원장은 **비추적이라 다음 사람이 못 연다**(`call_timeouts` 가 그
#: 문제를 이미 적어 뒀다) · 99 안의 표는 «측정치» 와 «판단» 이 섞인다 ⇒ 둘 다 안 골랐다.
#: 🔴 **측정치는 설정값과 달리 낡는다** — 그래서 「언제·무엇을·어떻게 잰 것인가」를
#: 다섯 칸으로 못 박는다. ③이 없으면 다음 사람이 **다른 표본으로 재고 비교**한다.
#:
#: 🔴 ① 잰 날짜 : **2026-08-25 (KST)**
#: 🔴 ② 버전    : `PROMPT_VERSION` **0.3** (#198 로 0.2→0.3 · 문면이 바뀐 뒤 첫 측정)
#: 🔴 ③ 표본    : 데모 신호 **11** × 변형 **2**(v1/v2) × 반복 **3** = **66콜** · 한 번
#: 🔴 ④ 수치    : v1(옛 프롬프트)  **70%** (23/33) · 평균 **1250ms**
#:                v3(현 프롬프트)  **67%** (22/33) · 평균 **1866ms**
#:                🔴 **그런데 이 수는 「게이트 통과율」이 아니다** —
#:                실패 **21건이 전부 `llm:LlmUnavailable`**(벤더 미도달)이고
#:                🔴 **게이트 사유 실패는 0건**이다.
#:                ⇒ 🔴 **응답을 받은 45건은 전부 게이트를 통과했다(45/45 = 100%)**
#:                ⇒ 위 70%·67% 는 **가용성 × 통과율**이고, 이 회차의 가용성은
#:                  **68%**(45/66)였다. 🔴 **품질 수치로 읽으면 안 된다.**
#:                🔴 ⚠ **(2026-08-25 · №112 정정) 이 68% 도 45/45 도 벤더에 대한
#:                  사실이 아니었다 — 측정기 결함이 만든 수다**(99 #255). 🔴 **지우지
#:                  않는다** — 지우면 «왜 두 수가 다른가» 를 다음 사람이 못 안다.
#: 🔴 ⑤ 하한    : 🔴 **이 수는 「첫 시도」 통과율이고 프로덕션의 하한**이다 —
#:                `make_brief` 는 게이트 실패 시 `MAX_REGEN` 회 **재생성**하지만
#:                이 러너는 안 한다(`_assert_first_try_only` 가 그 사실을 못 박는다).
#:                ⚠ 🔴 **그걸 모르고 비교하면 «품질이 떨어졌다» 로 오독한다**(99 #217).
#:
#: ⚠ 🔴 **비교 대상이 없다** — 0.2 시절 통과율 **수치**가 저장소에 없다(#27 · 전수).
#: ⇒ 🔴 이 줄은 «비교» 가 아니라 **«기준선을 세운 것»** 이다. 다음 측정이 여기와 비교한다.
#:
#: ━━ 🔴 **재측정(2026-08-25 · №110) — 덮어쓰지 않고 덧붙인다** ━━
#: 🔴 ① 잰 날짜 : **2026-08-25 (KST)** · 같은 날 두 번째
#: 🔴 ② 버전    : `PROMPT_VERSION` **0.3** (무변경 — 프롬프트·표본·재시도 무접촉)
#: 🔴 ③ 표본    : **66콜** 한 번 (신호 11 × 변형 2 × 반복 3) · 예상 66 = 실제 66
#: 🔴 ④ 수치    : v1 **70%** (23/33) 평균 **1463ms** · v3 **67%** (22/33) 평균 **1897ms**
#:                가용성 **68%** (45/66) · 게이트 사유 실패 **0건** ⇒ 응답 온 45 는 **45/45**
#:                ⚠ 🔴 **위 첫 측정과 통과 수가 한 건도 안 다르다**(23/33 · 22/33 · 10/11).
#:                🔴 벤더가 흔들렸다면 이럴 수 없다 — **결정론적 원인**이라는 뜻이다.
#:                🔴 ⚠ **(№112 정정) 이 수도 측정기 결함분이다** — 아래 세 번째 측정이
#:                  같은 표본에서 **가용성 100%** 를 냈다. 🔴 **평균 지연도 오염돼 있었다**:
#:                  즉시 실패한 콜(≈0ms)이 평균에 섞여 **낮게** 나왔다.
#: 🔴 ⑤ 하한    : 위와 같다(첫 시도 · 재생성 없음)
#:
#: 🔴 **⑥ 그리고 그 원인을 이번에 갈랐다(99 #255)** — 🔴 **벤더 가용성이 아니라 측정기다.**
#:   · 신호별: 6/11 신호에 흩어졌고 실패 수가 **그 `signal_type` 건수 × 2(변형)** 에
#:     정확히 비례한다 ⇒ 🔴 **신호 특성과 무관**하다.
#:   · 시간별: 앞 7 · 중 7 · 뒤 7 로 **완전 균등** ⇒ 🔴 **율속·워밍업이 아니다.**
#:   · 🔴 실패 콜 순번이 **4, 7, 10 … 64** — 공차 3 의 등차수열이고, 그건 정확히
#:     **각 `asyncio.run()` 의 첫 콜**이다(변형 22개 중 **맨 처음 하나만 성공** = 21건).
#:   ⇒ 🔴 `run()` 은 변형마다 `asyncio.run` 으로 **새 이벤트 루프**를 열면서 provider
#:     (`AsyncOpenAI`)는 **한 번 만들어 22개 루프에 걸쳐 재사용**한다. 앞 루프가 닫히면
#:     그 커넥션 풀이 죽고, 다음 루프의 **첫 콜**이 죽은 커넥션을 잡아
#:     `APIConnectionError` → `LlmUnavailable("LLM 연결 실패")` 로 떨어진다(문면 21/21 동일).
#:   ⚠ 🔴 **99 #218 · №100 과 같은 패턴**이다 — 「한 루프에 묶인 자원을 다른 루프에서 쓴다」.
#:   🔴 **이 회차는 재는 회차라 안 고쳤다**(#255 에 처방 갈래를 적어 뒀다).
#:   ⚠ 🔴 그래서 «가용성 68%」는 **벤더에 대한 사실이 아니다** — 인용하지 마라.
#:
#: ━━ 🔴 **세 번째 측정(2026-08-25 · №112) — ⓐ 적용 후. 덮어쓰지 않고 덧붙인다** ━━
#: 🔴 ① 잰 날짜 : **2026-08-25 (KST)** · 같은 날 세 번째
#: 🔴 ② 버전    : `PROMPT_VERSION` **0.3** (무변경 — 프롬프트·표본·재시도 무접촉)
#: 🔴 ③ 표본    : **66콜** 한 번 (신호 11 × 변형 2 × 반복 3) · 예상 66 = 실제 66
#: 🔴 ④ 수치    : 🔴 **가용성 100%** (66/66 · 실패 **0건**)
#:                🔴 **첫 시도 게이트 통과율 100%** — v1 **33/33** 평균 **1963ms** ·
#:                v3 **33/33** 평균 **2856ms**
#:                🔴 **분모가 45 → 66 으로 늘었는데도 100% 다** — 종전 45/45 는 편향된
#:                표본이었지만 **통과율 결론은 그대로**였다(이 회차가 답한 물음이다).
#:                ⚠ 🔴 **평균 지연은 「올랐다」가 아니라 「이제 진짜다」** — 종전 값
#:                (1463·1897ms)에는 즉시 실패한 콜이 섞여 있었다.
#: 🔴 ⑤ 하한    : 위와 같다(첫 시도 · 재생성 없음) — 🔴 **프로덕션은 이보다 높다**
#: 🔴 ⑥ 실패 순번 : **없음**(실패 0). 🔴 **공차 3 등차수열이 사라졌다 — ⓐ 의 증명이다.**
#:                ⚠ 🔴 판정 기준은 **돌리기 전에** 정했다: «실패 0» 이 아니라
#:                **«각 변형 첫 콜에 몰리는 수열이 없을 것»**. 실패가 남아도 수열이
#:                없으면 처방은 든 것이다. 🔴 **이 칸을 상설로 둔다**(로그 220 —
#:                «몇 건 실패」만 세지 말고 «몇 번째에 실패」를 같이 세라).
#: 🔴 **산출 본문은 여기 안 적는다**(불변식 3 · 99 #80) — 통과율·지연은 개인정보가 아니다.
_REPEATS = 3
_RESULT_PATH = Path.cwd() / "briefing_preview_result.md"
_NUMBER_RE = re.compile(r"\d+")

#: v1 다듬기 프롬프트 재현 — 평가 격리용(프로덕션에선 v2로 교체됨). 옛 버전과 문자열 동일.
_V1_TEMPLATE = """당신은 수능 국어 학원 강사에게 학생의 위험신호를 한 줄로 전하는 보조입니다.
아래 신호 초안을 강사가 읽기 좋은 자연스러운 한 문장으로 다듬으세요.

규칙:
- 정확히 한 문장, {max_length}자 이내.
- 학생 이름·식별자·과제명·연락처는 절대 쓰지 마세요(초안에 없습니다).
- 낙인·비교·진단 표현 금지(예: 게으르다·다른 아이들은·꼴찌·ADHD).
- 초안에 있는 숫자·단위만 그대로 쓰고, 초안에 없는 숫자는 만들지 마세요.
- 특수 괄호 ⟪ ⟫ 는 쓰지 마세요.
- 점수(score)는 낙인이 되므로 문장에 넣지 마세요.

신호 정보:
signal_type: {signal_type}
lifecycle: {lifecycle}
초안: {draft}

한 문장:
"""


@dataclass
class _Attempt:
    index: int
    raw: str
    gate_ok: bool
    gate_reason: str
    final: str
    latency_ms: int


@dataclass(frozen=True)
class _Failure:
    """실패 1건 — 🔴 **어느 신호가·언제·왜**(99 #255 · №110).

    ⚠ 🔴 종전 측정기는 사유별 `Counter` 만 남겨서 «21건이 전부 `LlmUnavailable`» 까지만
    말할 수 있었다 — 🔴 **«어느 신호에 몰렸나」와 «시간에 몰렸나」에 답을 못 했다.**
    그 둘은 처방이 갈린다: 신호 탓이면 **표본**을 다시 봐야 하고, 앞뒤로 몰렸으면
    **율속·워밍업**이라 간격이 처방이다(로그 193 — 측정기의 누락은 그 수를 믿고
    값을 정하게 만든다).
    """

    ordinal: int
    """실행 전체에서 **몇 번째 콜**인가 — 시간 축의 순번."""
    at_s: float
    """실행 시작으로부터 **몇 초** — 앞/중/뒤를 가른다."""
    signal: str
    variant: str
    kind: str
    """`llm:LlmUnavailable` 처럼 사유 종류."""
    detail: str
    """🔴 원인 문면 — **마스킹 통과분만**(`_scrub`)."""


@dataclass
class _Ledger:
    """두 변형이 **함께 쓰는** 실행 원장 — 시간 축이 실행 전체로 이어져야 한다.

    🔴 **`max_calls` 에 기본값을 두지 않는다**(99 #256 · №113) — 기본값이 있으면
    프로그램에서 부를 때 «상한 없음» 이 다시 생긴다. 부르는 쪽이 **매번 정한다**.
    """

    started: float
    max_calls: int
    """🔴 **총 실 LLM 콜 상한.** `0` 이면 한 콜도 안 태우고 배선만 확인한다."""
    capped: bool = False
    """상한에 걸려 멈췄나 — 🔴 **조용히 자르지 않기 위한 표식**."""
    ordinal: int = 0
    failures: list[_Failure] = field(default_factory=list)


@dataclass
class _Totals:
    calls: int = 0
    passed: int = 0
    latencies: list[int] = field(default_factory=list)
    reasons: Counter[str] = field(default_factory=Counter)


_SECRET_RE: Final = re.compile(r"sk-[A-Za-z0-9_\-]{4,}")
_DETAIL_MAX: Final = 80


def _scrub(text: str) -> str:
    """🔴 원인 문면을 **밖에 낼 수 있는 형태로만** — 키는 어떤 형태로도 안 나간다.

    ⚠ 🔴 어댑터가 내는 문면은 «LLM 연결 실패» · «LLM 일시 실패 429» 처럼 **짧고
    접속정보가 없다**(실측 · `openai_compat.py`). 🔴 그래도 **여기서 한 번 더 막는다**
    — 어댑터가 바뀌면 이 자리가 마지막 관문이다(불변식 3 의 fail-closed 방향).
    """
    scrubbed = _SECRET_RE.sub("sk-***", text.replace("\n", " ").strip())
    masked = redact(scrubbed).masked_text
    return masked[:_DETAIL_MAX]


def _context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=UUID("00000000-0000-4000-8000-0000000000ab"),
        tenant_id="tn_preview",
        capability=Capability.COMPOSITION,
        input_snapshot_hash="preview",
        # ⚠ **프로덕션 버전 세트를 안 쓴다**(99 #20 판정 ⓑ) — 이 값은 **미리보기 러너의
        #   표식**이지 실행된 코드의 버전이 아니다. `counsel_llm_smoke.py`와 같은 판단이고
        #   거기 근거를 적어 뒀다. **다른 것이 결함이 아니라 「왜 다른지」가 없는 것이
        #   결함이었다**(로그 89).
        versions=VersionSet(
            pipeline_version="0.1",
            engine_version="0.1",
            schema_version="0.1",
            contract_version="0.1",
            prompt_version=PROMPT_VERSION,
        ),
    )


def _v1_prompt(signal: Signal) -> str:
    return _V1_TEMPLATE.format(
        signal_type=signal.signal_type.value,
        lifecycle=signal.lifecycle.value,
        draft=signal.brief.text,
        max_length=MAX_BRIEF_LENGTH,
    )


def _note(
    ledger: _Ledger, signal: str, variant: str, kind: str, detail: str, start: float
) -> None:
    """🔴 실패를 **원장에 한 번만** 적는다 — 두 축(신호·시간)이 같은 자리에서 나온다."""
    ledger.failures.append(
        _Failure(
            ordinal=ledger.ordinal,
            at_s=start - ledger.started,
            signal=signal,
            variant=variant,
            kind=kind,
            detail=_scrub(detail),
        )
    )


async def _run_variant(
    provider: LLMProvider,
    prompt: str,
    allowed: frozenset[str],
    fallback: str,
    context: ExecutionContext,
    totals: _Totals,
    ledger: _Ledger,
    signal_label: str,
    variant: str,
    *,
    max_tokens: int | None = None,
) -> list[_Attempt]:
    """한 변형(v1 또는 v3)을 3회 생성. 전송 직전 redaction(이중 방어)·현 게이트 판정.

    max_tokens는 v3 프로덕션 경로(briefing.py)와 동일하게 주입해 지연을 실측 반영한다.
    """
    request = LLMRequest(
        role=ModelRole.GENERATOR,
        prompt=redact(prompt).masked_text,
        prompt_id=PROMPT_ID,
        prompt_version=PROMPT_VERSION,
        generation_params=GenerationParams(max_tokens=max_tokens) if max_tokens else None,
    )
    attempts: list[_Attempt] = []
    for index in range(1, _REPEATS + 1):
        #: 🔴 **상한에 닿으면 여기서 멈춘다**(99 #256) — 넘긴 뒤 자르는 게 아니다.
        if ledger.ordinal >= ledger.max_calls:
            ledger.capped = True
            break
        totals.calls += 1
        ledger.ordinal += 1
        start = time.monotonic()
        try:
            result = await provider.complete(request, context)
            raw = (result.text or "").strip()
            gate = check_brief_gate(raw, allowed)
            latency = int((time.monotonic() - start) * 1000)
            if gate.passed:
                totals.passed += 1
            else:
                kind = gate.reason.split(":")[0]
                totals.reasons[kind] += 1
                _note(ledger, signal_label, variant, kind, gate.reason, start)
            attempts.append(
                _Attempt(
                    index=index,
                    raw=raw,
                    gate_ok=gate.passed,
                    gate_reason="통과" if gate.passed else gate.reason,
                    final=raw if gate.passed else f"(폴백) {fallback}",
                    latency_ms=latency,
                )
            )
        except LlmError as exc:
            latency = int((time.monotonic() - start) * 1000)
            kind = f"llm:{type(exc).__name__}"
            totals.reasons[kind] += 1
            _note(ledger, signal_label, variant, kind, str(exc), start)
            attempts.append(
                _Attempt(
                    index=index,
                    raw=f"<{type(exc).__name__}: {exc}>",
                    gate_ok=False,
                    gate_reason=f"LLM 실패({type(exc).__name__})",
                    final=f"(폴백) {fallback}",
                    latency_ms=latency,
                )
            )
        totals.latencies.append(attempts[-1].latency_ms)
    return attempts


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


_CAPPED_CELLS: Final = "(상한으로 미실행) | ⛔ 콜 상한"


def _attempt_cells(attempt: _Attempt | None) -> str:
    """표의 두 칸(원문 · 게이트) — 🔴 **없으면 「상한으로 미실행」이라고 적는다**."""
    if attempt is None:
        return _CAPPED_CELLS
    verdict = "✅" if attempt.gate_ok else f"❌ {attempt.gate_reason}"
    return f"{_cell(attempt.raw)} | {verdict} ({attempt.latency_ms}ms)"


def _render_signal(
    signal: Signal,
    ctx: BriefingContext,
    v1: list[_Attempt],
    v2: list[_Attempt],
) -> list[str]:
    lines = [
        f"\n## {signal.signal_type.value} "
        f"({signal.display_label} · rule={signal.rule_id.value} · "
        f"lifecycle={signal.lifecycle.value})\n"
    ]
    lines.append(f"**v1 입력 초안(엔진 템플릿):** {signal.brief.text}\n")
    lines.append("**v3 입력 근거 패키지:**\n")
    lines.append("```")
    lines.append(render_evidence_block(ctx))
    lines.append("```")
    lines.append(f"\n허용 숫자(v3): {sorted(ctx.allowed_numbers()) or '없음'}\n")
    lines.append(
        "| 회차 | v1 원문(다듬기) | v1 게이트 | v3 원문(근거 작성) | v3 게이트 |"
    )
    lines.append("| --- | --- | --- | --- | --- |")
    #: 🔴 **길이가 다를 수 있다** — 콜 상한이 변형 **중간**에 걸리면 v1 만 있고 v3 은
    #: 없다(99 #256 · №113 뒤집기 ②가 실제로 그렇게 터뜨렸다: `zip(strict=True)` →
    #: `ValueError`). ⚠ 🔴 `strict=False` 로 **조용히 자르지 않는다** — 그러면 «그 회차는
    #: 원래 없었다» 로 읽힌다. 🔴 **없는 칸을 「상한」이라고 적는다.**
    for index in range(max(len(v1), len(v2))):
        a1 = v1[index] if index < len(v1) else None
        a2 = v2[index] if index < len(v2) else None
        lines.append(
            f"| {index + 1} | {_attempt_cells(a1)} | {_attempt_cells(a2)} |"
        )
    return lines


#: 🔴 **이 측정기가 스스로 말하는 자기 한계**(2026-08-24 · 99 #217).
#: `a6a9b75` 가 라벨 측정기에 넣은 자기검증과 같은 형태다 — 「측정기가 자기 한계를 말한다」.
_FIRST_TRY_CAVEAT: Final = (
    "🔴 **첫 시도 통과율이다 — 프로덕션 통과율이 아니다.** 프로덕션 `make_brief` 는 "
    "게이트 실패 시 `instruction_for(reason)` 을 붙여 `MAX_REGEN` 회 **재생성**하지만, "
    "이 측정기는 `provider.complete` + `check_brief_gate` 를 **한 번**만 태우고 같은 "
    "프롬프트를 반복할 뿐이다(루프·폴백 없음). ⇒ **실제 통과율은 이 수보다 높다** — "
    "이 수는 **하한**이다(99 #217 · #27 재측정 시 오독 주의)."
)


def _assert_first_try_only() -> None:
    """🔴 **측정기가 자기 한계를 잃지 않게 못 박는다**(99 #217).

    누군가 이 측정기에 재생성 루프를 넣으면 위 문구가 **거짓말이 된다** — 그때 여기서
    `AssertionError` 가 나서 «문구도 같이 고쳐라» 를 알린다.
    ⚠ 🔴 반대로 문구를 지우면 「첫 시도 통과율」이 「통과율」로 읽히고, #27 재측정이
    **«품질이 떨어졌다»로 오독**된다 — 그게 이 단언이 막는 일이다.
    """
    source = inspect.getsource(_run_variant)
    assert "MAX_REGEN" not in source and "instruction_for" not in source, (
        "이 측정기에 재생성 루프가 들어왔다 — `_FIRST_TRY_CAVEAT` 문구와 "
        "「첫 시도 통과율」 라벨을 같이 고쳐라(99 #217)."
    )


def _summary(name: str, totals: _Totals) -> list[str]:
    _assert_first_try_only()
    avg = sum(totals.latencies) / len(totals.latencies) if totals.latencies else 0
    rate = (totals.passed / totals.calls * 100) if totals.calls else 0
    lines = [
        f"### {name}",
        f"- 총 호출: **{totals.calls}회** · 🔴 **첫 시도** 게이트 통과율: **{rate:.0f}%** "
        f"({totals.passed}/{totals.calls}) · 평균 응답: **{avg:.0f}ms**",
        f"- {_FIRST_TRY_CAVEAT}",
    ]
    if totals.reasons:
        dist = " · ".join(f"{r} {c}" for r, c in totals.reasons.most_common())
        lines.append(f"- 실패 사유 분포: {dist}")
    else:
        lines.append("- 실패 사유 분포: (없음 — 전 회차 통과)")
    return lines


def _cap_note(ledger: _Ledger, signals: int) -> str:
    """🔴 **상한에 걸렸으면 산출 머리에 적는다**(99 #256).

    ⚠ 🔴 조용히 자르면 «전량 돌았다» 로 읽힌다 — #110 이 «45 가 무작위 표본인가» 를
    못 물었던 것과 같은 자리다. 🔴 **잘렸다는 사실이 수와 같은 화면에 있어야 한다.**
    """
    planned = signals * 2 * _REPEATS
    if not ledger.capped:
        return f"🔴 콜 상한 **{ledger.max_calls}** · 전량 **{planned}** 을 다 돌았다."
    return (
        f"🔴 ⚠ **콜 상한 {ledger.max_calls} 에 걸려 멈췄다 — 전량이 아니다**"
        f"(계획 {planned} 중 **{ledger.ordinal}** 만 돌았다). "
        "아래 수치는 **부분 표본**이다."
    )


def _breakdown(ledger: _Ledger, signals: int, calls: int) -> list[str]:
    """🔴 **갈래를 남긴다** — ① 신호별 ② 시간 분포 ③ 원인 문면(99 #255).

    ⚠ 🔴 «11 신호 중 3개가 죽었다» 와 «처음 20콜이 죽었다» 는 **완전히 다른 사실**이고
    처방이 갈린다. 그래서 셋을 **같이** 적는다 — 하나만 보면 또 못 가른다.
    """
    lines = ["", "## 🔴 실패 갈래(99 #255 · №110)", ""]
    if not ledger.failures:
        return [*lines, "- 실패 **0건** — 가를 것이 없다."]

    by_signal: Counter[str] = Counter(f.signal for f in ledger.failures)
    lines.append(f"### ① 신호별 실패 — {len(by_signal)}/{signals} 신호에서 발생")
    lines.append("")
    lines.append("| 신호 | 실패 | 사유 |")
    lines.append("| --- | --- | --- |")
    for name, count in by_signal.most_common():
        kinds = Counter(f.kind for f in ledger.failures if f.signal == name)
        detail = " · ".join(f"{k} {c}" for k, c in kinds.most_common())
        lines.append(f"| {_cell(name)} | {count} | {detail} |")

    #: 🔴 **콜 순번을 3등분**한다 — 앞뒤로 몰렸으면 신호가 아니라 율속·워밍업이다.
    third = max(calls // 3, 1)
    buckets = Counter(min((f.ordinal - 1) // third, 2) for f in ledger.failures)
    label = ("앞 1/3", "중 1/3", "뒤 1/3")
    spread = " · ".join(f"{label[i]} {buckets.get(i, 0)}건" for i in range(3))
    first, last = ledger.failures[0], ledger.failures[-1]
    lines.extend(
        [
            "",
            "### ② 시간 분포 — 앞쪽인가 뒤쪽인가",
            "",
            f"- 콜 순번 3등분: **{spread}**",
            f"- 첫 실패 **{first.ordinal}번째 콜**({first.at_s:.0f}초) · "
            f"마지막 실패 **{last.ordinal}번째 콜**({last.at_s:.0f}초)",
            "- 실패 콜 순번: "
            + ", ".join(str(f.ordinal) for f in ledger.failures),
        ]
    )

    causes = Counter(f.detail for f in ledger.failures)
    lines.extend(["", "### ③ 원인 문면(🔴 마스킹 통과분)", ""])
    lines.extend(f"- `{_cell(text)}` — **{count}건**" for text, count in causes.most_common())
    return lines


async def _run_all(
    signals: list[Signal],
    contexts: Mapping[str, BriefingContext],
    context: ExecutionContext,
    v1_totals: _Totals,
    v2_totals: _Totals,
    ledger: _Ledger,
) -> list[str]:
    """🔴 **전 변형·전 신호를 한 이벤트 루프 안에서** 돈다(99 #255 ⓐ · №112).

    ⚠ 🔴 종전에는 `run()` 이 **변형마다** `asyncio.run` 을 불렀다(22회). provider
    (`AsyncOpenAI`)는 **한 번 만들어 22개 루프에 걸쳐 재사용**됐고, 앞 루프가 닫히며
    커넥션 풀이 죽어 🔴 **다음 루프의 첫 콜이 그 시체를 잡았다** — 실패 21건이 전부
    거기였다(실패 순번 4·7·10…64 = 공차 3 등차수열 · 99 #255).
    🔴 그 21건이 «가용성 68%» 로 기준선에 적혔다 — **벤더에 대한 사실이 아니었다**.

    🔴 **provider 도 이 안에서 만든다** — 밖에서 만들면 같은 결함이다(루프가 다르다).
    ⚠ 🔴 ⓑ(루프마다 provider 재생성)는 기각했다 — 커넥션 재사용이 없어져 **매번 새
    TLS 핸드셰이크**가 되고 🔴 **지연 수치가 오염된다**(측정기를 고치려다 측정을 망친다).
    🔴 저장소 선례도 ⓐ 쪽이다 — 다른 러너 6개(`counsel_preview`·`problem_preview`·
    `report_llm_smoke`·`classify_eval`·`counsel_llm_smoke`·`pg_ledger_preflight`)는
    전부 **최상위에서 한 번만** 감싼다. 🔴 **이 파일만 예외였다.**
    """
    provider = build_brief_provider(BriefingSettings(llm_provider="openai_compat"))
    body: list[str] = []
    for signal in signals:
        ctx = contexts[signal.signal_id]
        v1 = await _run_variant(
            provider,
            _v1_prompt(signal),
            frozenset(_NUMBER_RE.findall(signal.brief.text)),
            signal.brief.text,
            context,
            v1_totals,
            ledger,
            signal.signal_type.value,
            "v1",
        )
        v2 = await _run_variant(
            provider,
            _assemble_prompt(ctx),
            ctx.allowed_numbers(),
            ctx.fallback_text,
            context,
            v2_totals,
            ledger,
            signal.signal_type.value,
            "v3",
            #: 🔴 값을 복제하지 않고 **프로덕션 파라미터에서 읽는다**(99 #02) —
            #:   복제하면 프로덕션만 바뀌었을 때 프리뷰가 낡은 채로 초록이다.
            #:   2026-08-13 현재 None(천장 없음 · 99 #54).
            max_tokens=BRIEF_GEN_PARAMS.max_tokens,
        )
        body.extend(_render_signal(signal, ctx, v1, v2))
    return body


def run(max_calls: int) -> int:
    response = detect(build_demo_request())
    signals = list(response.signals)
    if not signals:
        print("데모 신호가 없습니다 — 스냅숏 구성을 확인하세요.")
        return 1
    contexts = build_contexts(build_demo_request(), signals)

    context = _context()
    v1_totals, v2_totals = _Totals(), _Totals()
    ledger = _Ledger(started=time.monotonic(), max_calls=max_calls)
    #: 🔴 **여기 한 번뿐이다** — 이 파일에서 `asyncio.run` 은 이 자리가 유일하다.
    body = asyncio.run(
        _run_all(signals, contexts, context, v1_totals, v2_totals, ledger)
    )

    report = "\n".join(
        [
            "# 브리핑 문장 품질 리뷰 — v1(다듬기) vs v3(근거 작성·지연개선)",
            "",
            f"신호 {len(signals)}건 × {_REPEATS}회 × 2변형 = "
            f"{v1_totals.calls + v2_totals.calls}회 호출 · 현 프로덕션 게이트로 동일 판정 · "
            f"프롬프트·게이트 무수정(재생성 상한 {MAX_REGEN})",
            _cap_note(ledger, len(signals)),
            *body,
            "\n## 요약",
            "",
            *_summary("v1 다듬기(옛 프롬프트)", v1_totals),
            "",
            *_summary("v3 근거 작성(현 프롬프트·max_tokens=128·전체 15s)", v2_totals),
            *_breakdown(ledger, len(signals), v1_totals.calls + v2_totals.calls),
            "",
        ]
    )
    # 저장 전 redaction — 실명류 잔존 0 확인(무주어 수치 입력이라 없어야 정상)
    if redact(report).uncertain:
        print("⚠ 리포트에 마스킹 불확실 잔존 — 저장 보류. LLM 출력에 식별자 유입 여부 확인 필요.")
        return 2
    _RESULT_PATH.write_text(report + "\n", encoding="utf-8")
    print(f"리포트 저장: {_RESULT_PATH}")
    print(
        f"v1 통과 {v1_totals.passed}/{v1_totals.calls} · "
        f"v3 통과 {v2_totals.passed}/{v2_totals.calls}"
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="브리핑 품질 리뷰 v1 vs v3(실 LLM · 신호당 변형 2 × 반복 3)"
    )
    #: 🔴 **필수다 — 기본값을 안 둔다**(99 #256 · №113).
    #: ⚠ 🔴 `labels_llm_smoke` 는 `--max-calls` 에 기본 20 을 두는데, 🔴 **그 러너는
    #: 전량이 20 이라 「기본값 = 전량」이고 사실상 아무것도 안 막는다.** 여기서 기본
    #: 66 을 두면 같은 일이 된다 — **이번 사고가 정확히 「막는 것이 없어서」 났다**
    #: (로그 221: `.env` 의 `CHECKON_ALLOW_REAL_LLM=1` 이 어떤 기동 방식으로도 산다).
    #: 🔴 필수로 해도 **깨질 호출부가 없다** — 전수 확인: 이 러너를 코드에서 부르는
    #: 곳은 없고 CLI 뿐이다(검사들은 `_breakdown`·`_scrub` 만 import 한다).
    #: 🔴 그리고 `--max-calls 0` 이 **이번에 필요했던 그 동작**이다 — 0콜로 배선만 본다.
    parser.add_argument(
        "--max-calls",
        type=int,
        required=True,
        help="총 실 LLM 콜 상한(필수) — 전량은 66. `0` 이면 0콜로 배선만 확인한다",
    )
    args = parser.parse_args()
    if args.max_calls < 0:
        raise SystemExit("--max-calls 는 0 이상이어야 한다")
    raise SystemExit(run(args.max_calls))


if __name__ == "__main__":
    main()
