"""M2 문제출제 워크플로 의존성 조립 진입점."""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from ai.contracts.graphrag import GraphContextService
from ai.llm.gateway import LlmGateway
from ai.problem_generation.application.cross_solver import BlindCrossSolver
from ai.problem_generation.application.generator import ProblemGenerator
from ai.problem_generation.application.ports import CandidateStore, ProblemItemStore
from ai.problem_generation.application.workflow import (
    DiagnosisCallable,
    ProblemGenerationWorkflow,
)
from ai.problem_generation.domain.policy import BannedTopicsConfig, VerifyConfig


def build_problem_workflow(
    *,
    gateway: LlmGateway,
    graph_context: GraphContextService,
    diagnosis: DiagnosisCallable,
    candidate_store: CandidateStore,
    item_store: ProblemItemStore,
    checkpointer: BaseCheckpointSaver[Any],
    verify_config: VerifyConfig | None = None,
    banned_topics: BannedTopicsConfig | None = None,
) -> ProblemGenerationWorkflow:
    """외부 의존성과 저장 포트를 문제출제 워크플로로 조립한다."""

    return ProblemGenerationWorkflow(
        diagnosis=diagnosis,
        graph_context=graph_context,
        generator=ProblemGenerator(gateway),
        cross_solver=BlindCrossSolver(gateway),
        candidate_store=candidate_store,
        item_store=item_store,
        checkpointer=checkpointer,
        verify_config=verify_config,
        banned_topics=banned_topics,
    )


__all__ = ["build_problem_workflow"]
