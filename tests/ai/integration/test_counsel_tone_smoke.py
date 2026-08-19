"""🔴 **tone_map 24조합이 실 LLM에서 실제로 갈리는가** — 아무도 안 본 자리.

골든(`test_counsel_prompt_snapshots.py`)은 **부분 문자열 단언**이라 *"프롬프트에 그 키가
들어갔나"* 까지만 본다. `FakeCounselProvider` 는 톤을 **안 쓴다**(고정 문면). ⇒ 「라벨 4축이
문체를 가른다」는 이 제품의 핵심 차별점인데 **프롬프트까지만 검증됐고 산출물은 아무도 안
봤다.** 99 #56이 브리핑 축에서 정확히 같은 자리를 밟았다.

🔴 **이 파일은 「측정 장치」다** — `CHECKON_ALLOW_REAL_LLM=1` 없이는 skip 하고,
기본 회귀·`pre_pr_verify` 에서 자동으로 **안 돈다**(`-m 'not integration'`).

🔴 **본문을 저장소에 남기지 않는다**(99 #80) — 집계만 낸다. 문면은 사람이 화면으로 보고
**판정을 검사·등재로 환원**한 뒤 버린다. ⚠ 입력이 전부 가명이라 실명 위험이 실제로는
0인데도 유지한다: fail-closed 규칙의 값은 **「매번 판단하지 않아도 되는 것」**이다.

⚠ 이미 있는 `src/ai/evaluation/counsel_llm_smoke.py`(S1~S5)와 **겹치지 않는다** —
그쪽은 e2e·refine 공격·기록 무결성·재현성을 보고, **24조합 스윕은 0건**이다(실측 8/19).
"""

from __future__ import annotations

import itertools
import json
import os
import time
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

import pytest

from ai.composition.counsel.assembly import build_counsel_gateway
from ai.composition.counsel.gate import check_counsel_gate
from ai.composition.counsel.provider import (
    GatewayDraftWriter,
    GatewayPlanner,
    max_chars_for,
    min_chars_for,
)
from ai.contracts.composition import (
    CommStyle,
    DraftContext,
    EvidenceFact,
    Frequency,
    Interest,
    LabelSnapshot,
    Sensitivity,
)
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.llm.providers.openai_compat import get_llm_settings
from ai.runtime.draft_observation import ORIGIN_DRAFT, observe_gated_draft
from ai.runtime.real_llm import real_llm_skip_reason
from ai.runtime.redaction import redact

pytestmark = pytest.mark.integration

#: 🔴 산출물은 **저장소 밖**에 쓴다 — 본문이 레포로 오면 안 된다(99 #80).
_OUT_DIR: Final = Path(
    os.environ.get("COUNSEL_TONE_SMOKE_OUT", "/tmp/counsel_tone_smoke")
)

#: fact 요약문 — 🔴 **PR-07 의 D 목록(99 #104) 통과분에서 고른다.** `redact()` 오탐이
#: 이미 재진 문면이라 「마스킹에 걸려서 실패한 것」과 「톤이 안 갈린 것」이 안 섞인다.
_FACTS: Final = (
    ("le_2041", "6월 지문 42개·312문항"),
    ("le_2077", "제출률 100% (4주)"),
    ("le_2101", "최근 4주 정답률 평균 81%"),
)
_PERIOD: Final = "2026년 7월"


def _context(snapshot: LabelSnapshot) -> DraftContext:
    """🔴 **라벨만 바꾸고 나머지 입력은 동일하다** — 안 그러면 「톤이 갈린 것」과
    「입력이 달랐던 것」이 안 갈린다."""
    return DraftContext(
        student_ref="st_tone",
        guardian_ref="pa_tone",
        label_snapshot=snapshot,
        facts=tuple(
            EvidenceFact(label="근거", value=summary, record_id=rid)
            for rid, summary in _FACTS
        ),
        evidence_summaries=(),
        period_label=_PERIOD,
        fallback_text="이번 기간 학습 상황을 정리해 보내드립니다.",
    )


def _execution_context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=uuid4(),
        tenant_id="t_tone_smoke",
        capability=Capability.COMPOSITION,
        input_snapshot_hash="sha256:" + "0" * 64,
        versions=VersionSet(
            pipeline_version="0.1",
            engine_version="counsel-pack-0.1",
            schema_version="0.1",
            contract_version="0.1",
        ),
    )


def _combinations() -> list[LabelSnapshot]:
    """4축 데카르트곱 — 로더가 **24와 정확히 일치**를 fail-closed로 강제한다."""
    combos = [
        LabelSnapshot(comm=c, sensitivity=s, interest=i, frequency=f)
        for c, s, i, f in itertools.product(
            CommStyle, Sensitivity, Interest, Frequency
        )
    ]
    assert len(combos) == 24, len(combos)
    return combos


