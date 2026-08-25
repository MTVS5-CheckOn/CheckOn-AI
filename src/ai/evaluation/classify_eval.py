"""분류 평가 러너 — **실 LLM 측정** (08 §8 CI 3단 중 ②·일 1회 · H-4).

소유: 박진희 (평가 격리 — 프로덕션 경로 아님). 선례는 `counsel_llm_smoke.py`다.

🔴 **CI 기본 경로에 넣지 않는다.** FakeProvider 시나리오는 pytest가 담당하고
(`tests/ai/unit/composition/test_classify.py`), 이 러너는 실서버 env가 있을 때만 돈다.

**합격 기준(H-3 · 8/8 개정 — 정본은 08 §7):**
- `topic` 정확도 ≥ 85% (baseline 25.00% · 요구 개선폭 **+60.00%p**)
- `urgency` 정확도 ≥ 85% (baseline **81.25%** · 요구 개선폭 **+3.75%p** — 🔴 **검증력 없음**)
- `sentiment=complaint` **재현율 ≥ 95%** — 민원 놓침이 최악이다(08 §7 근거 유지)
- `urgency=immediate` 재현율 **`[측정 대기]`** — 표본 15건이라 1건이 6.7%p다(99 ㉡)
- 인젝션 탈출 0건
- **축 독립성**: `topic` 오분류 건에서 다른 축이 함께 틀리는 비율을 **관측만** 한다
  (수치 기준은 이번에 정하지 않는다 — 표본이 80건이라 유의성을 말할 수 없다)

🔴 **정확도는 baseline과 짝으로만 낸다.** 2차 실측에서 `urgency`가 87.5%로 "통과"했는데
같은 표본의 `immediate` 재현율은 **33.3%**(5/15)였다 — 긴급의 2/3를 놓치며 합격했다.
**baseline은 이 러너가 코퍼스에서 직접 계산한다** — 사람이 표에 적으면 코퍼스가 바뀔 때
갱신을 잊고, 그게 ㉡을 만든 형태다.

실행:
    LLM_PROVIDER=openai_compat uv run python -m ai.evaluation.classify_eval
    # 외부 API에서 429를 맞으면 호출 간격을 준다(기본 0)
    LLM_PROVIDER=openai_compat uv run python -m ai.evaluation.classify_eval --interval-ms 300
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections import Counter
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
from ai.contracts.counsel import InquirySentiment, InquiryTopic, InquiryUrgency
from ai.contracts.execution import Capability, ExecutionContext
from ai.contracts.llm import LlmError
from ai.evaluation.golden.classify.corpus import (
    CASES,
    INJECTION_CASES,
    REDACTION_CASES,
    complaint_count,
)
from ai.runtime.errors import RedactionUncertain
from ai.runtime.redaction import redact
from ai.runtime.tracing import active_tracing_env_names, external_tracing_active

logger = logging.getLogger(__name__)

_RESULT_PATH = Path("local_data/classify_eval_result.json")
"""원문 포함 산출 — 레포 반입 금지(local_data는 gitignore)."""

_TOPIC_MIN = 0.85
_URGENCY_MIN = 0.85
_COMPLAINT_RECALL_MIN = 0.95

#: 🔴 축 순서 — `expected`·`actual`·`confidence` **세 배열이 같은 순서**를 쓴다.
#: 순서가 어긋나면 조용히 틀린 표가 나온다(정확도는 그럴듯한 숫자로 나오고 아무도 못 본다).
AXES: Final = ("topic", "sentiment", "urgency")

#: 축별 라벨 — 혼동행렬의 행·열 순서다. 🔴 **표본에 안 나온 라벨도 표에 0으로 남아야**
#: "그 값이 한 번도 예측되지 않았다"는 사실이 보인다. 어휘 정본은 `contracts/counsel.py`다.
AXIS_LABELS: Final[dict[str, tuple[str, ...]]] = {
    "topic": tuple(v.value for v in InquiryTopic),
    "sentiment": tuple(v.value for v in InquirySentiment),
    "urgency": tuple(v.value for v in InquiryUrgency),
}

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


class ConfusionMatrix(NamedTuple):
    """한 축의 예측×정답 교차표 — **순수 함수 산출**(단위 테스트 대상).

    🔴 **왜 필요한가 — 재현율만으로는 대응이 안 정해진다.** 2차 실측에서 `complaint`
    재현율이 93.3%(28/30)로 H-3 기준 95%에 미달했는데, **놓친 2건이 어느 방향인지** 알 수
    없었다(99 ㉠). complaint→normal(**놓침**)이면 프롬프트·임계를 손봐야 하고,
    normal→complaint면 재현율 정의상 무관하다. 방향을 모르면 "미달"만 남는다.
    """

    labels: tuple[str, ...]
    #: `counts[정답][예측]` — 행이 정답, 열이 예측이다(관례를 뒤집으면 FN/FP가 바뀐다).
    counts: dict[str, dict[str, int]]

    def total(self) -> int:
        return sum(sum(row.values()) for row in self.counts.values())

    def hits(self) -> int:
        return sum(self.counts[label][label] for label in self.labels)

    def false_negatives(self, label: str) -> int:
        """정답이 `label`인데 다른 값으로 예측한 수 — **놓침**."""
        return sum(n for pred, n in self.counts[label].items() if pred != label)

    def false_positives(self, label: str) -> int:
        """정답이 아닌데 `label`로 예측한 수."""
        return sum(
            self.counts[truth][label] for truth in self.labels if truth != label
        )

    def confusions(self) -> list[tuple[str, str, int]]:
        """(정답, 예측, 수) — 틀린 칸만, 많은 순. 어디로 새는지가 대응을 정한다."""
        wrong = [
            (truth, pred, n)
            for truth, row in self.counts.items()
            for pred, n in row.items()
            if truth != pred and n
        ]
        return sorted(wrong, key=lambda item: (-item[2], item[0], item[1]))


def confusion_matrix(
    pairs: Sequence[tuple[str, str]], labels: Sequence[str]
) -> ConfusionMatrix:
    """(정답, 예측) 목록 → 교차표. **순수 함수**.

    `labels`를 인자로 받는 이유: 표본에 안 나온 라벨도 표에 **0으로 남아야** 한다 —
    빠지면 "그 값이 한 번도 예측되지 않았다"는 사실 자체가 안 보인다.
    """
    order = tuple(labels)
    counts: dict[str, dict[str, int]] = {
        truth: dict.fromkeys(order, 0) for truth in order
    }
    for truth, pred in pairs:
        counts[truth][pred] += 1
    return ConfusionMatrix(labels=order, counts=counts)


def render_confusion(axis: str, matrix: ConfusionMatrix) -> list[str]:
    """교차표를 텍스트 줄로 — 틀린 칸만 나열한다(4×4를 다 그리면 읽히지 않는다)."""
    lines = [f"  [{axis}] 혼동 — 총 {matrix.total()}건 · 적중 {matrix.hits()}건"]
    wrong = matrix.confusions()
    if not wrong:
        lines.append("    (오분류 없음)")
        return lines
    lines.extend(f"    정답 {t} → 예측 {p} : {n}건" for t, p, n in wrong)
    return lines


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


def majority_baselines() -> dict[str, float]:
    """축별 **다수 클래스 baseline** — 코퍼스에서 직접 계산한다(99 ㉡).

    🔴 **사람이 문서에 적어 두면 코퍼스가 바뀔 때 갱신을 잊는다.** 기준 85%가 분포와
    무관하게 굳어 있었던 것이 ㉡의 형태이므로, 분모는 **매 회차 계산**한다.
    `tests/ai/golden/test_classify_baseline.py`가 문서 값과의 일치를 따로 잠근다.
    """
    return {
        axis: max(Counter(getattr(c, axis).value for c in CASES).values()) / len(CASES)
        for axis in AXES
    }


@dataclass
class _Tally:
    total: int = 0
    topic_hit: int = 0
    urgency_hit: int = 0
    sentiment_hit: int = 0
    complaint_total: int = 0
    complaint_hit: int = 0
    #: 🔴 `complaint`와 **같은 종류의 실패**다 — 긴급을 인박스에 못 올린다(99 ㉡).
    #:  기준값은 아직 없다(`[측정 대기]`) — 그래도 **수치는 낸다**. 안 내면 다음 회차에도
    #:  "정확도는 통과"만 남는다.
    immediate_total: int = 0
    immediate_hit: int = 0
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


#: 🔴 장애를 판정(`classified`·`fallback_reason`)에 섞지 않는다 — 별도 축이다.
#: 그게 #117이 없앤 거짓말이고, 그 거짓말이 **측정 리포트에도** 있었다.
_FAILURE_KINDS: Final = {
    "LlmTimeout": "llm_timeout",
    "LlmUnavailable": "llm_unavailable",
    "RedactionUncertain": "redaction_uncertain",
}


def failure_kind(exc: BaseException) -> str:
    """장애 종류 — `fallback_reason`(판단)과 **다른 축**이다."""
    return _FAILURE_KINDS.get(type(exc).__name__, "llm_error")


@dataclass
class _Budget:
    """🔴 총 실 LLM 콜 상한 — **세 루프가 함께 쓴다**(99 #256 축 · №114).

    ⚠ 🔴 이 러너는 `CASES`(80) + `INJECTION_CASES`(5) + `REDACTION_CASES`(3) = **88콜**
    을 태우는데 상한이 **없었다** — `briefing_preview`(66) 보다 비싸다.
    🔴 **루프마다 세면 갈린다** — 하나로 센다.
    """

    max_calls: int
    """🔴 기본값을 안 둔다. `0` 이면 한 콜도 안 태우고 배선만 확인한다."""
    spent: int = 0
    capped: bool = False

    def take(self) -> bool:
        """한 콜을 쓴다 — 남았으면 `True`. 🔴 **없으면 표식을 세우고 `False`.**"""
        if self.spent >= self.max_calls:
            self.capped = True
            return False
        self.spent += 1
        return True


async def _try_classify_one(
    body: str, *, interval_ms: int = 0
) -> tuple[ClassifyResult | None, str | None]:
    """1건 분류 — **장애 1건에 88건 회차를 잃지 않는다.**

    돌려주는 것: `(결과, 장애 종류)`. 장애면 결과가 None이다.

    ⚠ `except Exception`으로 뭉개지 않는다. 잡는 건 `LlmError` 계열과 `RedactionUncertain`
    뿐이고 코드 버그(`AttributeError` 등)는 그대로 죽어야 한다 — **러너가 조용히 도는 것이
    측정에선 최악**이다.
    """
    try:
        return await _classify_one(body, interval_ms=interval_ms), None
    except (LlmError, RedactionUncertain) as exc:
        logger.warning("분류 1건 장애 — 행을 남기고 계속한다: %s", type(exc).__name__)
        return None, failure_kind(exc)


async def _main_async(interval_ms: int, max_calls: int) -> int:
    if external_tracing_active():
        names = ", ".join(active_tracing_env_names()) or "(env 밖)"
        raise SystemExit(f"❌ 외부 추적 활성({names}) — 평가 중단(C-1).")
    settings = get_classify_settings()
    if settings.llm_provider != "openai_compat":
        raise SystemExit("❌ LLM_PROVIDER=openai_compat 이 아니다 — 실모델 평가 skip.")

    budget = _Budget(max_calls=max_calls)
    planned = len(CASES) + len(INJECTION_CASES) + len(REDACTION_CASES)
    print(f"코퍼스 {len(CASES)}건(전량 합성) · complaint 양성 {complaint_count()}건")
    print(f"🔴 콜 상한 {max_calls} · 전량 {planned}")
    tally = _Tally()
    rows: list[CaseRow] = []
    failures: list[dict[str, str]] = []
    for case in CASES:
        if not budget.take():
            break
        result, failed = await _try_classify_one(case.body_text, interval_ms=interval_ms)
        if result is None:
            # 🔴 **분모에서 뺀다** — 판정이 없던 건을 오답으로 세면 정확도가 거짓이 된다.
            failures.append({"stage": "corpus", "case": case.body_text[:24], "kind": failed or ""})
            continue
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
        if case.urgency is InquiryUrgency.IMMEDIATE:
            tally.immediate_total += 1
            tally.immediate_hit += urgency_ok
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
    #: 🔴 **분모를 「돈 것」으로 센다** — 상한에 걸리면 5건을 다 못 돈다. 상수 `/5` 로
    #: 적으면 «5건 중 0건 탈출» 로 읽혀 🔴 **안 돈 것이 통과로 보인다**(#454 가 잡은 형태).
    injection_ran = 0
    for attack in INJECTION_CASES:
        if not budget.take():
            break
        injection_ran += 1
        result, failed = await _try_classify_one(attack, interval_ms=interval_ms)
        if result is None:
            # ⚠ 장애는 **탈출이 아니다** — 0으로도 1로도 세지 않고 별도 축에 남긴다.
            failures.append({"stage": "injection", "case": attack[:24], "kind": failed or ""})
            continue
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
        if not budget.take():
            break
        result, failed = await _try_classify_one(body, interval_ms=interval_ms)
        if result is None:
            failures.append({"stage": "redaction", "case": body[:24], "kind": failed or ""})
            continue
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

    if not tally.total:
        #: 🔴 **판정 표본이 0건이면 정확도를 못 낸다** — 나누기 전에 멈춘다.
        #: ⚠ 🔴 №114 뒤집기 ①(`--max-calls 0`)이 여기서 `ZeroDivisionError` 를 냈다.
        #: 🔴 **그냥 0.0 으로 채우지 않는다** — «정확도 0%» 는 «안 쟀다» 와 다르다.
        #: 🔴 **`capped` 로 가르면 거짓이 된다** — 상한 3 에 3콜이 다 장애여도 `capped`
        #: 는 True 라 «하나도 안 돌았다» 로 적힌다(№114 뒤집기 ②가 실제로 그렇게
        #: 나왔다). 🔴 **가르는 것은 「몇 콜을 썼나」다.**
        if budget.spent == 0:
            print(
                f"  🔴 표본 **0건** — 콜 상한 {budget.max_calls} 이라 한 콜도 안 썼다."
                " 배선만 확인했다(수치 없음)."
            )
            return 0
        #: 콜은 썼는데 판정이 0건이면 **전부 장애**다 — 그건 성공이 아니다.
        print(
            f"  ❌ 표본 0건 — {budget.spent}콜을 썼는데 **전 건 장애**"
            f"({len(failures)}건). 수치를 못 낸다."
        )
        return 1
    topic_acc = tally.topic_hit / tally.total
    urgency_acc = tally.urgency_hit / tally.total
    recall = (
        tally.complaint_hit / tally.complaint_total if tally.complaint_total else 0.0
    )
    #: `[측정 대기]`라 합격 판정에는 넣지 않는다 — 그래도 **수치는 낸다**(99 ㉡).
    immediate_recall = (
        tally.immediate_hit / tally.immediate_total if tally.immediate_total else 0.0
    )
    sentiment_acc = tally.sentiment_hit / tally.total
    baselines = majority_baselines()
    passed = (
        topic_acc >= _TOPIC_MIN
        and urgency_acc >= _URGENCY_MIN
        and recall >= _COMPLAINT_RECALL_MIN
        and injection_escapes == 0
    )

    # 🔴 **정확도는 baseline과 짝으로만 낸다**(99 ㉡) — 단독 수치는 그 기준이 무엇을
    #    검증하는지 말하지 않는다. 같은 85%가 topic에서는 +60%p를, urgency에서는
    #    +3.75%p를 요구한다.
    for axis, acc, floor in (
        ("topic", topic_acc, _TOPIC_MIN),
        ("sentiment", sentiment_acc, None),
        ("urgency", urgency_acc, _URGENCY_MIN),
    ):
        base = baselines[axis]
        gate = f"기준 {floor:.0%}" if floor is not None else "기준 미정"
        weak = "  🔴 이 기준은 검증력이 없다" if floor is not None and floor - base < 0.10 else ""
        print(
            f"  {axis:9} 정확도 {acc:.1%} (baseline {base:.2%} · "
            f"개선폭 {acc - base:+.2%}p · {gate}){weak}"
        )
    if budget.capped:
        #: ⚠ 🔴 **조용히 자르지 않는다** — 수와 같은 화면에 «부분 표본» 을 박는다.
        print(
            f"  🔴 ⚠ **콜 상한 {budget.max_calls} 에 걸려 멈췄다 — 전량이 아니다**"
            f"(계획 {planned} 중 {budget.spent} 만 돌았다). 아래 수치는 **부분 표본**이다."
        )
    print(f"  complaint 재현율 {recall:.1%} (기준 {_COMPLAINT_RECALL_MIN:.0%})")
    # ⚠ 기준값이 없어도 낸다 — 안 내면 다음 회차에도 "정확도는 통과"만 남는다.
    print(
        f"  immediate 재현율 {immediate_recall:.1%} "
        f"({tally.immediate_hit}/{tally.immediate_total} · 기준 [측정 대기] · 99 ㉡)"
    )
    print(
        f"  미분류 {tally.unclassified}/{tally.total} · "
        f"인젝션 탈출 {injection_escapes}/{injection_ran}"
        + (f" ⚠ (전량 {len(INJECTION_CASES)} 중 {injection_ran} 만 돌았다)"
           if injection_ran < len(INJECTION_CASES) else "")
    )
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

    # 🔴 ㉠ — 재현율만으로는 대응이 안 정해진다. 어디로 새는지를 축마다 낸다.
    matrices = {
        axis: confusion_matrix(
            [(row.expected[i], row.actual[i]) for row in rows], AXIS_LABELS[axis]
        )
        for i, axis in enumerate(AXES)
    }
    print()
    for axis in AXES:
        for line in render_confusion(axis, matrices[axis]):
            print(line)

    # 🔴 complaint **FN**을 따로 세운다 — §7이 이 축만 95%로 잡은 이유가 "민원 놓침이
    #    최악"이고 FN이 정확히 그 놓침이다. FP와 같은 무게로 섞어 보이면 판단이 흐려진다.
    sentiment_matrix = matrices["sentiment"]
    complaint = InquirySentiment.COMPLAINT.value
    fn = sentiment_matrix.false_negatives(complaint)
    fp = sentiment_matrix.false_positives(complaint)
    print(
        f"\n  🔴 complaint **놓침(FN)** {fn}건 — 민원을 normal로 봤다(§7이 95%를 요구하는 이유)"
        f"\n     complaint 오검(FP) {fp}건 — 정상을 민원으로 봤다(재현율과 무관 · 참고)"
    )

    if failures:
        print(f"\n🔴 장애로 **분모에서 뺀 건** {len(failures)}건 — 정확도는 나머지 기준이다")
        for f in failures:
            print(f"     [{f['stage']}] {f['kind']} :: {f['case']}")
    print(f"\n판정: {'통과 ✅' if passed else '미달 ❌'}")

    _RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _RESULT_PATH.write_text(
        json.dumps(
            {
                "topic_accuracy": topic_acc,
                "sentiment_accuracy": sentiment_acc,
                "urgency_accuracy": urgency_acc,
                "majority_baselines": baselines,
                "complaint_recall": recall,
                "immediate_recall": immediate_recall,
                "unclassified": tally.unclassified,
                "injection_escapes": injection_escapes,
                "topic_miss": tally.topic_miss,
                "topic_miss_with_other_miss": tally.topic_miss_with_other_miss,
                "confidence_buckets": {
                    axis: [b._asdict() for b in buckets[axis]] for axis in AXES
                },
                "confusion": {axis: matrices[axis].counts for axis in AXES},
                "complaint_fn": fn,
                "complaint_fp": fp,
                "bucket_width": BUCKET_WIDTH,
                "min_bucket_samples": MIN_BUCKET_SAMPLES,
                "redaction_cases": redaction_rows,
                # 🔴 장애는 **별도 축**이다 — 정확도 분모에서 뺐으므로 몇 건을 뺐는지가
                #    산출에 남아야 한다. 안 남기면 "88건 다 됐다"로 읽힌다.
                "failures": failures,
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
    # 🔴 기본 0 — **실측 전에는 필요한 간격을 모른다.** 88건은 순차 호출이라 tier에 따라
    #   429가 아예 안 날 수도 있다. 0이 아닌 값을 기본으로 박으면 이후 모든 실행이 그 지연을
    #   조용히 지불하면서 "간격이 실제로 필요했는지"를 영영 못 재게 된다.
    #   값은 429를 맞은 **실행자가** 정한다(우회책 — `_classify_one` docstring · 99 ⓡ).
    #: 🔴 **필수다 — 기본값을 안 둔다**(#454 판단 그대로 · 99 #256).
    #: ⚠ 🔴 «기본값을 두면 「기본값 = 전량」이라 아무것도 안 막는다» — `labels_llm_smoke`
    #: 의 기본 20 이 그 러너의 전량이라 사실상 안 막는 것이 실측이었다.
    #: 🔴 **깨질 호출부가 없다** — 전수: `_main_async` 를 부르는 곳은 이 `main()` 뿐이고
    #: 검사들은 `confidence_buckets`·`render_buckets`·`confusion_matrix`·
    #: `render_confusion`·`_pii_scan` 만 import 한다.
    #: 🔴 `--max-calls 0` 이면 **0콜로 배선만** 본다.
    parser.add_argument(
        "--max-calls",
        type=int,
        required=True,
        help="총 실 LLM 콜 상한(필수) — 전량은 88(코퍼스 80 + 인젝션 5 + redaction 3). "
        "`0` 이면 0콜로 배선만 확인한다",
    )
    parser.add_argument(
        "--interval-ms",
        type=int,
        default=0,
        help="호출 간 간격(ms) — rate limit 회피용. 기본 0(간격 없음)",
    )
    args = parser.parse_args()
    if args.interval_ms < 0:
        raise SystemExit("❌ --interval-ms는 음수일 수 없다")
    if args.max_calls < 0:
        raise SystemExit("❌ --max-calls는 음수일 수 없다")
    sys.path.insert(0, str(Path.cwd()))
    raise SystemExit(asyncio.run(_main_async(args.interval_ms, args.max_calls)))


if __name__ == "__main__":
    main()
