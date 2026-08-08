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

#: 🔴 **기록 축은 뺀다.** `99_open_items.md`(등재문·결정 로그)와 `docs/handoff/`(파일명에
#: 날짜가 박힌 리포트)는 **그때의 크기**를 적는 자리다 — 걸면 *"기록을 고쳐라"* 는 red가
#: 계속 나고 그건 기록을 훼손하라는 요구다. **가드의 축은 「현재형 진술」이다.**
_RECORD_AXIS: Final = ("99_open_items.md", "handoff")

#: 🔴 **앵커는 「크기 진술」이다** — 수 뒤에 `건`이 와야 한다. `코퍼스 50~54와 같은 형태`는
#: **케이스 id 범위**이지 크기가 아니라 여기 걸리면 안 된다(실측: `test_redaction_idempotence`).
_CORPUS_SIZE_CLAIM: Final = re.compile(r"코퍼스\s*(\d+)\s*건")

#: 🔴 **다른 코퍼스임을 말하는 표식 — fail-closed의 축이다.**
#: 이 코퍼스의 **이름 변형을 열거하지 않는다**(`failure 코퍼스`·`골든 코퍼스`·
#: `golden/redaction 코퍼스`·`redaction/ (코퍼스` — 실측 넷). 열거하면 **목록이 화이트리스트가
#: 되어 다섯째 변형에서 또 샌다**(PR-κ의 앵커가 정확히 그래서 미탐 다섯을 냈다).
#: ⇒ **반대로 「남의 코퍼스」를 등재한다** — 모르는 자리는 **이 코퍼스로 보고 red**를 낸다.
#: ⚠ 그래서 **다른 코퍼스가 새로 생기면 red가 나고 이유와 함께 여기 등재**하게 된다.
#: **미탐을 오탐 쪽으로 옮긴 거래**이고, 이 저장소가 ⓛ에 적어 둔 *"미탐은 오탐보다 나쁘다"*
#: 에 맞는 방향이다(선례: `NON_PROJECTED_COLUMNS`도 *"이유와 함께 등재"* 규약이다).
_OTHER_CORPUS_MARKERS: Final = {
    "실서버": "A군 활용형 오탐 대조군 26건 — `buffer_lexicon`·`05_tone_mapping`",
    "대조군": "같은 것. 표식이 **직전 줄**에 사는 자리가 있다(`test_buffer_lexicon`)",
    "문의": "분류(classify) 문의 코퍼스 80건 — `08_evaluation_plan`",
    "합성": "같은 것(전량 합성)",
    "당시": "기록 표식 — *「당시 코퍼스 N건」*은 그때의 크기다(`test_redaction_idempotence`)",
}

#: 🔴 **승인 대기 예외 — 조용히 범위 밖으로 밀지 않는다.**
#: 값은 **사유**이고 `test_the_pending_exception_still_violates`가 **만료 조건**이다:
#: 그 자리가 고쳐지면 **예외 자신이 red**가 되어 목록에서 지우게 만든다.
#: ⚠ 범위에서 빼면 승인이 와도 아무도 안 고친다.
_PENDING_APPROVAL: Final = {
    "src/ai/contracts/evaluation.py": (
        "양자 승인 파일(13곳) — 8/8 승인 대기. `db/models.py:3`(양자 12곳)·"
        "`CounselPackResult` docstring과 함께 세 줄 승인 요청 중(99 #02)"
    ),
}


def _scan_roots() -> list[Path]:
    root = Path(__file__).resolve().parents[3]
    return [root / "src", root / "docs", root / "tests"]


