"""표준국어대사전 T1 최소 색인 빌더 계약."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ai.contracts.graphrag import ContextLockedFields, GraphContextRequest
from ai.contracts.problem_generation import TargetSource
from ai.contracts.taxonomy import AreaTag, ItemFormat, TypeTag
from ai.problem_generation.infrastructure.build_lexicon_index import (
    build_index,
    normalize_headword,
    write_index,
)
from ai.problem_generation.infrastructure.graph_context import (
    GrammarNormGraphContextService,
)
from ai.problem_generation.infrastructure.lexicon_index import (
    LexiconNodeMap,
    LexiconNodeRule,
)


def _mapping(*, max_evidence_count: int = 8) -> LexiconNodeMap:
    return LexiconNodeMap(
        version="test-v1",
        source_revision="stdict-test",
        required_cat="언어",
        max_evidence_count=max_evidence_count,
        nodes={
            "language.grammar.pronoun": LexiconNodeRule(
                label="대명사",
                headwords=("대-명사01", "대-명사02"),
                allowed_pos=("명사",),
            )
        },
    )


def _write_xml(path: Path) -> None:
    senses = "".join(
        f"""
        <sense_info>
          <sense_code>{sense_code}</sense_code>
          <definition>색인에 남으면 안 되는 뜻풀이 {sense_code}</definition>
          <cat_info><cat>{cat}</cat></cat_info>
        </sense_info>
        """
        for sense_code, cat in (
            ("30", "언어"),
            ("2", "언어"),
            ("11", "언어"),
            ("1", "법률"),
        )
    )
    path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<channel>
  <item>
    <target_code>100</target_code>
    <word_info>
      <word>대-명사01</word>
      <word_type>단어</word_type>
      <pos_info>
        <pos>명사</pos>
        <comm_pattern_info><grammar_info>명사처럼 쓰인다.</grammar_info></comm_pattern_info>
        {senses}
      </pos_info>
      <pos_info>
        <pos>동사</pos>
        <sense_info>
          <sense_code>3</sense_code>
          <definition>허용 품사 아님</definition>
          <cat_info><cat>언어</cat></cat_info>
        </sense_info>
      </pos_info>
    </word_info>
  </item>
</channel>
""",
        encoding="utf-8",
    )


def _write_missing_pos_xml(path: Path) -> None:
    path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<channel>
  <item>
    <target_code>200</target_code>
    <word_info>
      <word>대-명사02</word>
      <word_type>구</word_type>
      <pos_info>
        <pos>품사 없음</pos>
        <comm_pattern_info>
          <sense_info>
            <sense_code>4</sense_code>
            <definition>구 표제어의 뜻풀이</definition>
            <cat_info><cat>언어</cat></cat_info>
          </sense_info>
        </comm_pattern_info>
      </pos_info>
    </word_info>
  </item>
</channel>
""",
        encoding="utf-8",
    )


def test_headword_normalization_contract() -> None:
    assert normalize_headword("대-명사01") == "대명사"
    assert normalize_headword("문장^성분") == "문장성분"


def test_index_filters_cat_and_pos_then_sorts_and_caps_senses(tmp_path: Path) -> None:
    _write_xml(tmp_path / "sample.xml")

    index = build_index(tmp_path, _mapping(max_evidence_count=2))
    entries = index.nodes["language.grammar.pronoun"]

    assert [entry.sense_code for entry in entries] == ["2", "11"]
    assert all(entry.cat == "언어" and entry.pos == "명사" for entry in entries)
    assert all(entry.target_code == "100" for entry in entries)


def test_missing_pos_uses_exact_headword_and_cat_without_opening_pos_filter(
    tmp_path: Path,
) -> None:
    _write_xml(tmp_path / "with-pos.xml")
    _write_missing_pos_xml(tmp_path / "missing-pos.xml")

    entries = build_index(tmp_path, _mapping()).nodes["language.grammar.pronoun"]

    assert [entry.sense_code for entry in entries] == ["2", "4", "11", "30"]
    assert next(entry for entry in entries if entry.sense_code == "4").pos == "품사 없음"
    assert all(entry.sense_code != "3" for entry in entries)


def test_definition_is_read_but_never_persisted(tmp_path: Path) -> None:
    _write_xml(tmp_path / "sample.xml")
    output = tmp_path / "index.json"

    write_index(build_index(tmp_path, _mapping()), output)
    raw = output.read_text(encoding="utf-8")
    payload = json.loads(raw)

    assert "뜻풀이" not in raw
    assert "definition" not in raw
    assert "example" not in raw
    assert set(payload["nodes"]["language.grammar.pronoun"][0]) == {
        "node_id",
        "target_code",
        "sense_code",
        "word",
        "pos",
        "cat",
        "word_type",
        "grammar_info",
        "source_revision",
        "content_hash",
    }


def test_lexicon_context_exposes_only_approved_headword_facts() -> None:
    node_id = "language.grammar.pronoun"
    context = asyncio.run(
        GrammarNormGraphContextService().resolve_generation_context(
            GraphContextRequest(
                tenant_id="tenant-lexicon-index",
                target_source=TargetSource.TEACHER_MANUAL,
                target_skill_node_ids=(node_id,),
                locked_fields=ContextLockedFields(
                    target_ref="student-lexicon-index",
                    area_tag=AreaTag.LANGUAGE,
                    type_tags=(TypeTag.CONCEPT,),
                    skill_node_id=node_id,
                    item_format=ItemFormat.MCQ,
                ),
                policy_constraints={"evidence_required": True},
            )
        )
    )

    refs = context.retrieval_trace["allowed_evidence_refs"]
    anchors = context.retrieval_trace["evidence_anchors"]
    assert isinstance(refs, list) and refs
    assert all(isinstance(ref, str) and ref.startswith("stdict:") for ref in refs)
    assert isinstance(anchors, list) and anchors
    anchor = anchors[0]
    assert isinstance(anchor, dict)
    assert anchor["quote"] == anchor["word"]
    assert {
        "word",
        "pos",
        "cat",
        "word_type",
        "sense_code",
        "source_revision",
    } <= anchor.keys()
    assert "definition" not in json.dumps(anchor, ensure_ascii=False)
