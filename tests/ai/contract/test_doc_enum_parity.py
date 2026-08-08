"""🔴 **문서에 적힌 것과 코드가 같다 — 문서를 실제로 읽어서** (99 #04 첫 적용 · #07).

**두 축을 본다:**

    ⓐ **값 집합**   문서의 표·ERD 주석 ↔ `StrEnum` 멤버
    ⓑ **필드 집합** 문서의 python 코드블록 ↔ `BaseModel.model_fields`   (8/11 · #07)

⚠ **파일명이 `_enum_parity`라 ⓐ만 있는 것처럼 읽힌다.** **개명하지 않았다** — 이 경로를
99·커밋·PR이 이미 참조하고 있어 개명하면 이력이 끊긴다(㉑에서 같은 판단을 했다). 대신
**이름이 좁다는 사실을 여기 적는다** — 이름을 못 고칠 때 할 수 있는 것이 그것이다.

⚠ **손으로 옮긴 기대 집합을 여기 두지 않는다.** 그건 *"문서와 대조한다"* 가 아니라
**두 번째 사본**을 만드는 것이고, 문서가 바뀌어도 영원히 초록이다.
🔴 **(8/11 인용 갱신)** 종전 이 자리는 `test_counsel_state.py::
test_state_fields_match_doc_exactly`를 *"정확히 그 형태다 — 그 파일에 **한계**를 적어
뒀다"* 로 가리켰다. **#154가 그 파일을 고쳐 이제 「한계」가 아니라 「다른 파일이 지킨다」
이고, 그 「다른 파일」이 바로 여기다** — ⓑ가 그 대조를 든다. **인용은 원문이 바뀌면 같이
바뀌어야 한다**(#12가 세운 규칙인데 같은 PR에서 이 자리를 안 봤다).

## 왜 필요했나

#146이 `PlanOutcome`에 값을 하나 넣고 `langgraph_state.md`를 *"다섯 가지"* 로 고쳤는데
**계약·refine·stores 세 자리의 산문이 「넷」으로 남았다.** 그때 아무 테스트도 안 걸렸다.

🔴 **다만 이 파일이 그 누락을 잡았을 것은 아니다** — 그 셋은 **docstring 산문**이고 값
집합이 아니다. 이 파일이 잡는 축은 **「문서 표의 값 집합 ↔ enum 멤버」** 이고, #146은
그 축이 **우연히 맞은** 경우다(둘 다 고쳤다). **산문은 여전히 아무도 안 본다** — 아래
「못 보는 것」 참조.

## 🔴 이 검사가 못 보는 것 (로그 67 — 목록형을 자백한다)

  · **산문 열거를 못 본다.** *"네 경우"*·*"셋 중 하나"* 같은 문장은 값 집합이 아니라
    파싱 대상이 아니다. #146이 놓친 세 자리가 정확히 그 형태다.
  · **대상이 목록형이다.** 아래 `_TABLE_PAIRS`·`_ERD_PAIRS`에 적은 쌍만 본다 — 새 enum이
    문서에 적혀도 여기 등재하지 않으면 안 걸린다. **전칭이 불가능한 대상**이라
    (문서마다 표기 형식이 다르다) 의식적으로 목록형을 골랐고, 대신
    **「목록이 비면 실패」**(`test_the_scan_finds_...`)로 검사 경로 절단을 막는다.
  · **ERD의 값 목록이 아닌 주석은 대상이 아니다** — `lease_owner "leased|running에서만"`,
    `error_code "failed|cancelled 사유"` 처럼 `|`가 있어도 **설명 문장**인 것이 있다.
    그래서 컬럼명을 명시 등재한다(정규식으로 `|`를 싹 훑으면 거짓 쌍이 생긴다).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path
from typing import Final, NamedTuple

import pytest
from pydantic import BaseModel

from ai.composition.counsel.state import CounselPackState
from ai.contracts.agents import JobPhase, PriorityClass, WorkerKind
from ai.contracts.composition import BlockType, DraftKind, DraftStatus, PlanOutcome
from ai.contracts.detection import Lifecycle
from ai.contracts.execution import Capability
from ai.contracts.gates import GateName, OwnerKind
from ai.contracts.llm import CallOutcome
from ai.contracts.problem_generation import (
    ProblemItemStatus,
    ProblemSetStatus,
    RevisionKind,
    TargetKind,
    TargetSource,
)
from ai.contracts.taxonomy import V1_TYPE_TAGS
from ai.detection.segments import Segment
from ai.evidence.models import EvidenceOwnerKind
from ai.import_mapping.probe.state import MappingProbeState

_DOCS: Final = Path(__file__).resolve().parents[3] / "docs"


class _Pair(NamedTuple):
    """문서의 값 집합 하나와 그것이 정본으로 삼는 값들.

    🔴 **정본이 enum 전체가 아닐 수 있다**(B 확장 8/9). `PROBLEM_ITEM.type_tag`는
    **우리가 산출한 문항**이 들어가는 컬럼이라 값 영역이 `V1_TYPE_TAGS`(예약 뺀 4종)이지
    `TypeTag`(5종)가 아니다 — 요청 문이 예약 태그를 400으로 끊으므로 **그 컬럼에 `apply`가
    들어갈 경로가 없다.** 그래서 `members`는 enum 클래스만이 아니라 **그 부분집합**도 받는다.
    ⚠ 여기서 `TypeTag`를 걸면 red가 나는데, 그건 문서가 틀려서가 아니라 **쌍을 잘못 맺어서**다.
    """

    label: str
    doc: Path
    #: enum 클래스(전체) 또는 멤버 부분집합 — 둘 다 멤버를 순회할 수 있다.
    members: Iterable[StrEnum]
    #: ERD 쌍에서 컬럼이 사는 **테이블**. 🔴 컬럼명만으로는 못 고른다 —
    #: `varchar status`가 06_erd.md에 **여덟 곳**(AGENT_RUN·DRAFT·PROBLEM_SET·
    #: PROBLEM_ITEM·LABEL_SUGGESTION·MAPPING_SPEC·IMPORT_JOB·TAG_SUGGESTION)이다. 앵커가 없으면
    #: 정규식이 **파일에서 먼저 나오는 테이블**을 집고, 그건 ERD를 재배열하는 순간
    #: 조용히 다른 표와 대조하게 된다(B 확장 8/9).
    table: str = ""
    #: 실패 메시지에 쓰는 정본 이름. 부분집합은 클래스가 아니라 `__name__`이 없다.
    source: str = ""

    @property
    def expected(self) -> set[str]:
        return {member.value for member in self.members}

    @property
    def origin(self) -> str:
        return self.source or getattr(self.members, "__name__", str(self.members))


# ── ⓐ 마크다운 표 — 첫 열이 백틱으로 감싼 값 ──────────────────────

#: `> | \`value\` | 뜻 | 대응 |` 형태의 인용 표에서 첫 열 값을 뽑는다.
_QUOTED_TABLE_CELL: Final = re.compile(r"^>\s*\|\s*`([a-z_]+)`\s*\|", re.MULTILINE)

#: 표가 사는 절을 잘라내는 앵커 — 문서 전체를 훑으면 다른 표의 값이 섞인다.
_TABLE_PAIRS: Final = (
    (
        _Pair("plan_outcome", _DOCS / "policies" / "langgraph_state.md", PlanOutcome),
        "`plan_outcome`·`plan_dropped`의 단위와 근거",
        "🔴 **분모는 잡이다.**",
    ),
)


def _table_values(doc: Path, start: str, end: str) -> set[str]:
    """`start`와 `end` 사이 구간의 인용 표에서 첫 열 값을 모은다."""
    text = doc.read_text(encoding="utf-8")
    i = text.find(start)
    j = text.find(end, i + 1) if i >= 0 else -1
    if i < 0 or j < 0:
        return set()
    return set(_QUOTED_TABLE_CELL.findall(text[i:j]))


# ── ⓒ python 코드블록 — `class X(BaseModel):` 의 필드 이름 (99 #07) ──


class _FieldPair(NamedTuple):
    """문서 코드블록의 필드 집합과 그것이 정본으로 삼는 모델."""

    label: str
    doc: Path
    heading: str
    class_name: str
    model: type[BaseModel]


#: 코드블록 안의 `    name: type` 한 줄 — 들여쓰기 4칸이 클래스 본문 필드다.
#: ⚠ 주석 줄(`# …`)·빈 줄·중첩 클래스 선언은 자연히 안 걸린다.
_FIELD_LINE: Final = re.compile(r"^ {4}(\w+)\s*:", re.MULTILINE)

_FIELD_PAIRS: Final = (
    _FieldPair(
        "counsel_pack_state",
        _DOCS / "policies" / "langgraph_state.md",
        "### 1.2 State (Pydantic)",
        "CounselPackState",
        CounselPackState,
    ),
    _FieldPair(
        "mapping_probe_state",
        _DOCS / "policies" / "langgraph_state.md",
        "### 2.2 State",
        "MappingProbeState",
        MappingProbeState,
    ),
)


def _codeblock_fields(doc: Path, heading: str, class_name: str) -> set[str]:
    """`heading` 뒤 첫 python 코드블록에서 `class_name`의 필드 이름을 뽑는다.

    🔴 **클래스 경계를 지킨다** — 같은 코드블록에 클래스가 둘 이상 있다(§1.2는
    `StudentResult` + `CounselPackState`). 블록 전체를 훑으면 남의 필드가 섞인다.
    """
    text = doc.read_text(encoding="utf-8")
    start = text.find(heading)
    if start < 0:
        return set()
    open_fence = text.find("```", start)
    close_fence = text.find("```", open_fence + 3) if open_fence >= 0 else -1
    if open_fence < 0 or close_fence < 0:
        return set()
    block = text[text.find("\n", open_fence) + 1 : close_fence]

    marker = f"class {class_name}"
    class_at = block.find(marker)
    if class_at < 0:
        return set()
    tail = block[block.find("\n", class_at) + 1 :]
    # 다음 최상위 선언(`class …`)에서 자른다 — 들여쓰기 0이 클래스 본문의 끝이다.
    next_class = re.search(r"^\S", tail, re.MULTILINE)
    body = tail[: next_class.start()] if next_class else tail
    return set(_FIELD_LINE.findall(body))


# ── ⓑ mermaid ERD — `varchar col "a|b|c"` ─────────────────────────

#: 🔴 **컬럼명을 명시 등재한다** — `|`가 있는 주석 중 **값 목록이 아닌 것**이 있다
#: (`lease_owner "leased|running에서만"`). 정규식으로 싹 훑으면 거짓 쌍이 생긴다.
_ERD_PAIRS: Final = (
    _Pair("capability", _DOCS / "06_erd.md", Capability, "AI_RUN"),
    _Pair("agent_kind", _DOCS / "06_erd.md", WorkerKind, "AGENT_RUN"),
    _Pair("priority_class", _DOCS / "06_erd.md", PriorityClass, "AGENT_RUN"),
    _Pair("status", _DOCS / "06_erd.md", JobPhase, "AGENT_RUN"),
    # [PART_B] 같은 `status` 컬럼명이 세 테이블에 있다 — 앵커가 이 둘을 가능하게 한다.
    _Pair("status", _DOCS / "06_erd.md", ProblemSetStatus, "PROBLEM_SET"),
    _Pair("status", _DOCS / "06_erd.md", ProblemItemStatus, "PROBLEM_ITEM"),
    # 🔴 **정본이 enum 전체가 아닌 유일한 쌍** — 산출 문항의 값 영역은 예약을 뺀 4종이다.
    _Pair(
        "type_tag", _DOCS / "06_erd.md", V1_TYPE_TAGS, "PROBLEM_ITEM", "V1_TYPE_TAGS"
    ),
    # ── 🔴 갈림이 실재한 둘 (8/8 · 99 #17) ─────────────────────────
    # 쌍을 **먼저 걸어 red를 보고** ERD를 고쳤다 — 한 커밋에 넣으면 *"원래 초록이었다"* 와
    # 구분이 안 된다. 커밋 순서가 증거다.
    _Pair("outcome", _DOCS / "06_erd.md", CallOutcome, "LLM_CALL"),
    _Pair("segment", _DOCS / "06_erd.md", Segment, "FEATURE_WEEK"),
    # ── A 축 일곱 (8/8 · 지금 일치라 등재만으로 잠긴다) ────────────
    # ⚠ `evidence/models.py`·`contracts/gates.py`는 **양자 파일이지만 import만 한다** —
    #   편집이 아니라 승인이 필요 없다(`Capability`를 `contracts/execution.py`에서
    #   가져오는 기존 쌍이 같은 형태다).
    _Pair("lifecycle", _DOCS / "06_erd.md", Lifecycle, "SIGNAL"),
    _Pair("owner_kind", _DOCS / "06_erd.md", EvidenceOwnerKind, "EVIDENCE_ITEM"),
    _Pair("kind", _DOCS / "06_erd.md", DraftKind, "DRAFT"),
    _Pair("status", _DOCS / "06_erd.md", DraftStatus, "DRAFT"),
    _Pair("block_type", _DOCS / "06_erd.md", BlockType, "DRAFT_BLOCK"),
    _Pair("owner_kind", _DOCS / "06_erd.md", OwnerKind, "GATE_RESULT"),
    # ⚠ `GateName`은 값이 **PascalCase**다(`Consent`·`DataSufficiency`…). 다른 enum과
    #   표기가 다르지만 **와이어 값은 영구**라 소문자로 통일하지 않는다.
    _Pair("gate_name", _DOCS / "06_erd.md", GateName, "GATE_RESULT"),
    # ── [PART_B] 축 셋 (8/8 · B가 값까지 대조해 승인 · 99 #17) ──────
    # *"흔들 예정 없습니다. 오히려 지금 잠가 두는 게 낫습니다 — 그 표면을 구현할 때 ERD를
    #  같이 고치게 되는 게 의도한 바입니다."*  ⇒ 소유자 동의를 받고 잠근다.
    _Pair("target_kind", _DOCS / "06_erd.md", TargetKind, "PROBLEM_SET"),
    _Pair("target_source", _DOCS / "06_erd.md", TargetSource, "PROBLEM_SET"),
    _Pair("revision_kind", _DOCS / "06_erd.md", RevisionKind, "ITEM_REVISION"),
    # ── 신설 테이블의 유일한 값목록 컬럼 (8/8 · 99 ㉕ · 커버리지 19/44 → 20/45) ──────
    # ⚠ 앵커가 **필수**다 — `plan_outcome`은 `langgraph_state.md` 표에서도 대조 중이라
    #   앵커가 없으면 정규식이 엉뚱한 블록을 집는다(A 본문 설계 §3.1).
    _Pair("plan_outcome", _DOCS / "06_erd.md", PlanOutcome, "COUNSEL_PACK_RESULT"),
)

#: 값 목록 뒤에 붙는 설명을 자르는 구분자. 🔴 이 저장소의 기존 표기 관례다 —
#: `lifecycle "new|ongoing|follow_up — AI 경보 생애 판정, 09 §4"`. 안 자르면 마지막 값에
#: 산문이 들러붙어 **문서가 맞는데 red**가 난다(B 확장 8/9).
_ERD_NOTE_SEPARATOR: Final = " — "


def _erd_values(doc: Path, column: str, table: str) -> set[str]:
    """`<table> { … varchar <column> "a|b|c — 설명" … }` 주석의 값 집합.

    🔴 **테이블 블록 안에서만 찾는다.** 파일 전체에서 첫 일치를 집으면 같은 컬럼명을 쓰는
    다른 테이블을 조용히 대조한다 — `status`가 그 형태다.
    """
    text = doc.read_text("utf-8")
    block = re.search(rf"^\s*{table} \{{(.*?)^\s*\}}", text, re.M | re.S)
    if block is None:
        return set()
    match = re.search(rf'^\s*varchar {column} "([^"]+)"', block.group(1), re.M)
    if match is None:
        return set()
    # 🔴 **`.strip()`이 필요하다**(8/8 · B가 A에게 넘긴 건). `split(sep, 1)[0]`만으로는
    #    꼬리 앞 공백이 **둘 이상**일 때 마지막 값에 공백이 들러붙어 **문서가 맞는데
    #    red**가 난다. ⚠ 지금 `06_erd.md`의 ` — ` 주석은 공백이 정확히 하나라 **우연히
    #    안전**하다 — 그 우연에 기대지 않는다. `test_the_guard_would_catch_a_violation`이
    #    공백 둘 픽스처로 red를 남긴다(#04 — 한 줄만 고치면 다음 사람이 지워도 모른다).
    listed = match.group(1).split(_ERD_NOTE_SEPARATOR, 1)[0]
    return {value.strip() for value in listed.split("|")}


# ── 검사 ───────────────────────────────────────────────────────────


def test_the_scan_finds_documented_value_sets() -> None:
    """🔴 **검사 경로가 끊기면 통과가 아니라 실패다.**

    파싱이 0건을 돌려주면 *"위반이 없다"* 가 아니라 *"안 봤다"* 다 — 문서 형식이 바뀌거나
    절이 개명되면 정규식이 조용히 빈 집합을 낸다. 선례는
    `test_execution_id_identity.py::test_the_scan_finds_envelope_calls`.
    """
    for pair, start, end in _TABLE_PAIRS:
        assert _table_values(pair.doc, start, end), (
            f"{pair.doc.name}의 {pair.label} 표에서 값을 하나도 못 찾았다 — 위반이 없는 게 "
            "아니라 **검사가 끊긴 것**이다(절 제목·표 형식 변경 확인)"
        )
    for pair in _ERD_PAIRS:
        assert _erd_values(pair.doc, pair.label, pair.table), (
            f"{pair.doc.name}의 {pair.table} 블록에서 `varchar {pair.label}` 주석을 "
            "못 찾았다 — "
            "검사가 끊겼다"
        )
    for field_pair in _FIELD_PAIRS:
        assert _codeblock_fields(
            field_pair.doc, field_pair.heading, field_pair.class_name
        ), (
            f"{field_pair.doc.name}의 {field_pair.heading!r} 코드블록에서 "
            f"`{field_pair.class_name}`의 필드를 하나도 못 찾았다 — **검사가 끊긴 것**이다"
        )


@pytest.mark.parametrize("pair", _FIELD_PAIRS, ids=[p.label for p in _FIELD_PAIRS])
def test_documented_codeblock_fields_match_the_model(pair: _FieldPair) -> None:
    """🔴 문서 코드블록의 필드 집합 == 모델 필드. **문서를 읽어서** 비교한다 (99 #07).

    #150이 *"형식이 일정하지 않아 이 PR 범위를 넘는다"* 며 미룬 것이다. python 코드블록
    둘(§1.2·§2.2)은 형식이 일정해서 걸 수 있었다.

    ⚠ **이 검사가 대체하는 것:** `test_counsel_state.py`·`test_probe_state.py`가 각각
    **손으로 옮긴 필드 집합**과 `model_fields`를 비교하고 있었다 — 반대편이 코드 상수라
    `코드 == 코드`이고 **문서가 바뀌면 조용했다.** 이제 문서를 읽는다.
    """
    documented = _codeblock_fields(pair.doc, pair.heading, pair.class_name)
    coded = set(pair.model.model_fields)
    assert documented == coded, (
        f"{pair.doc.name} {pair.heading!r}의 `{pair.class_name}`과 코드가 갈렸다.\n"
        f"  문서에만: {sorted(documented - coded)}\n"
        f"  코드에만: {sorted(coded - documented)}\n"
        "🔴 필드를 더하거나 뺐으면 문서 코드블록도 같이 고친다."
    )


@pytest.mark.parametrize(
    ("pair", "start", "end"),
    _TABLE_PAIRS,
    ids=[p.label for p, _s, _e in _TABLE_PAIRS],
)
def test_documented_table_matches_the_enum(
    pair: _Pair, start: str, end: str
) -> None:
    """🔴 문서 표의 값 집합 == enum 멤버. **문서를 읽어서** 비교한다."""
    documented = _table_values(pair.doc, start, end)
    coded = pair.expected
    assert documented == coded, (
        f"{pair.doc.name}의 {pair.label} 표와 `{pair.origin}`이 갈렸다.\n"
        f"  문서에만: {sorted(documented - coded)}\n"
        f"  코드에만: {sorted(coded - documented)}\n"
        "🔴 값을 늘렸으면 문서 표도 같이 늘린다 — 한쪽만 고치면 문서가 거짓이 된다."
    )


@pytest.mark.parametrize(
    "pair",
    _ERD_PAIRS,
    ids=[f"{p.table}.{p.label}={p.origin}" for p in _ERD_PAIRS],
)
def test_erd_comment_matches_the_enum(pair: _Pair) -> None:
    """🔴 ERD 주석의 값 목록 == enum 멤버.

    ⚠ **`part_a/02_design.md`에 같은 블록을 두지 않는 것이 전제다**(99 #02 ⓑ) — 두 곳에
    있으면 이 검사가 한 곳만 지키고 다른 곳이 조용히 갈린다. 실제로 그렇게 갈렸었다
    (`capability`가 거기서 **3종**이었다).
    """
    documented = _erd_values(pair.doc, pair.label, pair.table)
    coded = pair.expected
    assert documented == coded, (
        f"{pair.doc.name} {pair.table}.{pair.label} 주석과 `{pair.origin}`이 갈렸다.\n"
        f"  문서에만: {sorted(documented - coded)}\n"
        f"  코드에만: {sorted(coded - documented)}"
    )


def test_the_guard_would_catch_a_violation(tmp_path: Path) -> None:
    """⚠ **가드가 실제로 잡는지**를 이 파일 안에서 확인한다 — 뒤집기의 상시화(로그 70).

    ⚠ `tempfile.NamedTemporaryFile`을 쓰지 마라 — win32에서 열려 있는 동안 그 경로를 다시
    열 수 없다(#134 실측).
    """
    erd = tmp_path / "fake_erd.md"
    erd.write_text(
        "  FIRST {\n"
        '    varchar status "a|b"\n'
        "  }\n"
        "  SECOND {\n"
        '    varchar status "c|d"\n'
        '    varchar capability "detection|composition"\n'
        "  }\n",
        encoding="utf-8",
    )
    assert _erd_values(erd, "capability", "SECOND") == {"detection", "composition"}
    assert _erd_values(erd, "capability", "SECOND") != {m.value for m in Capability}, (
        "두 값짜리 가짜 주석이 실제 enum과 같다고 나온다 — 파싱이 안 되고 있다"
    )
    # 🔴 같은 컬럼명이 두 테이블에 있을 때 **앵커가 실제로 고르는가**(B 확장 8/9).
    #    앵커가 무시되면 둘 다 첫 테이블 값을 돌려주고, 그 순간 이 검사는 조용히
    #    엉뚱한 표와 대조하게 된다 — 초록인 채로.
    assert _erd_values(erd, "status", "FIRST") == {"a", "b"}
    assert _erd_values(erd, "status", "SECOND") == {"c", "d"}
    assert _erd_values(erd, "status", "NOPE") == set(), (
        "없는 테이블인데 값을 돌려준다 — 앵커가 안 걸리고 파일 전체를 훑는다"
    )

    # 🔴 **꼬리 앞 공백이 둘일 때** — `split(sep, 1)[0]`만으로는 마지막 값에 공백이
    #    들러붙어 **문서가 맞는데 red**가 난다. `.strip()`을 빼면 이 단정이 죽는다.
    #    ⚠ 지금 `06_erd.md`의 ` — ` 주석은 공백이 정확히 하나라 **우연히 안전**하다 —
    #      그 우연에 기대지 않으려고 여기 red를 남긴다(#04).
    spaced = tmp_path / "spaced_erd.md"
    spaced.write_text(
        "  T {\n"
        '    varchar capability "detection|composition  — 설명이 두 칸 뒤에 온다"\n'
        "  }\n",
        encoding="utf-8",
    )
    assert _erd_values(spaced, "capability", "T") == {"detection", "composition"}, (
        "값 뒤 공백이 안 잘렸다 — `.strip()`이 빠지면 'composition '이 나온다"
    )

    table = tmp_path / "fake_table.md"
    table.write_text("시작\n> | `ok` | 뜻 | 대응 |\n> | `nope` | 뜻 | 대응 |\n끝\n", "utf-8")
    assert _table_values(table, "시작", "끝") == {"ok", "nope"}
    assert _table_values(table, "없는앵커", "끝") == set(), (
        "앵커를 못 찾았는데 값을 돌려준다 — 검사 경로 절단을 못 잡는다"
    )
