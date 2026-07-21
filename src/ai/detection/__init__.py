"""위험신호 감지 — 결정론 파이프라인 (LLM 금지, 불변식 1).

DetectRequest(누적 스냅숏) → DetectResponse. 계산과 I/O를 분리하며(순수 함수),
임계값은 thresholds.ThresholdConfig로 주입한다 (04_threshold_config.md).
"""
