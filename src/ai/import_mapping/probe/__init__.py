"""mapping_probe 워커 — 매핑 조사 ReAct (LangGraph) · 결정론 골격.

정본: docs/policies/langgraph_state.md §2(state)·§3(공통)·§5(WorkerJob) · 10_import_spec §3.3.
소유: 박진희(member-A · 워커 그래프는 capability 오너 단독). LLM 접점은 Protocol+Fake 기본
(실 LLM·게이트웨이는 후속). 도구는 마스킹 통과분만 반환(§5.2 구조적 차단).
"""
