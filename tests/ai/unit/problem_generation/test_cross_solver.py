"""blind 교차 풀이 프롬프트의 정보 격리."""

import asyncio

import pytest
from fake_provider import FakeProvider

from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.llm import ModelRole, RedactionBlocked
from ai.contracts.problem_generation import (
    Answer,
    Choice,
    EvidenceAnchor,
    EvidenceKind,
    GeneratedItem,
    SolveResult,
)
from ai.contracts.taxonomy import AreaTag, ItemFormat, TypeTag
from ai.llm.determinism import LLM_SEED
from ai.llm.gateway import LlmGateway
from ai.problem_generation.application.cross_solver import BlindCrossSolver
from ai.runtime.redaction import RedactionResult, redact


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
    #: 🔴 **(8/13) `temperature`는 안 실린다** — `gpt-5.6-luna`가 기본값 외 값을 400으로
    #:   거부해 어댑터가 「값이 없으면 안 보낸다」로 흡수했다(99 #51). 종전 이 줄은
    #:   `== DETERMINISTIC_TEMPERATURE`로 **결정론 온도가 실린다**를 지키고 있었다.
    #:   재현 축은 이제 `seed` 하나다(8/4 실측 — 재현을 만든 것은 seed였다).
    assert generation_params.temperature is None
    assert generation_params.seed == LLM_SEED


def test_cross_solver_blocks_generated_item_with_person_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        # 🔴 한 문장에 인명 후보 **둘**(`김철수가`·`박영희를`) — 밀도 규칙상 그래야
        #   전송이 막힌다(`policies/masking_redaction.md` §2 [A 확정 7/23] · 후보 1개는
        #   토큰만 바꾸고 전송은 막지 않는다). 검사의 의도(「인명이 든 문항은 교차 풀이
        #   전에 fail-closed」)는 그대로다.
        stem="김철수가 박영희를 불렀다. 밑줄 친 표현으로 적절한 것을 고르시오.",
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
    observed: list[RedactionResult] = []

    def _record_redaction(text: str) -> RedactionResult:
        result = redact(text)
        observed.append(result)
        return result

    monkeypatch.setattr(
        "ai.problem_generation.application.cross_solver.redact",
        _record_redaction,
    )

    with pytest.raises(RedactionBlocked):
        asyncio.run(
            solver.solve(
                item=item,
                target_skill_node_id="grammar.node-1",
                execution_context=context,
            )
        )

    assert observed and observed[0].uncertain is True
    assert not provider.requests
