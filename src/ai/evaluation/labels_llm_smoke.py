"""라벨 제안 실 LLM 스모크 — 동기 1콜 축의 종단 실측 (99 #191 · №69).

소유: 박진희 (A · 평가 격리 — 프로덕션 경로 아님).

⚠ 🔴 **`counsel_llm_smoke.py` 를 복제하지 않았다** — counsel 은 잡·드레인·원장이 붙어
훨씬 크고, 라벨은 **동기 1콜**이다. 형태(인자·출력 표)만 따랐다.

**목적은 기능 추가가 아니라 실측이다.** 게이트·프롬프트는 스모크 통과를 위해 손대지
않는다 — 걸리면 걸린 대로 보고한다.

🔴 **재는 것 여섯**(순서가 곧 위험 순서다):
    ① **파서 드롭** — 형식이 틀린 줄. 🔴 전부 드롭되면 `suggestions: []` 가 나가고
      04 는 그것을 «게이트가 전량 드롭» 으로 정의한다 ⇒ **500 도 400 도 안 나는데 비어 있다**
    ② 게이트 드롭 — 인용 실존에서 떨어진 수(id 날조 · 인용 변형)
    ③ 최종 제안 수 · 축 분포
    ④ 소요 — p50 · p95 · **max**(`call_timeouts.yaml` 은 `max` 없이 값을 안 넣는다 · №58)
    ⑤ 이력 마스킹 제외 건수(99 #192 ⓑ · #193 계기)
    ⑥ 재시도 · 실패

🔴 **본문·인용문은 수만 남긴다**(불변식 3 · 99 #80). 원장이 필요하면 `local_data/` 로
(№58 선례 — 그 폴더는 추적되지 않는다).

실행:
    CHECKON_ALLOW_REAL_LLM=1 LLM_PROVIDER=openai_compat \\
      uv run --frozen python -m ai.evaluation.labels_llm_smoke --max-calls 8
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from ai.composition.counsel.versions import counsel_versions
from ai.composition.labels.grounding import ground_suggestions, keep_sendable_history
from ai.composition.labels.provider import (
    GatewayLabelSuggestProvider,
    build_label_gateway,
    merge_duplicate_axes,
)
from ai.contracts.execution import Capability, ExecutionContext
from ai.contracts.labels import HistoryItem

_FIXTURE: Final = Path(
    "tests/ai/contract/fixtures/http/post_labels_suggest.request.json"
)

#: 🔴 **새 실명을 창작하지 않는다** — 계약 픽스처 + 8/21·8/22 표본에서만 온다.
#: ⚠ 이 이력들은 **저장소에 이미 있는 문면**이라 새로 남기는 개인정보가 없다.
_EXTRA_HISTORY: Final = (
    "아이가 요즘 숙제를 힘들어합니다",
    "다음 상담은 언제 가능할까요",
    "집에서 뭘 도와주면 좋을까요",
    "지난달보다 나아졌는지 궁금해요",
    "결석하면 보충이 되나요",
)


@dataclass
class Run:
    """한 콜의 결과 — 🔴 본문 없음."""

    latency_ms: int
    raw_lines: int
    parsed: int
    grounded: int
    merged: int
    """🔴 **병합 뒤** — 라우터가 실제로 내는 수다(99 #216)."""
    dropped_history: int
    axes: tuple[str, ...]
    parser_drops: int = 0
    """🔴 형식이 틀려 버려진 줄 수 — `parse_suggestions` 가 로그로만 남긴다."""
    signature: tuple[tuple[str, str], ...] = ()
    """🔴 결정론 비교용 — (축, 값) 쌍. **인용문은 안 담는다**(불변식 3)."""
    error: str | None = None


@dataclass
class Totals:
    runs: list[Run] = field(default_factory=list)

    def add(self, run: Run) -> None:
        self.runs.append(run)


def _history(texts: tuple[str, ...]) -> tuple[HistoryItem, ...]:
    return tuple(
        HistoryItem(
            record_id=f"cm_{index}",
            direction="inbound",
            text=text,
            at=datetime(2026, 6, 12, 10, 11, tzinfo=UTC),
        )
        for index, text in enumerate(texts, start=88)
    )


def _scenarios() -> list[tuple[str, tuple[HistoryItem, ...]]]:
    """🔴 입력을 지어내지 않는다 — 계약 픽스처가 1번이다."""
    fixture_texts = tuple(
        item["text"] for item in json.loads(_FIXTURE.read_text())["history"]
    )
    return [
        ("계약 픽스처(수치 요구)", _history(fixture_texts)),
        ("문의 표본(서술 요구)", _history(_EXTRA_HISTORY)),
    ]


def _context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=uuid.uuid4(),
        tenant_id="smoke",
        capability=Capability.COMPOSITION,
        input_snapshot_hash="guardian:smoke",
        versions=counsel_versions(),
    )


class _DropCounter(logging.Handler):
    """🔴 파서 드롭은 로그로만 나온다 — 그걸 **세는 자리**를 만든다(로그 59).

    ⚠ 본문은 안 읽는다. `수=N` 만 센다(불변식 3 · 99 #80).
    """

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.dropped = 0

    def emit(self, record: logging.LogRecord) -> None:
        if "라벨 제안 줄 드롭" in record.getMessage():
            args = record.args
            if isinstance(args, tuple) and len(args) >= 2:
                self.dropped += int(str(args[1]))


