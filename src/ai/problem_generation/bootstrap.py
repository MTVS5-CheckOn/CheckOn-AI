"""M2 문제출제 워크플로 의존성 조립 진입점."""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from ai.contracts.graphrag import GraphContextService
from ai.llm.gateway import LlmGateway
from ai.problem_generation.application.cross_solver import BlindCrossSolver
from ai.problem_generation.application.generator import ProblemGenerator
from ai.problem_generation.application.literature_selector import LiteratureSelector
from ai.problem_generation.application.passage_generator import PassageGenerator
from ai.problem_generation.application.ports import CandidateStore, ProblemItemStore
from ai.problem_generation.application.workflow import (
    DiagnosisCallable,
    ProblemGenerationWorkflow,
)
from ai.problem_generation.domain.policy import BannedTopicsConfig, VerifyConfig
from ai.problem_generation.infrastructure.config import (
    load_area_specs,
    load_banned_topics,
    load_verify_config,
)
from ai.problem_generation.infrastructure.literature_pool import load_literature_pool


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

    resolved_verify_config = verify_config or load_verify_config()
    resolved_banned_topics = banned_topics or load_banned_topics()
    return ProblemGenerationWorkflow(
        diagnosis=diagnosis,
        graph_context=graph_context,
        generator=ProblemGenerator(gateway, area_specs=load_area_specs()),
        passage_generator=PassageGenerator(
            gateway,
            banned_topics=resolved_banned_topics,
        ),
        literature_selector=LiteratureSelector(load_literature_pool()),
        cross_solver=BlindCrossSolver(gateway),
        candidate_store=candidate_store,
        item_store=item_store,
        checkpointer=checkpointer,
        verify_config=resolved_verify_config,
        banned_topics=resolved_banned_topics,
    )


__all__ = ["build_problem_workflow"]
