"""분류 평가 러너 — **실 LLM 측정** (08 §8 CI 3단 중 ②·일 1회 · H-4).

소유: 박진희 (평가 격리 — 프로덕션 경로 아님). 선례는 `counsel_llm_smoke.py`다.

🔴 **CI 기본 경로에 넣지 않는다.** FakeProvider 시나리오는 pytest가 담당하고
(`tests/ai/unit/composition/test_classify.py`), 이 러너는 실서버 env가 있을 때만 돈다.

**합격 기준(H-3):**
- `topic` 정확도 ≥ 85%
- `sentiment=complaint` **재현율 ≥ 95%** — 민원 놓침이 최악이다(08 §7 근거 유지)
- `urgency` 정확도 ≥ 85%
- **축 독립성**: `topic` 오분류 건에서 다른 축이 함께 틀리는 비율을 **관측만** 한다
  (수치 기준은 이번에 정하지 않는다 — 표본이 80건이라 유의성을 말할 수 없다)

실행:
    LLM_PROVIDER=openai_compat uv run python -m ai.evaluation.classify_eval
    # 외부 API에서 429를 맞으면 호출 간격을 준다(기본 0)
    LLM_PROVIDER=openai_compat uv run python -m ai.evaluation.classify_eval --interval-ms 300
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NamedTuple
from uuid import uuid4

from ai.composition.classify.classifier import classify, classify_versions
from ai.composition.classify.provider import (
    build_classify_gateway,
    get_classify_settings,
)
from ai.contracts.classify import ClassifyRequest, ClassifyResult
from ai.contracts.counsel import InquirySentiment
from ai.contracts.execution import Capability, ExecutionContext
from ai.evaluation.golden.classify.corpus import (
    CASES,
    INJECTION_CASES,
    REDACTION_CASES,
    complaint_count,
)
from ai.runtime.redaction import redact
from ai.runtime.tracing import active_tracing_env_names, external_tracing_active

_RESULT_PATH = Path("local_data/classify_eval_result.json")
"""원문 포함 산출 — 레포 반입 금지(local_data는 gitignore)."""

_TOPIC_MIN = 0.85
_URGENCY_MIN = 0.85
_COMPLAINT_RECALL_MIN = 0.95

#: 🔴 축 순서 — `expected`·`actual`·`confidence` **세 배열이 같은 순서**를 쓴다.
#: 순서가 어긋나면 조용히 틀린 표가 나온다(정확도는 그럴듯한 숫자로 나오고 아무도 못 본다).
AXES: Final = ("topic", "sentiment", "urgency")

#: confidence 구간 폭. 0.1 단위 — 표본 88건이라 이보다 잘게 쪼개면 구간당 한 자릿수가 된다.
BUCKET_WIDTH: Final = 0.1

#: 이 표본 수 미만인 구간은 정확도를 **근거로 쓰지 않는다**(표시는 하되 ⚠를 붙인다).
MIN_BUCKET_SAMPLES: Final = 10


class CaseRow(NamedTuple):
    """케이스 1건의 채점 결과 — **세 배열이 `AXES`와 같은 순서**임을 타입으로 묶는다.

    dict로 두면 `expected`만 순서를 바꾸는 실수가 조용히 통과한다.
    """

    expected: tuple[str, str, str]
    actual: tuple[str, str, str]
    confidence: tuple[float, float, float]
    hit: tuple[bool, bool, bool]
    classified: bool

    def as_json(self) -> dict[str, object]:
        return {"axes": list(AXES), **self._asdict()}


class Bucket(NamedTuple):
    """confidence 한 구간의 집계 — **표본 수가 정확도와 항상 붙어 다닌다**."""

    low: float
    high: float
    samples: int
    hits: int

    @property
    def accuracy(self) -> float | None:
        """표본 0이면 None — 0%로 표시하면 "정확도가 낮다"로 오독된다."""
        return self.hits / self.samples if self.samples else None

    @property
    def thin(self) -> bool:
        return 0 < self.samples < MIN_BUCKET_SAMPLES


def confidence_buckets(
    scored: Sequence[tuple[float, bool]], *, width: float = BUCKET_WIDTH
) -> list[Bucket]:
    """(confidence, 정답 여부) 목록 → 구간별 집계. **순수 함수**(단위 테스트 대상).

    🔴 **표본 수를 함께 낸다.** 구간 5건짜리 정확도는 근거가 못 되는데, 정확도만 적으면
    표에서 다른 구간과 똑같이 생겼다 — ⓐ(임계값 확정)가 그 숫자를 근거로 삼는다.

    상단 경계 1.0은 마지막 구간에 넣는다(`0.9~1.0`) — 안 그러면 confidence 1.0이 혼자
    빈 구간을 만든다.
    """
    count = int(round(1.0 / width))
    tallies = [[0, 0] for _ in range(count)]
    for value, hit in scored:
        index = min(int(value / width), count - 1)
        tallies[index][0] += 1
        tallies[index][1] += int(hit)
    return [
        Bucket(low=i * width, high=(i + 1) * width, samples=n, hits=h)
        for i, (n, h) in enumerate(tallies)
    ]


def render_buckets(axis: str, buckets: Sequence[Bucket]) -> list[str]:
    """구간 표를 텍스트 줄로 — 표본 0 구간은 생략(노이즈)."""
    lines = [f"  [{axis}] confidence 구간별 실제 정확도"]
    for bucket in buckets:
        if not bucket.samples:
            continue
        accuracy = bucket.accuracy
        assert accuracy is not None  # samples > 0
        mark = "  ⚠ 표본 부족" if bucket.thin else ""
        lines.append(
            f"    {bucket.low:.1f}~{bucket.high:.1f}  n={bucket.samples:<3d}"
            f" 정확도 {accuracy:.1%}{mark}"
        )
    return lines


@dataclass
class _Tally:
    total: int = 0
    topic_hit: int = 0
    urgency_hit: int = 0
    sentiment_hit: int = 0
    complaint_total: int = 0
    complaint_hit: int = 0
    unclassified: int = 0
    #: topic이 틀린 건에서 다른 축도 함께 틀린 수 — 축 독립성 관측(H-3).
    topic_miss: int = 0
    topic_miss_with_other_miss: int = 0


def _context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=uuid4(),
        tenant_id="teacher_alias_eval",
        capability=Capability.COMPOSITION,
        input_snapshot_hash="inquiry:eval",
        versions=classify_versions(),
    )


async def _classify_one(body: str, *, interval_ms: int = 0) -> ClassifyResult:
    """1건 분류 후 `interval_ms`만큼 쉰다.

    🔴 **이건 우회책이다.** 근본은 게이트웨이의 재시도 정책이 `wait_none()`(간격 없는 즉시
    재시도)이라는 것인데, 외부 API에서 429를 맞으면 **즉시 재시도해도 또 429**다. 어댑터가
    429를 재시도 대상으로 올렸으므로(#96) 이제 재시도는 일어나지만 간격이 없다.
    `llm/gateway.py`는 **B 소유**라 백오프를 여기서 넣을 수 없다 — 러너가 호출 자체를
    띄엄띄엄 보내 429를 **덜 맞게** 할 뿐이다(99 ⓡ · B 협의 대상).
    """
    request = ClassifyRequest(inquiry_ref="iq_eval", body_text=body)
    result = await classify(request, build_classify_gateway(), context=_context())
    if interval_ms:
        await asyncio.sleep(interval_ms / 1000)
    return result


async def _main_async(interval_ms: int) -> int:
    if external_tracing_active():
        names = ", ".join(active_tracing_env_names()) or "(env 밖)"
        raise SystemExit(f"❌ 외부 추적 활성({names}) — 평가 중단(C-1).")
    settings = get_classify_settings()
    if settings.llm_provider != "openai_compat":
        raise SystemExit("❌ LLM_PROVIDER=openai_compat 이 아니다 — 실모델 평가 skip.")

    print(f"코퍼스 {len(CASES)}건(전량 합성) · complaint 양성 {complaint_count()}건")
    tally = _Tally()
    rows: list[CaseRow] = []
    for case in CASES:
        result = await _classify_one(case.body_text, interval_ms=interval_ms)
        tally.total += 1
        if not result.classified:
            tally.unclassified += 1
        topic_ok = result.topic is case.topic
        sentiment_ok = result.sentiment is case.sentiment
        urgency_ok = result.urgency is case.urgency
        tally.topic_hit += topic_ok
        tally.sentiment_hit += sentiment_ok
        tally.urgency_hit += urgency_ok
        if case.sentiment is InquirySentiment.COMPLAINT:
            tally.complaint_total += 1
            tally.complaint_hit += sentiment_ok
        if not topic_ok:
            tally.topic_miss += 1
            tally.topic_miss_with_other_miss += not (sentiment_ok and urgency_ok)
        rows.append(
            CaseRow(
                expected=(case.topic.value, case.sentiment.value, case.urgency.value),
                actual=(result.topic.value, result.sentiment.value, result.urgency.value),
                confidence=(
                    result.confidence.topic,
                    result.confidence.sentiment,
                    result.confidence.urgency,
                ),
                hit=(topic_ok, sentiment_ok, urgency_ok),
                classified=result.classified,
            )
        )

    # 🔴 인젝션 — 정답이 아니라 "enum 밖으로 못 나갔는가"를 본다.
    injection_escapes = 0
    for attack in INJECTION_CASES:
        result = await _classify_one(attack, interval_ms=interval_ms)
        # 파싱을 통과했다면 값은 반드시 enum 안이다(타입이 보장) — 여기선 예외 없이
        # 200으로 수렴했는지만 확인한다.
        injection_escapes += result.topic.value not in {
            "grade",
            "schedule",
            "counsel_request",
            "etc",
        }

    # redaction 경계 — 원문 조각이 프롬프트로 안 나갔는지는 pytest가 구조로 본다.
    # 여기선 **응답이 200으로 수렴하는지**(폴백 포함)만 본다.
    redaction_rows = []
    for body in REDACTION_CASES:
        result = await _classify_one(body, interval_ms=interval_ms)
        # A-4 판단: **넣는다.** 정답이 없어 정확도는 못 내지만, 폴백 건은 confidence가
        # 0.0으로 고정되므로(`_unclassified()`) "폴백했다"와 "낮은 확신으로 분류했다"를
        # 이 필드로만 가를 수 있다 — ⓔ(폴백률) 분석이 그 구분을 요구한다.
        redaction_rows.append(
            {
                "classified": result.classified,
                "reason": result.fallback_reason,
                "axes": list(AXES),
                "confidence": [
                    result.confidence.topic,
                    result.confidence.sentiment,
                    result.confidence.urgency,
                ],
            }
        )
        assert not redact(body).masked_text.count("010-1234-5678")

    topic_acc = tally.topic_hit / tally.total
    urgency_acc = tally.urgency_hit / tally.total
    recall = (
        tally.complaint_hit / tally.complaint_total if tally.complaint_total else 0.0
    )
    passed = (
        topic_acc >= _TOPIC_MIN
        and urgency_acc >= _URGENCY_MIN
        and recall >= _COMPLAINT_RECALL_MIN
        and injection_escapes == 0
    )

    print(f"  topic    정확도 {topic_acc:.1%} (기준 {_TOPIC_MIN:.0%})")
    print(f"  urgency  정확도 {urgency_acc:.1%} (기준 {_URGENCY_MIN:.0%})")
    print(f"  complaint 재현율 {recall:.1%} (기준 {_COMPLAINT_RECALL_MIN:.0%})")
    print(f"  미분류 {tally.unclassified}/{tally.total} · 인젝션 탈출 {injection_escapes}/5")
    if tally.topic_miss:
        share = tally.topic_miss_with_other_miss / tally.topic_miss
        print(f"  [관측] topic 오분류 {tally.topic_miss}건 중 타 축 동반 오분류 {share:.1%}")

    # 🔴 ⓐ(임계값)가 요구하는 표 — **축마다 따로**다. 3축이 독립이라 곡선도 다르다.
    buckets = {
        axis: confidence_buckets(
            [(row.confidence[i], row.hit[i]) for row in rows]
        )
        for i, axis in enumerate(AXES)
    }
    print()
    for axis in AXES:
        for line in render_buckets(axis, buckets[axis]):
            print(line)
    print(
        f"\n  ⚠ 표본 {tally.total}건은 **합성 코퍼스**다 — 실사용자 데이터가 아니다."
        "\n    임계값은 이 표를 근거로 **사람이** 정하고(04 §3.5 계약 값), 실사용 착수 후"
        "\n    재분류율(P2-c `corrected_*`)로 재조정한다."
    )
    print(f"\n판정: {'통과 ✅' if passed else '미달 ❌'}")

    _RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _RESULT_PATH.write_text(
        json.dumps(
            {
                "topic_accuracy": topic_acc,
                "urgency_accuracy": urgency_acc,
                "complaint_recall": recall,
                "unclassified": tally.unclassified,
                "injection_escapes": injection_escapes,
                "topic_miss": tally.topic_miss,
                "topic_miss_with_other_miss": tally.topic_miss_with_other_miss,
                "confidence_buckets": {
                    axis: [b._asdict() for b in buckets[axis]] for axis in AXES
                },
                "bucket_width": BUCKET_WIDTH,
                "min_bucket_samples": MIN_BUCKET_SAMPLES,
                "redaction_cases": redaction_rows,
                "rows": [row.as_json() for row in rows],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"산출(비커밋): {_RESULT_PATH}")
    return 0 if passed else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="분류 평가 러너(실 LLM)")
    # 🔴 기본 0 — 로컬 서버에선 불필요한 지연이다. 값은 **실행자가** 정한다.
    #   외부 API에서 429를 맞으면 올린다(우회책 — `_classify_one` docstring 참조).
    parser.add_argument(
        "--interval-ms",
        type=int,
        default=0,
        help="호출 간 간격(ms) — rate limit 회피용. 기본 0(간격 없음)",
    )
    args = parser.parse_args()
    if args.interval_ms < 0:
        raise SystemExit("❌ --interval-ms는 음수일 수 없다")
    sys.path.insert(0, str(Path.cwd()))
    raise SystemExit(asyncio.run(_main_async(args.interval_ms)))


if __name__ == "__main__":
    main()
