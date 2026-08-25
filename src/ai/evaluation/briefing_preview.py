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
#: 🔴 ⑤ 하한    : 🔴 **이 수는 「첫 시도」 통과율이고 프로덕션의 하한**이다 —
#:                `make_brief` 는 게이트 실패 시 `MAX_REGEN` 회 **재생성**하지만
#:                이 러너는 안 한다(`_assert_first_try_only` 가 그 사실을 못 박는다).
#:                ⚠ 🔴 **그걸 모르고 비교하면 «품질이 떨어졌다» 로 오독한다**(99 #217).
#:
#: ⚠ 🔴 **비교 대상이 없다** — 0.2 시절 통과율 **수치**가 저장소에 없다(#27 · 전수).
#: ⇒ 🔴 이 줄은 «비교» 가 아니라 **«기준선을 세운 것»** 이다. 다음 측정이 여기와 비교한다.
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


@dataclass
class _Totals:
    calls: int = 0
    passed: int = 0
    latencies: list[int] = field(default_factory=list)
    reasons: Counter[str] = field(default_factory=Counter)


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


async def _run_variant(
    provider: LLMProvider,
    prompt: str,
    allowed: frozenset[str],
    fallback: str,
    context: ExecutionContext,
    totals: _Totals,
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
        totals.calls += 1
        start = time.monotonic()
        try:
            result = await provider.complete(request, context)
            raw = (result.text or "").strip()
            gate = check_brief_gate(raw, allowed)
            latency = int((time.monotonic() - start) * 1000)
            if gate.passed:
                totals.passed += 1
            else:
                totals.reasons[gate.reason.split(":")[0]] += 1
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
            totals.reasons[f"llm:{type(exc).__name__}"] += 1
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
    for a1, a2 in zip(v1, v2, strict=True):
        v1v = "✅" if a1.gate_ok else f"❌ {a1.gate_reason}"
        v2v = "✅" if a2.gate_ok else f"❌ {a2.gate_reason}"
        lines.append(
            f"| {a1.index} | {_cell(a1.raw)} | {v1v} ({a1.latency_ms}ms) "
            f"| {_cell(a2.raw)} | {v2v} ({a2.latency_ms}ms) |"
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


def run() -> int:
    provider = build_brief_provider(BriefingSettings(llm_provider="openai_compat"))
    response = detect(build_demo_request())
    signals = list(response.signals)
    if not signals:
        print("데모 신호가 없습니다 — 스냅숏 구성을 확인하세요.")
        return 1
    contexts = build_contexts(build_demo_request(), signals)

    context = _context()
    v1_totals, v2_totals = _Totals(), _Totals()
    body: list[str] = []
    for signal in signals:
        ctx = contexts[signal.signal_id]
        v1 = asyncio.run(
            _run_variant(
                provider,
                _v1_prompt(signal),
                frozenset(_NUMBER_RE.findall(signal.brief.text)),
                signal.brief.text,
                context,
                v1_totals,
            )
        )
        v2 = asyncio.run(
            _run_variant(
                provider,
                _assemble_prompt(ctx),
                ctx.allowed_numbers(),
                ctx.fallback_text,
                context,
                v2_totals,
                #: 🔴 값을 복제하지 않고 **프로덕션 파라미터에서 읽는다**(99 #02) —
                #:   복제하면 프로덕션만 바뀌었을 때 프리뷰가 낡은 채로 초록이다.
                #:   2026-08-13 현재 None(천장 없음 · 99 #54).
                max_tokens=BRIEF_GEN_PARAMS.max_tokens,
            )
        )
        body.extend(_render_signal(signal, ctx, v1, v2))

    report = "\n".join(
        [
            "# 브리핑 문장 품질 리뷰 — v1(다듬기) vs v3(근거 작성·지연개선)",
            "",
            f"신호 {len(signals)}건 × {_REPEATS}회 × 2변형 = "
            f"{v1_totals.calls + v2_totals.calls}회 호출 · 현 프로덕션 게이트로 동일 판정 · "
            f"프롬프트·게이트 무수정(재생성 상한 {MAX_REGEN})",
            *body,
            "\n## 요약",
            "",
            *_summary("v1 다듬기(옛 프롬프트)", v1_totals),
            "",
            *_summary("v3 근거 작성(현 프롬프트·max_tokens=128·전체 15s)", v2_totals),
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
    argparse.ArgumentParser(description="브리핑 품질 리뷰 v1 vs v2(실 LLM 3회/신호)").parse_args()
    raise SystemExit(run())


if __name__ == "__main__":
    main()
