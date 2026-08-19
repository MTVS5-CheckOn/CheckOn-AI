"""M2 문제출제 워크플로 의존성 조립 진입점."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from ai.contracts.graphrag import GraphContextService
from ai.llm.gateway import LlmGateway
from ai.problem_generation.application.cross_solver import BlindCrossSolver
from ai.problem_generation.application.generator import ProblemGenerator
from ai.problem_generation.application.literature_selector import LiteratureSelector
from ai.problem_generation.application.passage_generator import (
    PassageGenerator,
    SourceMaterialGenerator,
)
from ai.problem_generation.application.ports import (
    CandidateStore,
    ProblemItemStore,
    ProblemSetStore,
)
from ai.problem_generation.application.workflow import (
    DiagnosisCallable,
    ProblemGenerationWorkflow,
)
from ai.problem_generation.domain.external_corpus import ExternalCorpusIndex
from ai.problem_generation.domain.literature import LiteraturePool
from ai.problem_generation.domain.policy import BannedTopicsConfig, VerifyConfig
from ai.problem_generation.infrastructure.aihub_corpus import (
    load_aihub_external_corpus,
)
from ai.problem_generation.infrastructure.config import (
    load_area_specs,
    load_banned_topics,
    load_verify_config,
)
from ai.problem_generation.infrastructure.literature_pool import load_literature_pool
from ai.problem_generation.infrastructure.memory_store import InMemoryProblemSetStore
from ai.problem_generation.settings import get_problem_generation_settings


@lru_cache
def resolve_literature_pool() -> LiteraturePool:
    """동봉 문학 풀을 프로세스당 한 번만 읽는다.

    🔴 **잡 실행마다 다시 읽으면 안 된다.** 로더는 fail-closed 라 본문 전량을 읽고
    sha256 을 다시 계산한다 — 풀이 5편(64 KB)일 때는 공짜였지만 1,495편(33 MB)에서는
    **잡 한 건마다 33 MB 재해싱**이다(실측: 냉간 5.6s · 온간 0.46s).
    ⚠ 캐시는 여기 조립 계층에만 둔다 — `load_literature_pool` 자체를 캐시하면
    무결성 검증의 실패 경로를 테스트가 재현할 수 없다.
    """

    return load_literature_pool()


def build_literature_selector(verify_config: VerifyConfig) -> LiteratureSelector:
    """설정된 발췌 창 경계로 문학 선택기를 조립한다.

    ⚠ **조립 자리는 하나여야 한다.** 러너가 따로 `LiteratureSelector(pool)` 를 만들면
    발췌 경계가 빠져 **미리보기와 실제 출제가 다른 구간을 고른다** — 실제로 그렇게
    9자짜리 대화 한 줄을 제시문으로 본 적이 있다.
    """

    return LiteratureSelector(
        resolve_literature_pool(),
        excerpt_min_chars=verify_config.literature_excerpt_min_chars,
        excerpt_max_chars=verify_config.literature_excerpt_max_chars,
    )


@lru_cache
def resolve_external_corpus() -> ExternalCorpusIndex | None:
    """설정된 R-8 외부 대조 코퍼스를 적재한다 — 미설정이면 R-8을 수행하지 않는다.

    🔴 **경로가 있는데 못 읽으면 예외로 끝낸다.** 조용히 `None`으로 떨어뜨리면
    **오설정과 미설정이 같은 모양**이 되고, 그때 R-8은 「검사했는데 통과」처럼 보인다
    — 06 §1이 금지하는 형태다. 적재 비용(실측 0.5s)이 있어 프로세스당 한 번만 읽는다.
    """

    root = get_problem_generation_settings().external_corpus_root
    if root is None:
        return None
    return load_aihub_external_corpus(root)


def build_problem_workflow(
    *,
    gateway: LlmGateway,
    graph_context: GraphContextService,
    diagnosis: DiagnosisCallable,
    candidate_store: CandidateStore,
    item_store: ProblemItemStore,
    checkpointer: BaseCheckpointSaver[Any],
    set_store: ProblemSetStore | None = None,
    verify_config: VerifyConfig | None = None,
    banned_topics: BannedTopicsConfig | None = None,
    external_corpus: ExternalCorpusIndex | None = None,
) -> ProblemGenerationWorkflow:
    """외부 의존성과 저장 포트를 문제출제 워크플로로 조립한다."""

    resolved_verify_config = verify_config or load_verify_config()
    resolved_banned_topics = banned_topics or load_banned_topics()
    area_specs = load_area_specs()
    return ProblemGenerationWorkflow(
        diagnosis=diagnosis,
        graph_context=graph_context,
        generator=ProblemGenerator(gateway, area_specs=area_specs),
        passage_generator=PassageGenerator(
            gateway,
            banned_topics=resolved_banned_topics,
        ),
        source_material_generator=SourceMaterialGenerator(
            gateway,
            banned_topics=resolved_banned_topics,
            area_specs=area_specs,
        ),
        literature_selector=build_literature_selector(resolved_verify_config),
        cross_solver=BlindCrossSolver(gateway),
        candidate_store=candidate_store,
        item_store=item_store,
        set_store=set_store or InMemoryProblemSetStore(),
        checkpointer=checkpointer,
        verify_config=resolved_verify_config,
        banned_topics=resolved_banned_topics,
        external_corpus=external_corpus or resolve_external_corpus(),
    )


__all__ = [
    "build_literature_selector",
    "build_problem_workflow",
    "resolve_external_corpus",
    "resolve_literature_pool",
]
