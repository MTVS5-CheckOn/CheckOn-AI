"""blind 교차 풀이 프롬프트의 정보 격리."""

import asyncio

from fake_provider import FakeProvider

from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import ModelRole
from ai.contracts.problem_generation import (
    Answer,
    Choice,
    EvidenceAnchor,
    EvidenceKind,
    GeneratedItem,
    SolveResult,
)
from ai.contracts.taxonomy import AreaTag, ItemFormat, TypeTag
from ai.llm.determinism import DETERMINISTIC_TEMPERATURE, LLM_SEED
from ai.llm.gateway import LlmGateway
from ai.problem_generation.application.cross_solver import BlindCrossSolver


def test_cross_solver_sends_only_blind_item() -> None:
    solve = SolveResult(
        chosen=1,
        reasoning="독립 풀이",
        confidence=0.9,
        target_skill_node_id="grammar.node-1",
        measured_skill_node_id="grammar.node-1",
        aligned=True,
        alignment_confidence=0.9,
        alignment_reason="목표와 일치",
    )
    provider = FakeProvider((solve.model_dump_json(),), name="fake-verifier")
    gateway = LlmGateway(
        {ModelRole.VERIFIER: provider},
        transport_retry={ModelRole.VERIFIER: 0},
    )
    solver = BlindCrossSolver(gateway)
    item = GeneratedItem(
        area_tag=AreaTag.LANGUAGE,
        type_tag=TypeTag.CONCEPT,
        item_format=ItemFormat.MCQ,
        skill_node_id="grammar.node-1",
        stem="다음 중 옳은 것을 고르시오.",
        choices=tuple(
            Choice(
                no=no,
                text=f"선지 {no}",
                why_wrong=None if no == 1 else f"오답 근거 {no}",
            )
            for no in range(1, 6)
        ),
        answer=Answer(correct_no=1),
        rationale="비공개 해설 원문",
        evidence=(
            EvidenceAnchor(
                kind=EvidenceKind.GRAMMAR_RULE,
                ref="grammar:rule-1",
            ),
        ),
    )
    context = ExecutionContext(
        execution_id="11111111-1111-1111-1111-111111111111",
        tenant_id="tenant-a",
        capability=Capability.PROBLEM_GENERATION,
        input_snapshot_hash="sha256:test",
        versions=VersionSet(
            pipeline_version="pipeline-v1",
            engine_version="engine-v1",
            schema_version="schema-v1",
            contract_version="contract-v1",
        ),
    )

    result = asyncio.run(
        solver.solve(
            item=item,
            target_skill_node_id="grammar.node-1",
            execution_context=context,
        )
    )

    assert result == solve
    prompt = provider.requests[0].prompt
    blind_json = prompt.split("[정답·해설·근거가 제거된 문항 JSON]\n", 1)[1].split(
        "\n\n[목표 메타데이터 JSON]",
        1,
    )[0]
    assert '"answer"' not in blind_json
    assert '"rationale"' not in blind_json
    assert '"evidence"' not in blind_json
    assert '"why_wrong"' not in blind_json
    assert "비공개 해설 원문" not in prompt
    assert "grammar:rule-1" not in prompt
    generation_params = provider.requests[0].generation_params
    assert generation_params is not None
    assert generation_params.temperature == DETERMINISTIC_TEMPERATURE
    assert generation_params.seed == LLM_SEED