def _label(snapshot: LabelSnapshot) -> str:
    return "·".join(
        (
            snapshot.comm.value,
            snapshot.sensitivity.value,
            snapshot.interest.value,
            snapshot.frequency.value,
        )
    )


@pytest.fixture(scope="module")
def _out() -> Iterator[Path]:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    yield _OUT_DIR


async def _one(
    writer: GatewayDraftWriter,
    planner: GatewayPlanner,
    snapshot: LabelSnapshot,
) -> dict[str, Any]:
    """한 조합 1건 — plan 1콜 + write 1콜(+ 게이트 재생성)."""
    context = _context(snapshot)
    execution_context = _execution_context()
    started = time.monotonic()

    plan_outcome = "ok"
    #: 🔴 **(2차) 콜당 지연을 나눠 잰다** — 1차 원자료가 plan+write **합**이라 콜당 p95 를
    #: 못 냈고, 그래서 `openai_timeout_s=45` 의 근거가 「잡당 max 의 1.8배」라는 **어림**에
    #: 머물렀다(99 #106). 이 숫자가 그 어림을 실수로 바꾼다.
    plan_seconds = 0.0
    write_seconds: list[float] = []
    plan_started = time.monotonic()
    try:
        emphasis_map = await planner.plan(
            contexts={context.student_ref: context},
            student_refs=[context.student_ref],
            execution_context=execution_context,
        )
        emphasis = tuple(emphasis_map.get(context.student_ref, ()))
    except Exception as exc:  # noqa: BLE001 — 사유를 집계에 남긴다
        plan_outcome = type(exc).__name__
        emphasis = ()
    finally:
        plan_seconds = round(time.monotonic() - plan_started, 2)

    max_chars = max_chars_for(context)
    min_chars = min_chars_for(context)
    feedback = ""
    attempts = 0
    reasons: list[str] = []
    text = ""
    failure = ""
    #: 재생성 상한은 라우터와 같다(3) — 여기서 리터럴을 새로 정하지 않는다.
    from ai.api.routers.counsel import _REGEN_MAX  # noqa: PLC0415

    for _ in range(_REGEN_MAX + 1):
        attempts += 1
        write_started = time.monotonic()
        try:
            text = await writer.write(
                context=context,
                execution_context=execution_context,
                emphasis=emphasis,
                gate_feedback=feedback,
            )
        except Exception as exc:  # noqa: BLE001
            failure = type(exc).__name__
            #: ⚠ **죽은 호출의 지연도 센다** — 타임아웃이 몇 초에 끊겼는지가 상한 판정의
            #:   재료다. 성공분만 세면 «45로 올렸더니 안 죽는다»의 근거가 반쪽이다.
            write_seconds.append(round(time.monotonic() - write_started, 2))
            break
        write_seconds.append(round(time.monotonic() - write_started, 2))
        gate = check_counsel_gate(
            text, context, max_chars=max_chars, min_chars=min_chars
        )
        if gate.passed:
            break
        reasons.append(gate.reason)
        from ai.composition.gate_feedback import instruction_for  # noqa: PLC0415

        feedback = instruction_for(gate.reason.partition(":")[0])
    else:
        failure = "gate_exhausted"

    hits = (
        observe_gated_draft(
            text,
            origin=ORIGIN_DRAFT,
            tenant_id=execution_context.tenant_id,
            execution_id=str(execution_context.execution_id),
        )
        if text and not failure
        else ()
    )
    return {
        "labels": _label(snapshot),
        "plan_outcome": plan_outcome,
        "emphasis_n": len(emphasis),
        "attempts": attempts,
        "gate_reasons": reasons,
        "failure": failure,
        "chars": len(text.strip()),
        "max_chars": max_chars,
        "min_chars": min_chars,
        #: 🔴 **본문은 집계에만 쓰고 반환하지 않는다** — 호출자가 파일로 쓸 때만 싣는다.
        "buffer_hits": list(hits),
        "output_uncertain": bool(text) and redact(text).uncertain,
        "seconds": round(time.monotonic() - started, 2),
        #: 🔴 콜당 분해(2차 신설) — `seconds` 는 합이라 콜당 p95 를 못 낸다.
        "plan_seconds": plan_seconds,
        "write_seconds": write_seconds,
        "_text": text,
    }


def _providers() -> tuple[GatewayDraftWriter, GatewayPlanner]:
    from ai.llm.providers.openai_compat import (  # noqa: PLC0415
        build_openai_compat_provider,
    )

    #: 🔴 **중앙 실 client 관문을 지난다** — provider 를 직접 만들면 opt-in 가드·재시도
    #: 배선을 우회한다(99 #32 가 그 사고다).
    gateway = build_counsel_gateway(build_openai_compat_provider())
    return GatewayDraftWriter(gateway), GatewayPlanner(gateway)


