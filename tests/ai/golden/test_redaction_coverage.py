"""형태 커버리지 매트릭스 — **축의 곱집합을 생성해 빈 칸을 CI가 찾는다.**

🔴 **왜 이 파일이 생겼나 — 코퍼스 편중이 세 번 났다.**

| | 코퍼스가 빠뜨린 축 | 결과 |
| --- | --- | --- |
| ⓒ (8/5) | 별명형만 있고 **정식 성명형**이 없었다 | 미탐 7종 |
| ⓛ (8/6) | 조사 없는 형태만 있고 **조사 결합형**이 없었다 | 트립와이어 오차단 |
| 이번 (8/6) | 관계어 앞 성명·**학원 빈출어**가 없었다 | 미탐 14 · 오탐 9 |

세 번 다 **"미탐 0" 게이트가 통과하는 동안** 샜다. 코퍼스는 사람이 고른 표본이라
**안 넣은 형태는 안 재진다** — 게이트가 통과한 방식 자체가 구멍이 안 보인 이유였다.

⚠ **성질(멱등성)로는 미탐을 못 잡는다**(ⓛ에서 한 겹 올렸지만) — 안 잡힌 건 두 번 돌려도
안 잡힌다. 그래서 형태 축이 계속 샜다.

**이 파일이 하는 것:** 이름꼴 × 뒤따르는 말의 **곱집합을 코드로 생성**하고 각 칸에
`redact`를 돌린다. 코퍼스에 그 형태가 있든 없든 **엔진이 실제로 잡는지**를 잰다 —
코퍼스 커버리지가 아니라 **엔진 커버리지**다. 빈 칸은 `_KNOWN_GAPS`에 사유와 함께
적어야 하고, 목록에 없는 빈 칸이 생기면 red다. 반대로 **메워졌는데 목록에 남아 있으면**
그것도 red다(목록이 실제보다 크면 새 구멍을 숨긴다).
"""

from __future__ import annotations

import ast
import itertools
import re
from pathlib import Path
from typing import Final

import pytest

from ai.evaluation.golden.redaction import corpus as corpus_module
from ai.evaluation.golden.redaction.corpus import CORPUS
from ai.runtime.redaction import redact

#: 머리말(모듈 docstring)만 읽는다 — 케이스 본문은 이 검사의 축이 아니다.
_CORPUS_SRC: Final = Path(corpus_module.__file__).read_text(encoding="utf-8")

#: 이름꼴 축 — 실제로 들어오는 표기 형태.
_NAME_FORMS: dict[str, str] = {
    "정식성명3자": "박서연",
    "별명형(성생략+이)": "서연이",
    "영문명": "Sarah",
    "성명2자": "김철",  # ⚠ 알려진 한계(99) — 성+이름 1자
}

#: 뒤따르는 말 축 — 이름 뒤에 실제로 오는 것.
_FOLLOWERS: dict[str, str] = {
    "조사(가)": "가 결석했어요",
    "조사(은)": "은 성실합니다",
    "조사(의)": "의 성적표입니다",
    "호칭(어머니)": " 어머니입니다",
    "호칭(학생)": " 학생의 성적",
    "호칭(양)": " 양이 결석",
    "관계어(엄마)": " 엄마입니다",
    "관계어(아버지)": " 아버지입니다",
    "관계어(할머니)": " 할머니가 오세요",
    "관계어(보호자)": " 보호자입니다",
    "관계어(형)": " 형입니다",
}

#: 🔴 **메우지 못한 칸 — 사유와 등재처를 적는다.** 여기 없는 빈 칸이 생기면 red.
#: 목록이 줄어드는 방향으로만 움직여야 한다(늘리려면 99에 근거가 있어야 한다).
#: ⚠ 목록을 손으로 짐작하지 않았다 — 매트릭스를 먼저 돌려 **실제로 빈 칸만** 적었다.
#: (초안에서 "성명2자는 전부 구멍"으로 넓게 적었다가 `test_known_gaps_are_still_gaps`가
#:  잡았다 — `김철 어머니`는 긴 호칭 갈래가 2~3자를 허용해 **이미 잡힌다**.)
_KNOWN_GAPS: frozenset[tuple[str, str]] = frozenset(
    {
        # ── 성+이름 1자(`김철`) — 조사·짧은 관계어 갈래가 `[$surnames][가-힣]{2}`로
        #    3자를 요구한다. 긴 호칭(어머니·학생)은 2~3자를 허용해 잡힌다. 99 등재분.
        ("성명2자", "조사(가)"),
        ("성명2자", "조사(은)"),
        ("성명2자", "조사(의)"),
        ("성명2자", "호칭(양)"),
        ("성명2자", "관계어(엄마)"),
        ("성명2자", "관계어(아버지)"),
        ("성명2자", "관계어(할머니)"),
        ("성명2자", "관계어(보호자)"),
        ("성명2자", "관계어(형)"),
        # ── 영문명: **라틴 조사 목록을 넓히지 않는다** — `의`·`을`을 더하면
        #    `ProblemPack의` 같은 도메인 용어가 걸린다(8/5 실측). 호칭·관계어 결합은
        #    한국어 화자가 영문명에 붙이는 형태가 아니라 축을 열지 않았다.
        ("영문명", "조사(은)"),
        ("영문명", "조사(의)"),
        *(
            ("영문명", follower)
            for follower in _FOLLOWERS
            if follower.startswith(("호칭", "관계어"))
        ),
    }
)


