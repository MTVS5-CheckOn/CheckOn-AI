"""counsel_pack 워커 — 반 단위 상담 초안 오케스트레이터.

사양: `docs/policies/langgraph_state.md` §1(그래프·state·중단/재개) · §5(WorkerJob 실행 계약).
구조는 `import_mapping/probe/`(PR #25·#28)를 따르되, counsel_pack은 ReAct가 아니라
`plan → 학생 루프 → summarize` 순차 그래프라 도구·플래너 모듈이 없다 — probe의 그 자리를
`provider.py`(plan LLM Protocol)와 `gate.py`(결정론 게이트)가 대신한다.
"""