def _skip_unless_opted_in() -> None:
    reason = real_llm_skip_reason(get_llm_settings().openai_base_url)
    if reason is not None:
        pytest.skip(reason)


def _write_report(out: Path, name: str, rows: Sequence[dict[str, Any]]) -> None:
    """🔴 **본문은 `/tmp` 에만** — 집계는 옆 파일로 나눠 사람이 읽는다."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    bodies = [{"labels": r["labels"], "text": r["_text"]} for r in rows]
    (out / f"{name}.{stamp}.bodies.json").write_text(
        json.dumps(bodies, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary = [{k: v for k, v in r.items() if k != "_text"} for r in rows]
    (out / f"{name}.{stamp}.summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def test_b1_the_twenty_four_tone_combinations(_out: Path) -> None:
    """B-1 · 24조합 × 1건 — 🔴 **전수다.** 6조합만 재면 18조합이 미검증인데
    「톤을 봤다」로 읽힌다.
    """
    import asyncio  # noqa: PLC0415

    _skip_unless_opted_in()
    writer, planner = _providers()

    async def run() -> list[dict[str, Any]]:
        return [await _one(writer, planner, s) for s in _combinations()]

    rows = asyncio.run(run())
    _write_report(_out, "b1_tone_sweep", rows)

    assert len(rows) == 24
    #: 🔴 **판정은 사람이 한다** — 여기서는 「전부 죽지 않았다」만 절단 가드로 본다.
    alive = [r for r in rows if not r["failure"]]
    assert alive, f"24조합이 전부 실패했다: {[r['failure'] for r in rows]}"


def test_b2_reproducibility_of_one_combination(_out: Path) -> None:
    """B-2 · 기본 조합 × 3건 — **입력이 완전히 동일**하다."""
    import asyncio  # noqa: PLC0415

    _skip_unless_opted_in()
    writer, planner = _providers()
    snapshot = LabelSnapshot(
        comm=CommStyle.NARRATIVE,
        sensitivity=Sensitivity.ANXIOUS,
        interest=Interest.GRADE,
        frequency=Frequency.MONTHLY,
    )

    async def run() -> list[dict[str, Any]]:
        return [await _one(writer, planner, snapshot) for _ in range(3)]

    rows = asyncio.run(run())
    _write_report(_out, "b2_reproducibility", rows)

    assert len(rows) == 3


# ── 2차 회차 · 빈도 축 (#79 판정 재료) ────────────────────────────


def test_c1_frequency_sample_of_thirty(_out: Path) -> None:
    """🔴 **2차 · 같은 조합 × 30건** — B군 빈도의 상한을 좁힌다.

    1차는 **상한만** 냈다: 적중 0건 · n=27 이면 참 비율의 95% 상한이 약 **11%** 다.
    #79 가 필요한 판정은 *"게이트에 넣으면 재생성이 터지나"* = **「5% 미만인가」**인데
    11% 로는 못 가른다.

        n=27 · 0건 → 상한 약 11%     (1차)
        n=57 · 0건 → 상한 약 5.2%    (1차 + 2차 합산)

    ⇒ **30건이면 충분하고 그 이상은 이 판정에 필요 없다.**

    🔴 **입력을 1차와 완전히 동일하게 둔다** — 합산해서 세려면 같은 분포여야 한다.
    조합도 1차 B-2 와 같은 기본 조합이다.
    ⚠ **톤 24조합은 다시 안 돈다** — 1차에서 완결됐다(24/24 서로 다름 ·
    `anxious` 12/12 인사 vs `direct` 0/12).
    """
    import asyncio  # noqa: PLC0415

    _skip_unless_opted_in()
    writer, planner = _providers()
    snapshot = LabelSnapshot(
        comm=CommStyle.NARRATIVE,
        sensitivity=Sensitivity.ANXIOUS,
        interest=Interest.GRADE,
        frequency=Frequency.MONTHLY,
    )

    async def run() -> list[dict[str, Any]]:
        return [await _one(writer, planner, snapshot) for _ in range(30)]

    rows = asyncio.run(run())
    _write_report(_out, "c1_frequency_30", rows)

    assert len(rows) == 30
    #: 🔴 **절단 가드** — 전부 죽으면 빈도를 못 센다(중단 규칙: timeout 사망 0건이어야 한다).
    alive = [r for r in rows if not r["failure"]]
    assert alive, f"30건이 전부 실패했다: {[r['failure'] for r in rows][:5]}"