def _is_masked(name: str, sentence: str) -> bool:
    """이름 조각이 산출물에서 사라졌는가 — 토큰 종류는 묻지 않는다.

    ⚠ `⟪이름N⟫`이든 `⟪확인필요⟫`든 **원문이 남지 않으면 통과**다. 확신 등급은 이
    매트릭스의 관심사가 아니고(그건 골든 `expected`가 본다), 여기서 재는 건 **유출 여부**다.
    """
    return name not in redact(sentence).masked_text


@pytest.mark.parametrize(
    ("form", "follower"),
    sorted(itertools.product(_NAME_FORMS, _FOLLOWERS)),
)
def test_every_cell_is_covered_or_a_known_gap(form: str, follower: str) -> None:
    """🔴 곱집합의 모든 칸 — 마스킹되거나, 사유가 적힌 알려진 구멍이거나."""
    name = _NAME_FORMS[form]
    masked = _is_masked(name, name + _FOLLOWERS[follower])
    if (form, follower) in _KNOWN_GAPS:
        pytest.skip(f"알려진 구멍: {form} × {follower}")
    got = redact(name + _FOLLOWERS[follower]).masked_text
    assert masked, (
        f"새 구멍: {form}({name}) × {follower} → {got!r} — "
        "메우든지 _KNOWN_GAPS에 사유와 99 등재처를 적어라"
    )


@pytest.mark.parametrize(("form", "follower"), sorted(_KNOWN_GAPS))
def test_known_gaps_are_still_gaps(form: str, follower: str) -> None:
    """🔴 반대 방향 — 메워졌는데 목록에 남아 있으면 red.

    목록이 실제보다 크면 **새 구멍을 그 그늘에 숨긴다.** 줄어드는 방향으로만 움직인다.
    """
    name = _NAME_FORMS[form]
    assert not _is_masked(name, name + _FOLLOWERS[follower]), (
        f"{form} × {follower}이 이제 마스킹된다 — _KNOWN_GAPS에서 빼라"
    )


def test_the_matrix_actually_spans_the_axes() -> None:
    """⚠ 검사 경로 절단 검출 — 축이 비면 위 대조가 공허하게 통과한다.

    `test_gate_feedback_coverage`의 `_MIN_CODES`와 같은 자리다.
    """
    assert len(_NAME_FORMS) >= 4
    assert len(_FOLLOWERS) >= 10
    covered = len(_NAME_FORMS) * len(_FOLLOWERS) - len(_KNOWN_GAPS)
    assert covered >= 24, f"실제로 재는 칸이 {covered}개뿐이다"


def test_relation_terms_are_the_axis_that_leaked() -> None:
    """이번 결함의 축이 매트릭스에 실제로 들어 있다 — 표만 늘리고 안 재면 소용없다."""
    relations = [f for f in _FOLLOWERS if f.startswith("관계어")]
    assert len(relations) >= 5
    for follower in relations:
        assert _is_masked("박서연", "박서연" + _FOLLOWERS[follower]), follower


# ── 머리말 표가 가리키는 케이스가 실존하는가 (8/8 · 99 #02) ────────


_CASE_REF: Final = re.compile(r"\*\*(\d+(?:[·~]\d+)*)\*\*")


def _referenced_case_ids() -> set[int]:
    """`corpus.py` 머리말 커버리지 표가 **볼드로 가리키는** 케이스 id 전부.

    `**44·45**`(열거)와 `**50~53**`(범위) 둘 다 쓴다.
    """
    header = ast.get_docstring(ast.parse(_CORPUS_SRC)) or ""
    table = [line for line in header.splitlines() if line.startswith("|")]
    ids: set[int] = set()
    for line in table:
        for token in _CASE_REF.findall(line):
            if "~" in token:
                lo, hi = (int(x) for x in token.split("~"))
                ids.update(range(lo, hi + 1))
            else:
                ids.update(int(x) for x in token.split("·"))
    return ids


def test_the_header_scan_finds_case_references() -> None:
    """🔴 검사 경로가 끊기면 통과가 아니라 실패다 — 0건이면 표 형식이 바뀐 것이다."""
    refs = _referenced_case_ids()
    assert len(refs) >= 10, f"머리말 표에서 케이스 참조를 못 찾았다: {sorted(refs)}"