async def _one(
    provider: GatewayLabelSuggestProvider, history: tuple[HistoryItem, ...]
) -> Run:
    counter = _DropCounter()
    parser_logger = logging.getLogger("ai.composition.labels.provider")
    parser_logger.addHandler(counter)
    sendable, dropped = keep_sendable_history(history)
    started = time.monotonic()
    try:
        raw = await provider.suggest(
            guardian_ref="gd_smoke", history=history, context=_context()
        )
    except Exception as error:  # noqa: BLE001 — 스모크는 실패도 수치다
        parser_logger.removeHandler(counter)
        return Run(
            latency_ms=int((time.monotonic() - started) * 1000),
            raw_lines=0,
            parsed=0,
            grounded=0,
            merged=0,
            dropped_history=len(dropped),
            axes=(),
            parser_drops=counter.dropped,
            error=type(error).__name__,
        )
    parser_logger.removeHandler(counter)
    latency = int((time.monotonic() - started) * 1000)
    outcome = ground_suggestions(raw, history=sendable or history)
    #: 🔴 **병합까지 태운다**(2026-08-24 · 99 #216) — 종전에는 여기서 멈춰서 «최종 제안»
    #: 이 라우터가 실제로 내는 수가 **아니었다.** 실측 8/24: 12콜 중 **2콜**이 같은
    #: `(축, 값)` 을 두 번 냈고 그 회차 보고의 «최종 19» 는 병합 뒤 **17** 이 맞았다.
    #: ⚠ 🔴 대역이 아니라 **측정기**가 층을 건너뛴 것이다 — #208 과 같은 결이다.
    merged = merge_duplicate_axes(outcome.suggestions)
    return Run(
        latency_ms=latency,
        raw_lines=len(raw),
        parsed=len(raw),
        grounded=len(outcome.suggestions),
        merged=len(merged),
        dropped_history=len(dropped),
        axes=tuple(s.label.axis for s in outcome.suggestions),
        parser_drops=counter.dropped,
        signature=tuple(
            sorted((s.label.axis, s.label.value) for s in outcome.suggestions)
        ),
    )


def _quantile(values: list[int], point: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * point))]


def _report(totals: Totals) -> None:
    runs = totals.runs
    ok = [r for r in runs if r.error is None]
    print("\n━━ 라벨 실 LLM 스모크 결과 ━━")
    print(f"콜 {len(runs)} · 성공 {len(ok)} · 실패 {len(runs) - len(ok)}")
    parsed = sum(r.parsed for r in ok)
    grounded = sum(r.grounded for r in ok)
    drops = sum(r.parser_drops for r in runs)
    print(f"① 🔴 파서 드롭 {drops}  (통과 줄 {parsed})")
    merged = sum(r.merged for r in ok)
    print(f"② 게이트 드롭 {parsed - grounded}  (파서 {parsed} → 게이트 {grounded})")
    #: 🔴 병합 감소 — 같은 `(축, 값)` 중복(99 #204)이 실물에서 얼마나 나오나.
    print(f"③ 병합 감소 {grounded - merged}  (게이트 {grounded} → 병합 {merged})")
    print(f"④ 최종 제안 {merged} · 축 분포 {dict(Counter(a for r in ok for a in r.axes))}")
    if ok:
        latencies = [r.latency_ms for r in ok]
        print(
            f"⑤ 소요 p50 {_quantile(latencies, 0.5)}ms · "
            f"p95 {_quantile(latencies, 0.95)}ms · max {max(latencies)}ms "
            f"(mean {round(statistics.mean(latencies))}ms)"
        )
    print(f"⑥ 이력 마스킹 제외 {sum(r.dropped_history for r in runs)}건")
    errors = Counter(r.error for r in runs if r.error)
    print(f"⑦ 실패 {dict(errors) if errors else '없음'}")
    #: 🔴 §E 결정론 — **같은 이력을 두 번** 태워 (축, 값) 쌍이 같은가.
    #: ⚠ `deterministic_params()` 가 보증하는 것은 «요청이 안 흔들린다» 까지다
    #:   (8/13 실측: 같은 seed 8회에 3종) — 출력 동일성은 **여기서 재는 것**이다.
    print("\n🔴 §E 결정론 — 같은 이력 2회")
    for index in range(0, len(runs) - 1, 2):
        first, second = runs[index], runs[index + 1]
        same = first.signature == second.signature
        print(
            f"  시나리오 {index // 2 + 1}: {'같다' if same else '🔴 다르다'} "
            f"{first.signature} vs {second.signature}"
        )


async def _main(max_calls: int, repeats: int) -> None:
    scenarios = _scenarios()
    planned = len(scenarios) * repeats
    #: 🔴 **상한 없이 돌리지 않는다**(불변식 6) — 사용자 개인 키다.
    if planned > max_calls:
        raise SystemExit(
            f"계획 콜 {planned} 이 상한 {max_calls} 을 넘는다 — --max-calls 를 올리거나 "
            "--repeats 를 줄여라"
        )
    print(f"🔴 이번 회차 총 콜 수: {planned} (상한 {max_calls})")
    provider = GatewayLabelSuggestProvider(build_label_gateway())
    totals = Totals()
    for name, history in scenarios:
        for turn in range(1, repeats + 1):
            run = await _one(provider, history)
            print(
                f"  [{name} {turn}/{repeats}] {run.latency_ms}ms "
                f"파서 {run.parsed} → 게이트 {run.grounded} → 병합 {run.merged} "
                f"제외 {run.dropped_history} {run.error or ''}"
            )
            totals.add(run)
    _report(totals)


def main() -> None:
    parser = argparse.ArgumentParser(description="라벨 제안 실 LLM 스모크")
    parser.add_argument("--max-calls", type=int, default=20, help="총 콜 상한(기본 20)")
    parser.add_argument("--repeats", type=int, default=2, help="시나리오당 반복(기본 2)")
    args = parser.parse_args()
    asyncio.run(_main(args.max_calls, args.repeats))


if __name__ == "__main__":
    main()


__all__: list[str] = ["main"]
