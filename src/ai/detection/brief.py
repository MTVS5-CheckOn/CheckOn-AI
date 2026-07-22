"""브리핑 한 줄 문장화 — 결정론 템플릿 (LLM 금지).

소유: 박진희. 02_design상 문장화(ⓐ)는 detection 밖 소비 기능이므로 별도 모듈로 둔다 —
엔진은 신호·근거만 만들고 brief.text는 이 모듈이 채운다. 나중에 composition/briefing.py가
LLM 문장화를 붙일 때 이 결정론 구현을 교체·보완한다.

**gate_passed=True · fallback_used=False 고정:** 템플릿은 신호 사실만 조립해 수치 왜곡이
없으므로 게이트를 통과한다. fallback_used는 09 §3상 "LLM 문장이 검사에 걸려 템플릿으로
대체됨"을 뜻하는데, LLM 문장화가 붙기 전인 이 단계에서 false는 **"템플릿 1차 산출"**을
의미한다 — true로 두면 나중에 진짜 폴백률 지표와 전후 비교가 오염된다.
"""

from __future__ import annotations

from ai.contracts.detection import Brief, SignalType

#: signal_type별 결정론 문구 — 수치·실명 없이 신호 성격만 전한다(왜곡 게이트 통과).
_TEMPLATES: dict[SignalType, str] = {
    SignalType.ACC_DROP: "정답률이 평소보다 눈에 띄게 떨어진 상태가 이어지고 있어요.",
    SignalType.SUBMIT_DROP: "과제 제출이 최근 연속으로 빠지고 있어요.",
    SignalType.VOLUME_GAP: "이번 주 학습량이 평소보다 크게 줄었어요.",
    SignalType.HIDDEN_RISK: "점수는 버티고 있지만 문제를 붙잡는 시간이 늘고 있어요.",
    SignalType.RETURN_CARE: "복귀 첫 주예요 — 적응을 한 번 살펴봐 주세요.",
    SignalType.TYPE_BIAS: "특정 영역·유형에 오답이 몰리고 있어요.",
}


def build_brief(signal_type: SignalType, detail: str = "") -> Brief:
    """결정론 템플릿으로 brief를 만든다. detail이 있으면 괄호로 덧붙인다."""
    text = _TEMPLATES[signal_type]
    if detail:
        text = f"{text} ({detail})"
    return Brief(text=text, gate_passed=True, fallback_used=False)