def _size_claiming_lines() -> list[tuple[str, int, str]]:
    """이 코퍼스의 크기를 수로 말하는 자리 전수 — (경로, 줄번호, 수).

    표식은 **그 줄과 직전 줄**에서 찾는다 — 실측에 표식이 앞 줄에 사는 자리가 있다.
    """
    root = Path(__file__).resolve().parents[3]
    hits: list[tuple[str, int, str]] = []
    for scan_root in _scan_roots():
        for path in scan_root.rglob("*"):
            if path.suffix not in {".py", ".md", ".yaml"} or not path.is_file():
                continue
            relative = str(path.relative_to(root))
            if any(part in relative for part in _RECORD_AXIS):
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
            for number, line in enumerate(lines, start=1):
                match = _CORPUS_SIZE_CLAIM.search(line)
                if match is None:
                    continue
                window = line + "\n" + (lines[number - 2] if number >= 2 else "")
                if any(marker in window for marker in _OTHER_CORPUS_MARKERS):
                    continue
                hits.append((relative, number, match.group(1)))
    return hits


def test_the_scan_reaches_every_root() -> None:
    """🔴 검사 경로가 끊기면 통과가 아니라 실패다 — 세 뿌리를 다 읽어야 한다."""
    for scan_root in _scan_roots():
        found = [
            path for path in scan_root.rglob("*") if path.suffix in {".py", ".md", ".yaml"}
        ]
        assert len(found) > 10, f"{scan_root}를 못 읽었다 — {len(found)}개"


def test_the_other_corpus_markers_carry_a_reason() -> None:
    """⚠ 표식은 **이유와 함께** 등재한다 — 사유 없는 등재는 조용한 화이트리스트다."""
    assert all(reason.strip() for reason in _OTHER_CORPUS_MARKERS.values())
    assert all(reason.strip() for reason in _PENDING_APPROVAL.values())


def test_no_current_statement_restates_the_corpus_size() -> None:
    """🔴 **정본을 수로 재진술하지 않는다 — 가드 없는 재진술은 또 갈린다**(99 #02).

    8/8 실측: `masking_redaction.md` §5 **제목**이 크기를 **30**으로 적고 있었고 실제 코퍼스는
    **63건**이다. PR-κ가 그 넷을 걷었는데 **앵커(`failure 코퍼스`)와 범위(`docs/` 셋)가 둘 다
    좁아 미탐이 다섯 남았다** — `contracts/evaluation.py`·`03_usecases.md`(같은 파일에서
    하나만 고쳤다)·`02_ownership.md`·`golden/redaction/__init__.py`·`masking_redaction.md`.

    🔴 **그래서 이름을 열거하지 않는다.** 이 코퍼스의 이름 변형을 나열하면 목록이
    화이트리스트가 되고 여섯째 변형에서 또 샌다 — **남의 코퍼스를 등재하고 모르는 자리는
    이 코퍼스로 보는** 방향이다(fail-closed).
    """
    violations = [
        (path, number, size)
        for path, number, size in _size_claiming_lines()
        if not any(pending in path for pending in _PENDING_APPROVAL)
    ]
    assert not violations, (
        f"이 코퍼스의 크기를 수로 재진술한다: {violations} (현재 {len(CORPUS)}건) — "
        "수를 빼고 「전건」으로 적어라. 다른 코퍼스라면 `_OTHER_CORPUS_MARKERS`에 "
        "**이유와 함께** 등재해라"
    )


def test_the_pending_exception_still_violates() -> None:
    """🔴 **예외의 만료 조건이다** — 승인 대기 자리가 고쳐지면 **이 검사가 red**가 된다.

    ⚠ 조용히 범위 밖으로 밀면 승인이 와도 아무도 안 고친다. 예외를 **살아 있는 채로**
    두고, 그 예외가 필요 없어지는 순간 **목록에서 지우라고 red**가 난다.
    """
    violating_paths = {path for path, _number, _size in _size_claiming_lines()}
    stale = sorted(
        pending
        for pending in _PENDING_APPROVAL
        if not any(pending in path for path in violating_paths)
    )
    assert not stale, (
        f"승인 대기 예외가 더는 위반이 아니다: {stale} — 고쳐졌으면 "
        "`_PENDING_APPROVAL`에서 지워라(예외가 남으면 다음 위반을 조용히 덮는다)"
    )
