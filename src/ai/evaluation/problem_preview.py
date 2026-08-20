"""문항 생성 미리보기 러너 — 실제로 뽑아 보고 눈으로 판정하기 위한 CLI.

🔴 **평가 러너지 프로덕션 경로가 아니다.** 조립은 `build_problem_workflow` 정본을 그대로
쓰고(우회 조립 금지), 저장은 전부 in-memory라 PG 없이 돈다. 여기서 만든 문항은 저장소·
원장에 남지 않는다.

세 가지 모드가 있다.

- ``--no-llm`` : LLM을 부르지 않고 **무엇이 들어갈지**만 본다. 문학은 결정론 선택기가 고른
  작품·구간을 그대로 보여 준다 — 풀을 늘린 뒤 **어느 작품이 뽑히는지** 확인하는 자리다.
- 기본       : 실 LLM으로 끝까지 돌리고 문항·게이트 판정을 출력한다.
- ``--json`` : 같은 내용을 기계가 읽을 형태로 덤프한다(``--out`` 지정 시 파일로).

⚠ **출력물은 커밋하지 않는다.** ``--out`` 기본 위치는 `local_data/`(반입 금지 구역)다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from langgraph.checkpoint.memory import InMemorySaver

from ai.contracts.diagnosis import (
    CellVerdict,
    DiagnosisResult,
    DiagnosisStatus,
    NodeVerdict,
    WeaknessCell,
    WeaknessMap,
    WeaknessNode,
)
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.contracts.problem_generation import (
    GeneratedItem,
    LiteratureGenre,
    MediaSourceKind,
    MediaSourceRequest,
    PassageDomain,
    PassageRequest,
    ProblemRequest,
    ProblemSetResult,
    SentenceComplexity,
    SourceRequest,
    SpeechWritingSourceKind,
    SpeechWritingSourceRequest,
    TargetKind,
    TargetSource,
    WorkSelection,
)
from ai.contracts.taxonomy import AreaTag, ItemFormat, TypeTag
from ai.llm.prompts.loader import load_prompt_template
from ai.llm.providers.openai_compat import get_llm_settings
from ai.problem_generation.application.workflow import DiagnosisCallable
from ai.problem_generation.bootstrap import (
    build_literature_selector,
    build_problem_workflow,
    resolve_external_corpus,
)
from ai.problem_generation.domain.models import StoredProblemItem
from ai.problem_generation.infrastructure.config import load_verify_config
from ai.problem_generation.infrastructure.grammar_norm import (
    load_grammar_norm_corpus,
    select_node_rows,
)
from ai.problem_generation.infrastructure.graph_context import (
    AreaDelegatingGraphContextService,
)
from ai.problem_generation.infrastructure.lexicon_index import (
    load_lexicon_index,
    select_node_entries,
)
from ai.problem_generation.infrastructure.memory_store import (
    InMemoryCandidateStore,
    InMemoryProblemItemStore,
)
from ai.problem_generation.provider import build_problem_gateway
from ai.runtime.real_llm import real_llm_skip_reason

_SNAPSHOT_HASH = "sha256:problem-preview-snapshot"
_TAXONOMY_VERSION = "v1"
_GRAPH_VERSION = "curriculum-five-area-v1"
_DEFAULT_OUT_DIR = Path("local_data")

#: 영역별 기본 목표 노드. `--node` 로 덮을 수 있다.
_DEFAULT_NODES: dict[AreaTag, str] = {
    AreaTag.LANGUAGE: "language.grammar.phonological_change",
    AreaTag.READING: "reading.reasoning.inference",
    AreaTag.LITERATURE: "literature.structure.composition",
    AreaTag.SPEECH_WRITING: "speech_writing.speech.audience",
    AreaTag.MEDIA: "media.reception.intent",
}


class PreviewUnavailable(RuntimeError):
    """실 LLM 전제나 요청 조합이 갖춰지지 않아 미리보기를 돌릴 수 없음."""


@dataclass(frozen=True, slots=True)
class PreviewOptions:
    area_tag: AreaTag
    node_id: str
    count: int
    genre: LiteratureGenre
    era: str | None
    keywords: tuple[str, ...]
    domain: PassageDomain
    topic: str | None
    use_llm: bool


def _source_request(options: PreviewOptions) -> SourceRequest | None:
    """영역별 필수 자료 요청 — 없으면 문 앞에서 400으로 막히는 값이다(04 §2.3)."""

    if options.area_tag is AreaTag.READING:
        return PassageRequest(
            domain=options.domain,
            topic_hint=options.topic,
            word_count=500,
            sentence_complexity=SentenceComplexity.STANDARD,
            paragraph_count=2,
            banned_topics_version="pg-banned-v1",
        )
    if options.area_tag is AreaTag.SPEECH_WRITING:
        return SpeechWritingSourceRequest(
            source_kind=SpeechWritingSourceKind.PRESENTATION,
            topic_hint=options.topic,
            banned_topics_version="pg-banned-v1",
        )
    if options.area_tag is AreaTag.MEDIA:
        return MediaSourceRequest(
            source_kind=MediaSourceKind.PAIRED,
            topic_hint=options.topic,
            banned_topics_version="pg-banned-v1",
        )
    return None


def _work_selection(options: PreviewOptions) -> WorkSelection | None:
    if options.area_tag is not AreaTag.LITERATURE:
        return None
    return WorkSelection(
        genre=options.genre,
        era=options.era,
        concept_keywords=options.keywords,
    )


def _request(options: PreviewOptions) -> ProblemRequest:
    stamp = datetime.now(UTC).strftime("%H%M%S")
    return ProblemRequest(
        request_id=f"req-preview-{options.area_tag.value}-{stamp}",
        idempotency_key=f"idem-preview-{options.area_tag.value}-{stamp}",
        tenant_id="tenant-preview",
        target_kind=TargetKind.STUDENT,
        target_ref="student-preview",
        target_source=TargetSource.TEACHER_MANUAL,
        manual_targets=(options.node_id,),
        snapshot_hash=_SNAPSHOT_HASH,
        taxonomy_version=_TAXONOMY_VERSION,
        area_tag=options.area_tag,
        type_tags=(TypeTag.INFER,),
        item_format=ItemFormat.MCQ,
        count=options.count,
        passage=_source_request(options),
        work_selection=_work_selection(options),
    )


def _execution_context() -> ExecutionContext:
    return ExecutionContext(
        execution_id=UUID("44444444-4444-4444-8444-444444444444"),
        tenant_id="tenant-preview",
        capability=Capability.PROBLEM_GENERATION,
        input_snapshot_hash=_SNAPSHOT_HASH,
        versions=VersionSet(
            pipeline_version="pipeline-v1",
            engine_version="engine-v1",
            schema_version="schema-v1",
            contract_version="contract-v1",
            prompt_version=load_prompt_template("pg.items.v1").version,
            graph_version=_GRAPH_VERSION,
            taxonomy_version=_TAXONOMY_VERSION,
            verify_config_version="verify-config.v1",
        ),
    )


def _diagnosis_for(options: PreviewOptions) -> DiagnosisCallable:
    cell_key = f"{options.area_tag.value}×infer"

    async def diagnose(request: ProblemRequest) -> DiagnosisResult:
        return DiagnosisResult(
            status=DiagnosisStatus.GENERATED,
            weakness_map=WeaknessMap(
                graph_version=_GRAPH_VERSION,
                taxonomy_version=_TAXONOMY_VERSION,
                config_version="verify-config.v1",
                snapshot_hash=_SNAPSHOT_HASH,
                cells={
                    cell_key: WeaknessCell(
                        acc=0.4, n=10, verdict=CellVerdict.WEAK, severity=0.8
                    )
                },
                nodes={
                    options.node_id: WeaknessNode(
                        verdict=NodeVerdict.WEAK_CONFIRMED,
                        basis=(f"cell:{cell_key}",),
                    )
                },
            ),
        )

    return diagnose


def _preview_source(options: PreviewOptions) -> dict[str, Any]:
    """LLM 없이 확인 가능한 입력 — 문학은 결정론 선택 결과가 곧 자료다."""

    if options.area_tag is AreaTag.LANGUAGE:
        # 문법은 자료 요청이 아니라 승인 근거를 쓴다. ⚠ **두 축이다** — 어문 규범 행이
        # 없으면 `GrammarNormGraphContextService` 가 **어휘 색인으로 넘어간다**. 한쪽만
        # 보여 주면 「근거 0건」으로 잘못 읽힌다.
        rows = select_node_rows(load_grammar_norm_corpus(), options.node_id)
        if rows:
            return {
                "kind": "grammar_norm_rows",
                "rows": [
                    {
                        "regulation": f"{row.regulation_code} {row.regulation_no}",
                        "title": row.title,
                        "keyword": row.keyword,
                    }
                    for row in rows
                ],
            }
        entries = select_node_entries(load_lexicon_index(), options.node_id)
        return {
            "kind": "lexicon_entries",
            "rows": [
                {
                    "regulation": entry.sense_code,
                    "keyword": entry.word,
                    "title": f"{entry.pos}·{entry.cat}",
                }
                for entry in entries
            ],
        }
    if options.area_tag is not AreaTag.LITERATURE:
        request = _source_request(options)
        return {
            "kind": "generated_source_request",
            "request": request.model_dump(mode="json") if request else None,
        }
    selection = _work_selection(options)
    assert selection is not None
    excerpt = build_literature_selector(load_verify_config()).select(selection)
    return {
        "kind": "selected_work",
        "slug": excerpt.slug,
        "title": excerpt.title,
        "author": excerpt.author,
        "era": excerpt.era,
        "genre": excerpt.genre.value,
        "source_ref": excerpt.source_ref,
        "revision_id": excerpt.revision_id,
        "span": [excerpt.start, excerpt.end],
        "quote": excerpt.quote,
    }


async def _run_llm(
    options: PreviewOptions,
) -> tuple[ProblemSetResult, tuple[StoredProblemItem, ...], float, list[tuple[str, int]]]:
    verify_config = load_verify_config()
    item_store = InMemoryProblemItemStore()
    # 🔴 **prompt_id 와 latency_ms 까지 남긴다** — 종전에는 role 문자열만
    #   담아 「모두 몇 번」만 알 수 있었다. 자리별 상한(`llm/call_timeouts.yaml`)은
    #   **자리별 실측 p95** 에서 나오므로, role 축으로는 근거가 안 된다
    #   (counsel 의 plan 과 write 가 같은 role 이라는 것과 같은 사정이다).
    calls: list[tuple[str, int]] = []
    gateway = build_problem_gateway(
        verify_config=verify_config,
        recorder=lambda record, _context: calls.append(
            (record.prompt_id, record.latency_ms)
        ),
    )
    workflow = build_problem_workflow(
        gateway=gateway,
        graph_context=AreaDelegatingGraphContextService(),
        diagnosis=_diagnosis_for(options),
        candidate_store=InMemoryCandidateStore(),
        item_store=item_store,
        checkpointer=InMemorySaver(),
        verify_config=verify_config,
    )
    started = time.perf_counter()
    outcome = await workflow.run(_request(options), _execution_context())
    elapsed = time.perf_counter() - started
    if not isinstance(outcome, ProblemSetResult):
        raise PreviewUnavailable(f"문항 세트로 끝나지 않았다: {type(outcome).__name__}")
    # 생성 본문은 `ItemResult` 가 아니라 저장소에 있다 — 결과는 판정 메타만 싣는다.
    stored = tuple(await item_store.list_all())
    return outcome, stored, elapsed, calls


def _render_item(index: int, item: GeneratedItem) -> list[str]:
    lines = [
        f"  [{index}] {item.stem}",
    ]
    for choice in item.choices:
        mark = "★" if choice.no == item.answer.correct_no else " "
        lines.append(f"      {mark} {choice.no}. {choice.text}")
        if choice.why_wrong:
            lines.append(f"           └ 오답 근거: {choice.why_wrong}")
    lines.append(f"      해설: {item.rationale}")
    for anchor in item.evidence:
        quote = anchor.quote or ""
        shown = quote if len(quote) <= 160 else f"{quote[:160]}…"
        lines.append(f"      근거[{anchor.kind.value}] {anchor.ref}")
        if shown:
            lines.append(f"           “{shown}”")
    return lines


def _render(options: PreviewOptions, payload: dict[str, Any]) -> str:
    lines = [
        "",
        f"■ 영역 {options.area_tag.value} · 노드 {options.node_id} · {options.count}문항",
    ]
    source = payload["source"]
    if source["kind"] == "selected_work":
        lines += [
            f"■ 선택 작품  {source['title']} — {source['author']} "
            f"({source['era']}·{source['genre']})",
            f"   리비전 {source['revision_id']} · 구간 {source['span'][0]}~{source['span'][1]}",
            "   " + source["quote"].replace("\n", "\n   "),
        ]
    elif source["kind"] in {"grammar_norm_rows", "lexicon_entries"}:
        rows = source["rows"]
        label = (
            "어문 규범 근거"
            if source["kind"] == "grammar_norm_rows"
            else "어휘 색인 근거"
        )
        lines.append(f"■ {label}  {len(rows)}건")
        if not rows:
            lines.append("   🔴 근거 0행 — 이 노드는 지금 출제 재료가 없다")
        for row in rows:
            lines.append(f"   · [{row['regulation']}] {row['keyword']} — {row['title']}")
    elif source["request"] is not None:
        lines.append(f"■ 자료 요청  {json.dumps(source['request'], ensure_ascii=False)}")

    result = payload.get("result")
    if result is None:
        lines += ["", "▶ --no-llm — 여기까지가 LLM 없이 확인되는 입력이다.", ""]
        return "\n".join(lines)

    lines += [
        "",
        f"■ 결과  status={result['status']} · {payload['elapsed_s']:.1f}s "
        f"· LLM 호출 {payload['llm_calls']}회",
    ]
    if result["dropped_reasons"]:
        lines.append(f"   탈락 사유: {', '.join(result['dropped_reasons'])}")
    for item in payload["items"]:
        lines.append("")
        lines += item["rendered"]
        lines.append(
            f"      판정: {item['status']}"
            + (f" · 검수사유 {item['review_reason']}" if item["review_reason"] else "")
            + (f" · 난이도 {item['difficulty_band']}" if item["difficulty_band"] else "")
        )
        # 실패 사유를 안 찍으면 「문항은 좋은데 왜 failed인지」를 사람이 못 읽는다.
        if item["failure_reason"] or item["failure_detail"]:
            lines.append(
                f"      🔴 실패: {item['failure_reason'] or '-'}"
                + (f" — {item['failure_detail']}" if item["failure_detail"] else "")
            )
    lines.append("")
    return "\n".join(lines)


def _collect(options: PreviewOptions) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "area_tag": options.area_tag.value,
        "node_id": options.node_id,
        "count": options.count,
        "external_corpus": (
            "주입됨" if resolve_external_corpus() is not None else "미설정(R-8 미수행)"
        ),
        "source": _preview_source(options),
        "result": None,
        "items": [],
    }
    if not options.use_llm:
        return payload

    reason = real_llm_skip_reason(get_llm_settings().openai_base_url)
    if reason is not None:
        raise PreviewUnavailable(reason)

    result, stored, elapsed, calls = asyncio.run(_run_llm(options))
    items_by_slot = {record.slot_index: record.item for record in stored}
    payload["elapsed_s"] = elapsed
    payload["llm_calls"] = len(calls)
    #: ⚠ 자리별 상한을 정하려면 합계가 아니라 **콜 단위**가 필요하다.
    payload["llm_call_detail"] = [
        {"prompt_id": prompt_id, "latency_ms": latency} for prompt_id, latency in calls
    ]
    payload["result"] = {
        "status": result.status.value,
        "set_id": str(result.set_id),
        "dropped_reasons": [reason.value for reason in result.dropped_reasons],
    }
    for index, entry in enumerate(result.items):
        item = items_by_slot.get(index)
        rendered = (
            _render_item(index + 1, item)
            if item is not None
            else [
                f"  [{index + 1}] (문항 없음)"
                + (f" — {entry.failure_detail}" if entry.failure_detail else "")
            ]
        )
        payload["items"].append(
            {
                "status": entry.status.value,
                "failure_reason": (
                    entry.failure_reason.value if entry.failure_reason else None
                ),
                "failure_detail": entry.failure_detail,
                "review_reason": (
                    entry.review_reason.value if entry.review_reason else None
                ),
                "difficulty_band": (
                    entry.difficulty_band.value if entry.difficulty_band else None
                ),
                "item": item.model_dump(mode="json") if item else None,
                "rendered": rendered,
            }
        )
    return payload


def _options(namespace: argparse.Namespace) -> PreviewOptions:
    area_tag = AreaTag(namespace.area)
    return PreviewOptions(
        area_tag=area_tag,
        node_id=namespace.node or _DEFAULT_NODES[area_tag],
        count=namespace.count,
        genre=LiteratureGenre(namespace.genre),
        era=namespace.era,
        keywords=tuple(namespace.keyword or ()),
        domain=PassageDomain(namespace.domain),
        topic=namespace.topic,
        use_llm=not namespace.no_llm,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="문항 생성 미리보기 — 실제로 뽑아 보고 눈으로 판정한다",
    )
    parser.add_argument(
        "--area",
        default=AreaTag.LITERATURE.value,
        choices=[tag.value for tag in AreaTag],
        help="출제 영역",
    )
    parser.add_argument("--node", default=None, help="목표 스킬 노드 (기본: 영역별 대표 노드)")
    parser.add_argument("--count", type=int, default=1, help="문항 수")
    parser.add_argument(
        "--genre",
        default=LiteratureGenre.MODERN_NOVEL.value,
        choices=[genre.value for genre in LiteratureGenre],
        help="문학 갈래",
    )
    parser.add_argument("--era", default=None, help="문학 시대 (예: 근대·조선)")
    parser.add_argument(
        "--keyword", action="append", default=None, help="문학 개념어 (여러 번 지정 가능)"
    )
    parser.add_argument(
        "--domain",
        default=PassageDomain.SCIENCE.value,
        choices=[domain.value for domain in PassageDomain],
        help="독서 지문 분야",
    )
    parser.add_argument("--topic", default=None, help="지문·자료 주제 힌트")
    parser.add_argument(
        "--no-llm", action="store_true", help="LLM을 부르지 않고 입력만 확인한다"
    )
    parser.add_argument("--json", action="store_true", help="JSON으로 출력한다")
    parser.add_argument(
        "--out",
        default=None,
        help=f"결과를 파일로 저장한다 (기본 위치 {_DEFAULT_OUT_DIR}/ — 커밋 금지 구역)",
    )
    namespace = parser.parse_args()
    options = _options(namespace)

    try:
        payload = _collect(options)
    except PreviewUnavailable as error:
        raise SystemExit(f"미리보기 불가 — {error}") from error

    text = (
        json.dumps(payload, ensure_ascii=False, indent=2)
        if namespace.json
        else _render(options, payload)
    )
    print(text)
    if namespace.out:
        path = Path(namespace.out)
        if not path.is_absolute() and path.parent == Path("."):
            path = _DEFAULT_OUT_DIR / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"저장: {path}")


if __name__ == "__main__":
    main()
