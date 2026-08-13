"""브리핑 근거 패키지(grounding) — 유형별 수치·한글 라벨·배 단위·게이트 허용집합.

데모 스냅숏(6규칙 발화)으로 실제 조립 결과를 검증한다. rule 판정식 미접근(공개 피처
재사용)이 전제라 엔진 detect() 산출 신호에 붙여 확인한다.
"""

from __future__ import annotations

import re

from ai.composition.briefing_context import BriefingContext, build_contexts
from ai.contracts.detection import RuleId
from ai.contracts.taxonomy import AreaTag, TypeTag
from ai.detection.engine import detect
from ai.evaluation.demo_snapshot import build_demo_request


def _ctx_for(rule_id: RuleId) -> BriefingContext:
    request = build_demo_request()
    response = detect(request)
    contexts = build_contexts(request, response.signals)
    for signal in response.signals:
        if signal.rule_id == rule_id:
            return contexts[signal.signal_id]
    raise AssertionError(f"데모에 {rule_id} 신호가 없다")


def test_r6_uses_korean_area_type_labels() -> None:
    """R6 셀 영역·유형은 확정 한글 어휘로(영어 원태그 유출 없음)."""
    ctx = _ctx_for(RuleId.R6)
    cell = next(f for f in ctx.facts if f.label == "오답이 몰린 영역·유형")
    #: ⚠ **값을 리터럴로 박지 않는다** — 데모의 bias 셀이 바뀌면(2026-08-14에 시드와
    #:   맞추면서 `language·concept` 학생이 앞에 왔다) 이 검사가 **어휘 문제가 아닌
    #:   이유로** red가 된다. 지키려는 것은 «영어 원태그가 안 샌다»이지 특정 셀이 아니다.
    assert "·" in cell.value, cell.value
    assert not any(tag.value in cell.value for tag in AreaTag), cell.value
    assert not any(tag.value in cell.value for tag in TypeTag), cell.value


def test_r4_ratio_uses_bae_unit_not_percent() -> None:
    """R4 풀이시간 배율은 '배' 단위(%가 아님) — 게이트 허용집합도 이 표기 기준."""
    ctx = _ctx_for(RuleId.R4)
    ratio = next(f for f in ctx.facts if f.label.startswith("문제 풀이 시간"))
    assert ratio.value.endswith("배")
    assert "%" not in ratio.value
    assert set(re.findall(r"\d+", ratio.value)) <= ctx.allowed_numbers()


def test_allowed_numbers_only_from_provided_facts() -> None:
    """허용 숫자는 컨텍스트가 제공한 표기에서만 나온다(EXACT — 파생 표기 포함)."""
    ctx = _ctx_for(RuleId.R6)
    provided: set[str] = set()
    for fact in ctx.facts:
        provided.update(re.findall(r"\d+", fact.value))
    assert ctx.allowed_numbers() >= provided
    # 근거에 없는 숫자는 허용집합에 없다
    assert "999" not in ctx.allowed_numbers()
