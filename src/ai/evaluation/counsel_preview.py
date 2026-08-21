"""상담 초안 프롬프트 **A/B 미리보기** — 실 LLM (박진희 · A · 평가 격리).

🔴 **목적은 판정이 아니라 「나란히 보기」다.** 프롬프트를 고쳤을 때 무엇이 달라지는지
사람이 읽고 정하려면 **같은 컨텍스트의 두 출력이 한 화면에 있어야 한다.** 기억으로
비교하면 *"나아진 것 같다"* 밖에 안 남는다.

⚠ **`counsel_llm_smoke.py` 와 다른 도구다.** 그쪽은 *"게이트·프롬프트를 손대지 않는다"* 는
실측 하네스이고, 이쪽은 **프롬프트를 바꿔 가며 보는** 도구다. 합치지 마라 — 합치면
스모크가 재는 「현행」이 무엇인지 흔들린다.

🔴 **원본 템플릿을 안 고친다.** 후보는 `--candidate <경로>` 로 받아 `_template()` 만
갈아끼운다. 확정 전까지 `counsel_pack.txt` 와 `PROMPT_VERSION` 은 그대로다.

🔴 **초안 본문을 파일로 남기지 않는다**(99 #80). 표준출력으로만 나간다.

실행:
    LLM_PROVIDER=openai_compat uv run python -m ai.evaluation.counsel_preview \
        --candidate _to_delete/counsel_pack.candidate.txt

    # 현행만 보고 싶으면 --candidate 를 빼면 된다 (호출 수 절반)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final
from unittest.mock import patch
from uuid import UUID

from ai.composition.counsel import prompt as counsel_prompt
from ai.composition.counsel.gate import check_counsel_gate
from ai.composition.counsel.provider import max_chars_for, min_chars_for
from ai.composition.counsel.settings import LLM_CALL_TIMEOUT_S
from ai.composition.tone import combination_key
from ai.contracts.composition import DraftContext, EvidenceFact, LabelSnapshot
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import LlmError, LLMProvider, LLMRequest, ModelRole
from ai.runtime.redaction import redact

#: 🔴 성격이 갈리게 넷 — 가장 긴 조합(4문장×4블록)과 가장 짧은 조합(2문장×3블록)이 다 든다.
_DEFAULT_COMBOS: Final = (
    "data.anxious.grade.monthly",
    "narrative.direct.attitude.monthly",
    "data.direct.grade.frequent",
    "narrative.anxious.admission.monthly",
)

#: 🔴 블록 라벨이 본문에 새는지 본다 — tone_map 의 `blocks` 값이 소제목으로 나오면
#:   그것이 「기계적」의 직접 증거다.
#: ⚠ **손으로 박은 목록을 쓰지 않는다.** 실측 2026-08-20: 네 단어(`인사|제안|근거|결론`)만
#:   박아 뒀더니 「기간 수치표입니다.」를 **놓쳤다** — 대상은 샜는데 감지기가 없다고 말했다
#:   (99 #140 ⓓ · 측정 장치가 고장 났다). ⇒ 그 조합의 `blocks` 에서 만든다.

_SENT_END: Final = re.compile(r"[.!?…]+\s*")


@dataclass
class _Metrics:
    paragraphs: int
    sentences_per_paragraph: list[int]
    label_leak: list[str]
    chars: int
    gate_passed: bool
    gate_reason: str
    latency_ms: int


@dataclass
class _Totals:
    calls: int = 0
    failures: int = 0
    gate_blocked: int = 0
    reasons: list[str] = field(default_factory=list)


def _context() -> ExecutionContext:
    """미리보기 러너의 표식 — 🔴 **프로덕션 버전 세트를 안 쓴다**(briefing_preview 와 같은 판단).

    이 값은 「이 코드가 어느 버전이었나」가 아니라 「미리보기였다」는 표시다.
    """
    return ExecutionContext(
        execution_id=UUID("00000000-0000-4000-8000-0000000000c0"),
        tenant_id="tn_counsel_preview",
        capability=Capability.COMPOSITION,
        input_snapshot_hash="counsel-preview",
        versions=VersionSet(
            pipeline_version="0.1",
            engine_version="0.1",
            schema_version="0.1",
            contract_version="0.1",
            prompt_version=counsel_prompt.PROMPT_VERSION,
        ),
    )


#: 시나리오 A — 종전 유일본. 🔴 **수치다운 것이 「정답률」 하나다.**
#: 그래서 세 `interest`(grade·attitude·admission)가 집을 것이 하나뿐이고,
#: v0.7 24조합 전수에서 셋이 전부 정답률을 주력으로 썼다(№41 §1).
_FACTS_A: Final = (
    EvidenceFact(label="지문 학습량", value="6월 42지문 312문항", record_id="le_2041"),
    EvidenceFact(label="제출률", value="최근 4주 100%", record_id="le_2077"),
    EvidenceFact(label="추론 문항 정답률", value="62% → 71%", record_id="le_2103"),
)
_SUMMARIES_A: Final = (
    "6월 한 달 지문 42개·312문항을 풀었다",
    "최근 4주 과제 제출률 100%",
    "추론 문항 정답률이 62%에서 71%로 올랐다",
)

#: 🔴 시나리오 B — **축마다 집을 것이 따로 있게** 짠다(№41 §3-3).
#:
#:     attitude  →  과제 제출 두 창(직전 10/10 · 최근 4/10)
#:     grade     →  지문당 풀이 시간 두 달(4분 · 7분) · 학습량 감소
#:     admission →  🔴 **없다.** 점수·석차·반 평균이 불변식 7 과 프롬프트 「지켜야 할
#:                  선」 양쪽에서 금지돼 「목표 대비 위치」를 말할 재료가 계약에 없다.
#:                  ⇒ admission 이 안 갈리면 그것은 프롬프트 탓도 시나리오 탓도 아니다.
#:
#: ⚠ 🔴 **정답률을 일부러 뺐다** — A 와 같은 것이 있으면 세 축이 또 거기로 몰린다.
#: ⚠ 🔴 **파생값(「4분 → 7분」)을 fact 로 만들지 않았다.** 두 시점을 **각자의
#:   `record_id` 를 가진 두 fact** 로 둔다 — 계약이 *"집계·기준선 파생 fact 는 `None`"*
#:   이라 파생으로 쓰면 인용이 막히고, 프롬프트도 *"두 수치를 빼서 새 숫자를 만들지
#:   마세요"* 라 **뺄셈은 애초에 모델이 할 일이 아니다.**
_FACTS_B: Final = (
    EvidenceFact(label="직전 4주 과제 제출", value="10건 중 10건", record_id="le_3009"),
    EvidenceFact(label="최근 4주 과제 제출", value="10건 중 4건", record_id="le_3101"),
    EvidenceFact(label="6월 지문당 풀이 시간", value="평균 4분", record_id="le_3016"),
    EvidenceFact(label="7월 지문당 풀이 시간", value="평균 7분", record_id="le_3115"),
    EvidenceFact(label="7월 지문 학습량", value="12지문 96문항", record_id="le_3120"),
)
_SUMMARIES_B: Final = (
    "직전 4주에는 과제 10건을 모두 제출했다",
    "최근 4주에는 과제 10건 중 4건을 제출했다",
    "지문당 풀이 시간이 6월 평균 4분, 7월 평균 7분이다",
    "7월 지문 학습량은 12지문 96문항이다",
)

_SCENARIOS: Final = {
    "a": (_FACTS_A, _SUMMARIES_A, "요즘 아이가 잘 하고 있는지 궁금합니다."),
    "b": (_FACTS_B, _SUMMARIES_B, "지난달보다 힘들어하는 것 같아서 여쭤봅니다."),
}


def _draft_context(combo: str, scenario: str = "a") -> DraftContext:
    """조합 키 하나로 컨텍스트를 짓는다 — 🔴 **근거는 네 축에서 같다.**

    ⚠ 근거를 조합마다 바꾸면 «프롬프트가 바꾼 것»과 «입력이 바꾼 것»이 안 갈린다.
    🔴 **그래서 시나리오는 `combo` 가 아니라 명시 인자로만 바뀐다** — 한 번의 실행 안에서는
    모든 조합이 같은 근거를 본다(№41 §2-1).
    """
    comm, sensitivity, interest, frequency = combo.split(".")
    facts, summaries, inquiry = _SCENARIOS[scenario]
    return DraftContext(
        student_ref="st_preview",
        guardian_ref="pa_preview",
        label_snapshot=LabelSnapshot(
            comm=comm, sensitivity=sensitivity, interest=interest, frequency=frequency
        ),
        facts=facts,
        evidence_summaries=summaries,
        period_label="2026년 7월",
        fallback_text="이번 기간 학습 상황을 정리해 전해 드립니다.",
        inquiry_text=inquiry,
    )


def _leaks_label(paragraph: str, blocks: Sequence[str]) -> bool:
    """이 문단이 블록 라벨로 시작하는가 — 🔴 라벨은 **그 조합의 blocks 에서 온다**."""
    head = paragraph.splitlines()[0].strip().lstrip("[(<【# ").strip()
    return any(head.startswith(label) for label in blocks)


def _measure(
    text: str, context: DraftContext, latency_ms: int, blocks: Sequence[str]
) -> _Metrics:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    per_paragraph = [
        len([s for s in _SENT_END.split(p) if s.strip()]) for p in paragraphs
    ]
    leaks = [p.splitlines()[0][:28] for p in paragraphs if _leaks_label(p, blocks)]
    gate = check_counsel_gate(
        text,
        context,
        max_chars=max_chars_for(context),
        min_chars=min_chars_for(context),
    )
    return _Metrics(
        paragraphs=len(paragraphs),
        sentences_per_paragraph=per_paragraph,
        label_leak=leaks,
        chars=len(text),
        gate_passed=gate.passed,
        gate_reason="" if gate.passed else gate.reason,
        latency_ms=latency_ms,
    )


async def _generate(
    provider: LLMProvider, prompt_text: str, totals: _Totals
) -> tuple[str, int]:
    """한 번 생성 — 🔴 **전송 직전 redaction**(프로덕션과 같은 이중 방어)."""
    request = LLMRequest(
        role=ModelRole.GENERATOR,
        prompt=redact(prompt_text).masked_text,
        prompt_id=counsel_prompt.PROMPT_ID,
        prompt_version=counsel_prompt.PROMPT_VERSION,
    )
    totals.calls += 1
    start = time.monotonic()
    try:
        result = await provider.complete(request, _context())
    except LlmError as exc:
        totals.failures += 1
        return f"[LLM 실패] {type(exc).__name__}", int((time.monotonic() - start) * 1000)
    return (result.text or "").strip(), int((time.monotonic() - start) * 1000)


def _render(name: str, text: str, metrics: _Metrics) -> None:
    print(f"\n  ── {name} " + "─" * (58 - len(name)))
    print(
        f"     문단 {metrics.paragraphs} · 문단별 문장 {metrics.sentences_per_paragraph}"
        f" · {metrics.chars}자 · {metrics.latency_ms}ms"
    )
    leak = ", ".join(metrics.label_leak) if metrics.label_leak else "없음"
    gate = "통과" if metrics.gate_passed else f"거부({metrics.gate_reason})"
    print(f"     🔴 라벨 누출: {leak}   ·   게이트: {gate}")
    print()
    for line in text.splitlines():
        print(f"     {line}")


def _build_provider(provider_name: str) -> LLMProvider:
    """🔴 **counsel 의 콜당 상한(90s)을 준다** — 브리핑 상한(15s)이 아니다.

    ⚠ 실측 2026-08-20: 처음엔 `build_brief_provider` 를 썼는데 그건 **브리핑 전용 15s** 를
    주입한다(`composition/provider.py`). 상담 초안은 브리핑보다 길어서 8콜 중 3콜이
    `LlmTimeout` 이었다 — 🔴 **프롬프트 품질이 아니라 측정 장치의 상한에 걸린 것**이고,
    프로덕션 counsel 은 90s 라 안 걸린다(99 #140 ⓓ).
    ⚠ `model_copy` 로 **타임아웃만** 바꾼다 — 키·URL·모델을 이 파일이 다시 읽지 않는다.
    """
    if provider_name != "openai_compat":
        from ai.composition.provider import (  # noqa: PLC0415 — fake 경로만
            BriefingSettings,
            build_brief_provider,
        )

        return build_brief_provider(BriefingSettings(llm_provider=provider_name))
    from ai.llm.providers.openai_compat import (  # noqa: PLC0415 — 벤더 지연 import
        build_openai_compat_provider,
        get_llm_settings,
    )

    return build_openai_compat_provider(
        settings=get_llm_settings().model_copy(
            update={"openai_timeout_s": float(LLM_CALL_TIMEOUT_S)}
        )
    )


async def _run(
    combos: tuple[str, ...],
    candidate: Path | None,
    runs: int,
    provider_name: str,
    *,
    only_candidate: bool = False,
    scenario: str = "a",
) -> int:
    provider = _build_provider(provider_name)
    totals = _Totals()
    candidate_text = candidate.read_text(encoding="utf-8") if candidate else None

    for combo in combos:
        context = _draft_context(combo, scenario)
        key = combination_key(**context.label_snapshot.as_axes())
        rule = counsel_prompt.tone_rule_for(context)
        print("\n" + "=" * 72)
        print(f"■ {key}   [시나리오 {scenario.upper()}]")
        print(
            f"  blocks={rule.blocks} · 문장/블록={rule.sentences_per_block}"
            f" · buffer={rule.buffer_level}"
        )

        for attempt in range(1, runs + 1):
            suffix = f" #{attempt}" if runs > 1 else ""
            #: 🔴 A 를 건너뛸 수 있다 — 현행의 결함이 이미 확정된 뒤에는 비교가 필요 없다.
            if not only_candidate:
                base_prompt = counsel_prompt.assemble_prompt(context)
                text, ms = await _generate(provider, base_prompt, totals)
                metrics = _measure(text, context, ms, rule.blocks)
                if not metrics.gate_passed:
                    totals.gate_blocked += 1
                    totals.reasons.append(f"A/{key}/{metrics.gate_reason}")
                _render(f"A 현행{suffix}", text, metrics)

            if candidate_text is None:
                continue
            #: 🔴 원본 파일을 안 고친다 — `_template()` 만 후보로 갈아끼운다.
            with patch.object(counsel_prompt, "_template", lambda: candidate_text):
                cand_prompt = counsel_prompt.assemble_prompt(context)
            text_b, ms_b = await _generate(provider, cand_prompt, totals)
            metrics_b = _measure(text_b, context, ms_b, rule.blocks)
            if not metrics_b.gate_passed:
                totals.gate_blocked += 1
                totals.reasons.append(f"B/{key}/{metrics_b.gate_reason}")
            _render(f"B 후보{suffix}", text_b, metrics_b)

    print("\n" + "=" * 72)
    print(
        f"실 LLM {totals.calls}콜 · 실패 {totals.failures} · 🔴 게이트 거부 {totals.gate_blocked}"
    )
    for reason in totals.reasons:
        print(f"  · {reason}")
    #: 🔴 게이트 거부는 **실패가 아니다**(불변식 4) — 종료 코드로 만들지 않는다.
    return 1 if totals.failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="상담 초안 프롬프트 A/B 미리보기(실 LLM)"
    )
    parser.add_argument(
        "--combos",
        default=",".join(_DEFAULT_COMBOS),
        help="쉼표로 구분한 조합 키 (기본: 성격이 갈리는 넷)",
    )
    parser.add_argument(
        "--candidate",
        type=Path,
        default=None,
        help="후보 템플릿 경로 — 🔴 없으면 현행(A)만 돈다",
    )
    parser.add_argument("--runs", type=int, default=1, help="조합당 반복 (기본 1)")
    parser.add_argument(
        "--only-candidate",
        action="store_true",
        help="🔴 A(현행)를 건너뛴다 — 호출 수 절반",
    )
    parser.add_argument(
        "--scenario",
        choices=sorted(_SCENARIOS),
        default="a",
        help="🔴 근거 묶음 — a(종전) · b(정답률 없음 · 제출 하락 · 풀이 시간 증가)",
    )
    parser.add_argument(
        "--provider",
        #: 🔴 **저장소 관례를 따른다 — `LLM_PROVIDER` 가 있으면 그것이 기본값이다.**
        #: ⚠ 종전에는 **이 도구만 안 따라서**, `LLM_PROVIDER=fake` 를 주고 배선을 증명한
        #: 한 번이 **실 LLM 1콜을 태웠다**(2026-08-21 실측). 저장소 전체가 그 env 로 fake 를
        #: 켜는데 여기만 하드코딩된 기본값을 썼다 — 그 파일 자신의 안내가
        #: *"먼저 `--provider fake` 로 배선을 증명하라"* 인데 **아무것도 강제하지 않았다.**
        default=os.environ.get("LLM_PROVIDER", "openai_compat"),
        help="🔴 먼저 `--provider fake` 로 배선을 증명하라 (실 LLM 0회) — "
        "`LLM_PROVIDER` 가 기본값이다",
    )
    args = parser.parse_args()
    combos = tuple(c.strip() for c in args.combos.split(",") if c.strip())
    sys.exit(
        asyncio.run(
            _run(
                combos,
                args.candidate,
                args.runs,
                args.provider,
                only_candidate=args.only_candidate,
                scenario=args.scenario,
            )
        )
    )


if __name__ == "__main__":
    main()
