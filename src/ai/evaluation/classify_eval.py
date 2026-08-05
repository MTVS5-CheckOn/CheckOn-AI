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
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
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


async def _classify_one(body: str) -> ClassifyResult:
    request = ClassifyRequest(inquiry_ref="iq_eval", body_text=body)
    return await classify(request, build_classify_gateway(), context=_context())


async def _main_async() -> int:
    if external_tracing_active():
        names = ", ".join(active_tracing_env_names()) or "(env 밖)"
        raise SystemExit(f"❌ 외부 추적 활성({names}) — 평가 중단(C-1).")
    settings = get_classify_settings()
    if settings.llm_provider != "openai_compat":
        raise SystemExit("❌ LLM_PROVIDER=openai_compat 이 아니다 — 실모델 평가 skip.")

    print(f"코퍼스 {len(CASES)}건(전량 합성) · complaint 양성 {complaint_count()}건")
    tally = _Tally()
    rows = []
    for case in CASES:
        result = await _classify_one(case.body_text)
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
            {
                "expected": [case.topic.value, case.sentiment.value, case.urgency.value],
                "actual": [
                    result.topic.value,
                    result.sentiment.value,
                    result.urgency.value,
                ],
                "classified": result.classified,
            }
        )

    # 🔴 인젝션 — 정답이 아니라 "enum 밖으로 못 나갔는가"를 본다.
    injection_escapes = 0
    for attack in INJECTION_CASES:
        result = await _classify_one(attack)
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
        result = await _classify_one(body)
        redaction_rows.append(
            {"classified": result.classified, "reason": result.fallback_reason}
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
                "redaction_cases": redaction_rows,
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"산출(비커밋): {_RESULT_PATH}")
    return 0 if passed else 1


def main() -> None:
    argparse.ArgumentParser(description="분류 평가 러너(실 LLM)").parse_args()
    sys.path.insert(0, str(Path.cwd()))
    raise SystemExit(asyncio.run(_main_async()))


if __name__ == "__main__":
    main()