def test_every_referenced_case_exists() -> None:
    """🔴 **표가 가리키는 케이스는 실존해야 한다.**

    8/8 실측: 표가 `| **성+이름 2자** | 김철이 | **43** |`로 43을 가리키는데 **id 43이
    없었다** — 그리고 **같은 표가 두 줄 아래에서 같은 형태를 「미커버(99 등재)」**라고
    적고 있었다. 실제로 그 형태는 99 ⓓ에서 **BE 명부 1차의 책임으로 층 배정**돼(8/6)
    AI 코퍼스에 케이스를 두지 않는 것이 맞다 ⇒ **매달린 쪽이 43 행**이었고 지웠다.

    ⚠ **표가 「덮는다」고 적고 케이스가 없으면 커버리지 진술이 거짓**이 된다 — 다음 사람은
    표를 읽고 그 형태가 검증된다고 믿는다. **가드 없는 재진술은 또 갈린다**(99 #02).
    """
    existing = {case.id for case in CORPUS}
    dangling = sorted(_referenced_case_ids() - existing)
    assert not dangling, (
        f"머리말 표가 없는 케이스를 가리킨다: {dangling} — 커버리지 진술이 거짓이다. "
        "케이스를 넣거나 표 행을 고쳐라(층 배정이면 「미커버」로 적는다)"
    )


# ── 코퍼스 크기를 문서가 수로 재진술하지 않는다 (8/8 · 99 #02) ──────

#: 현재형 진술이 사는 곳 — **기록 축(99)은 뺀다**(아래 docstring).
_CURRENT_STATEMENT_DIRS: Final = ("policies", "part_a", "handoff")

#: 🔴 **이 코퍼스를 이름으로 부르는 자리만 본다.** 첫 판에 `코퍼스\s*\d+`로 넓게 걸었더니
#: **다른 코퍼스 여덟을 함께 잡았다**(분류 문의 80건 · A군 활용형 26건 · 오탐 코퍼스 30건).
#: 셋 다 **다른 코퍼스의 참값**이라 red가 거짓이 된다 — **검사의 이름이 검사보다 넓었다**
#: (로그 85 · 내가 이 회차에 계속 잡던 형태를 가드를 세우다 스스로 냈다).
#: ⇒ `failure 코퍼스`라는 **고유 명칭**에 앵커한다(전수 확인: 이 이름은 redaction 코퍼스만 쓴다).
_CORPUS_SIZE_CLAIM: Final = re.compile(r"failure 코퍼스\s*(\d+)")


def _documents_restating_the_size() -> list[tuple[str, str]]:
    docs_root = Path(__file__).resolve().parents[3] / "docs"
    hits: list[tuple[str, str]] = []
    for folder in _CURRENT_STATEMENT_DIRS:
        for path in (docs_root / folder).rglob("*.md"):
            for claimed in _CORPUS_SIZE_CLAIM.findall(path.read_text(encoding="utf-8")):
                hits.append((str(path.relative_to(docs_root)), claimed))
    return hits


def test_the_document_scan_reaches_the_docs_tree() -> None:
    """🔴 검사 경로가 끊기면 통과가 아니라 실패다 — 문서를 못 읽으면 0건이 거짓이다."""
    docs_root = Path(__file__).resolve().parents[3] / "docs"
    found = [
        path
        for folder in _CURRENT_STATEMENT_DIRS
        for path in (docs_root / folder).rglob("*.md")
    ]
    assert len(found) > 10, f"문서 트리를 못 찾았다: {docs_root} · {len(found)}개"


def test_no_document_restates_the_corpus_size() -> None:
    """🔴 **정본을 수로 재진술하지 않는다 — 가드 없는 재진술은 또 갈린다**(99 #02).

    8/8 실측: `masking_redaction.md`가 *"코퍼스 54가 이 판정을 고정한다"* 였는데 코퍼스는
    **63건**이다(id 1~64 · **43 결번**). **정책 문서에 거짓 수가 현재형으로 서 있었다.**
    ⇒ *"골든 코퍼스 전건이"* 로 바꿨다 — 크기를 말할 이유가 애초에 없다.

    ⚠ **`99_open_items.md`는 이 검사의 축이 아니다.** 등재문·결정 로그는 **날짜가 붙은
    기록**이라 현재 크기와 다른 것이 정상이고, 걸면 *"기록을 고쳐라"* 는 red가 계속 난다.
    **가드의 축은 「현재형 진술」이지 「기록」이 아니다** — 이 구분을 안 적으면 다음 사람이
    범위를 넓혀 기록을 훼손한다(검사의 이름을 보는 것보다 넓히지 않는다 · 로그 85).
    """
    restated = _documents_restating_the_size()
    assert not restated, (
        f"문서가 코퍼스 크기를 수로 재진술한다: {restated} (현재 {len(CORPUS)}건) — "
        "수를 빼고 「전건」으로 적어라. 수를 적으면 코퍼스가 늘 때 문서만 낡는다"
    )
