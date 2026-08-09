"""기록이 가리키는 날짜가 **실제로 지나갔는가** (99 #30).

*"<미래날짜> 해소"*·*"(<미래날짜> 실측)"* 처럼 **일어난 일의 시점**이 아직 오지 않은
날짜면 거짓이다.
🔴 세 축을 가른다 — 날짜만으로는 못 가르고 **문면**이 축이다:

    ⓐ 과거형   "<미래날짜> 해소" · "(<미래날짜> 실측)"  → 🔴 거짓. 이 검사가 잡는다
    ⓑ 계획형   "<미래날짜> BE 연결" · "데드라인"         → ✅ 정상
    ⓒ 기록 표식 "<지난날짜> 실측 당시"                   → ✅ 기록
    ⓓ 부정      "A의 <미래날짜> 런북 **오기**"           → ✅ 틀렸다고 말하는 것은 주장이 아니다

🔴 **분류를 뒤집었다 — 첫 판이 미탐을 냈다.** 처음엔 ⓐ의 말(`해소`·`실측`·`확정`…)을
**열거**했는데 `고쳤다`·`증명`·`조사`·`좁혔다`·`이주`를 빠뜨려 **자리 열둘을 놓쳤다.**
**「무엇이 주장인가」는 열거할 수 없다** — 한국어 서술어가 열려 있다. ⇒ **「주장이 아닌 것」을
이유와 함께 등재하고 모르는 자리는 주장으로 본다**(fail-closed). ⚠ 오탐은 red로 보이고
**미탐은 green으로 보인다**(99 로그 99) — 이 저장소가 같은 형태를 네 번 겪었다.

🔴 **기준이 `date`가 아니라 「최신 커밋 날짜」다.** `date`는 **같은 순간에 두 답을 낸다** —
실측: KST 8/9 00:37 = **UTC 8/8 15:37**이라 기기·CI마다 하루가 갈린다(99 #30 · 머리말 규율).
커밋 타임존은 `+0900`으로 고정돼 있고 **저장소가 이미 `git log --date=short`를 정본으로**
못 박았으므로, 그것을 쓰면 **어느 기기에서 돌려도 같은 답**이 나온다.

⚠ **이 검사는 한 방향으로만 안전하다 — 지우지 마라.**
시간이 지나면 미래 날짜는 과거가 되므로 **한 번 green이면 계속 green**이다. ⇒ 🔴 *"시계에
의존하는 테스트"* 로 보이지만 **flaky가 아니다**: 실패는 *"아직 안 온 날짜를 지나간 일로
적었다"* 는 **사실**일 때만 나고, 통과가 시간에 의해 뒤집히지 않는다.
⚠ **그래서 red가 났을 때 「기다리면 없어진다」로 넘기면 안 된다** — 그날이 오기 전에
누군가 그 문장을 근거로 쓴다.
"""

from __future__ import annotations

import datetime
import re
import subprocess
from pathlib import Path
from typing import Final

_ROOT: Final = Path(__file__).resolve().parents[3]
_SCAN_ROOTS: Final = ("docs", "src", "tests")
_SUFFIXES: Final = {".md", ".py", ".yaml"}

#: 짧은 표기(`8/NN`)와 긴 표기(`2026-08-NN`) 둘 다.
_SHORT: Final = re.compile(r"\b8/(\d{1,2})\b")
_LONG: Final = re.compile(r"\b2026-08-(\d{2})\b")

#: 문면 축 — 이 창 안에서 찾는다. ⚠ 좁히면 미탐이 늘고 넓히면 옆 문장을 본다.
_WINDOW: Final = 45

#: 🔴 **「주장이 아닌 것」만 등재한다 — 값은 사유다.** 모르는 자리는 **주장으로 보고 red**다.
#: ⚠ 새 표현이 나오면 red가 나고 **이유와 함께 여기 등재**하게 된다 — 미탐을 오탐 쪽으로
#: 옮긴 거래이고, `_OTHER_CORPUS_MARKERS`(`test_redaction_coverage.py`)와 같은 규약이다.
_NOT_A_CLAIM: Final = {
    "당시": "ⓒ 기록 표식 — 그때의 상태를 적은 것이다",
    "그때": "ⓒ 같은 것",
    "오기": "ⓓ 그 날짜가 **틀렸다고 말하는** 문장은 주장이 아니다",
    "오측": "ⓓ 같은 것",
    "틀렸": "ⓓ 같은 것",
    "거짓": "ⓓ 같은 것",
    "미래 날짜": "ⓓ 이 안건 자신을 설명하는 문면(99 #30·이 파일)",
    "예정": "ⓑ 계획형",
    "하기로": "ⓑ 계획형",
    "데드라인": "ⓑ 계획형",
    "대기": "ⓑ 계획형 — 아직 안 온 일을 기다린다",
    "리셋": "ⓑ 운영 규칙 서술(04 §쿼터의 「매일 자정 리셋」)",
}

#: 🔴 **일정표 — 계획임이 「표 머리」에 있고 행에는 없다.** 행 단위로는 못 가르므로
#: **파일 + 사유**로 등재한다. ⚠ `…_pg_status_briefing.md`는 **B 소유 문서**라
#: 문면을 고칠 수도 없다(A가 남의 핸드오프를 고치지 않는다).
_PLAN_TABLES: Final = {
    "docs/handoff/2026-08-08_pg_status_briefing.md": (
        "B의 일정표 — 행이 `| **<미래날짜>** | 영속화 왕복 테스트 …` 형태라 계획임이 표 머리에 "
        "있다. "
        "B 소유 문서이므로 A가 문면을 고치지 않는다"
    ),
}

#: 🔴 **통보 축이 붙은 자리 — 조용히 범위 밖으로 밀지 않는다.**
#: `test_the_pending_notification_still_violates`가 **만료 조건**이다 — 위반이 아니게 되면
#: 이 예외 자신이 red가 되어 목록에서 지우게 만든다
#: (선례: `test_redaction_coverage._PENDING_APPROVAL`).
#:
#: ✅ **비었다 — `docs/04_api_contract.md`가 2026-08-10에 이 가드의 축에서 벗어났다.**
#: 🔴 **만료 사유가 「고쳐졌다」가 아니다.** 그 문서의 `8/10` 표기는 **한 글자도 안 바뀌었고**,
#: 기준일(`_newest_commit_date()`)이 그 날짜를 지나면서 *"아직 안 온 날짜"* 라는 **이 가드의
#: 위반 조건**만 자연 소멸했다. 예외를 남겨 두면 만료 가드가 계속 red라 develop이 막힌다.
#: ⚠ **A가 99 #30에 등재한 문제는 그대로 열려 있다** — 정본은 `git log -S`의 `2026-08-07`이고
#: `04`는 `8/10`이라 **값이 다르다.** 그건 「미래 날짜」가 아니라 **「값 불일치」** 라 이 가드가
#: 보는 축이 아니었다(`test_the_cited_section_date_divergence_is_disclosed`가 그 축을 든다).
#: 통보 후 정정은 `part_b/09`의 공용 문서 변경 요청에 남아 있다.
_PENDING_NOTIFICATION: Final[dict[str, str]] = {}


def _parse_commit_stamp(raw: str) -> datetime.date:
    """`git log --date=short` 출력 → 날짜. 🔴 **이 검사 전체의 fail-closed다.**

    빈 문자열·쓰레기가 오면 **여기서 터진다.** 안 터지게 만들면 `stamp`가 빈 값이 되고
    비교가 전부 통과해 **검사가 아무것도 안 보는데 green으로 보인다.**
    ⚠ `test_the_baseline_parser_is_fail_closed`가 이 assert를 **실제로 문다** — 함수로
    빼 둔 이유가 그것이다(프로세스를 흉내내지 않고 로직을 직접 겨눈다).
    """
    stamp = raw.strip()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", stamp), (
        f"최신 커밋 날짜를 못 읽었다: {stamp!r} — 기준이 없으면 이 검사는 아무것도 안 본다"
    )
    return datetime.date.fromisoformat(stamp)


def _newest_commit_date() -> datetime.date:
    """저장소의 최신 커밋 날짜 — 기준이다(`git log --date=short`가 정본)."""
    result = subprocess.run(
        ["git", "log", "-1", "--format=%ad", "--date=short"],
        cwd=_ROOT, capture_output=True, text=True, check=False,
    )
    return _parse_commit_stamp(result.stdout)


def _future_past_tense_sites() -> list[tuple[str, int, str]]:
    baseline = _newest_commit_date()
    found: list[tuple[str, int, str]] = []
    for root in _SCAN_ROOTS:
        for path in (_ROOT / root).rglob("*"):
            if path.suffix not in _SUFFIXES or not path.is_file():
                continue
            relative = path.relative_to(_ROOT).as_posix()
            lines = path.read_text(encoding="utf-8").splitlines()
            for number, line in enumerate(lines, start=1):
                for pattern in (_SHORT, _LONG):
                    for match in pattern.finditer(line):
                        day = int(match.group(1))
                        if not 1 <= day <= 31:
                            continue
                        if datetime.date(2026, 8, day) <= baseline:
                            continue
                        window = line[
                            max(0, match.start() - _WINDOW) : match.end() + _WINDOW
                        ]
                        if any(word in window for word in _NOT_A_CLAIM):
                            continue
                        if any(table in relative for table in _PLAN_TABLES):
                            continue
                        # 🔴 여기까지 왔으면 **주장으로 본다**(fail-closed).
                        found.append((relative, number, match.group(0)))
                        break
    return found


def test_the_scan_reaches_every_root() -> None:
    """🔴 검사 경로가 끊기면 통과가 아니라 실패다."""
    for root in _SCAN_ROOTS:
        files = [p for p in (_ROOT / root).rglob("*") if p.suffix in _SUFFIXES]
        assert len(files) > 10, f"{root}를 못 읽었다 — {len(files)}개"


def test_every_exclusion_carries_a_reason() -> None:
    """⚠ 사유 없는 제외는 **조용한 화이트리스트**다 — 선례와 같은 규약이다."""
    assert all(reason.strip() for reason in _NOT_A_CLAIM.values())
    assert all(reason.strip() for reason in _PLAN_TABLES.values())
    assert all(reason.strip() for reason in _PENDING_NOTIFICATION.values())


def test_the_baseline_parser_is_fail_closed() -> None:
    """🔴 **기준을 못 읽으면 터진다 — 이 검사 전체의 fail-closed를 여기서 문다.**

    ⚠ **종전 이름이 `…comes_from_git_not_the_wall_clock`이었는데 단정은 `year == 2026`
    하나였다** — **벽시계로도 참**이라 *"기준이 git에서 온다"* 를 하나도 안 봤다.
    「검사의 이름이 보는 것보다 넓다」의 **여섯째**이고, 이 회차가 그 형태를 다섯 번 잡고
    여섯째를 **가드를 세우면서** 냈다(99 로그 103).

    🔴 **기준이 git에서 온다는 것은 `_newest_commit_date()`의 구현이 보장한다**(`git log`를
    부르는 자리가 거기 하나다). 이 검사가 보는 것은 **그 출력을 못 읽었을 때 조용히
    통과하지 않는가**다 — 그게 깨지면 `stamp`가 빈 값이 되고 **검사가 아무것도 안 보는데
    green으로 보인다.**
    """
    for broken in ("", "   ", "not-a-date", "2026-8-9", "2026-08-09 00:00:00 +0900"):
        try:
            _parse_commit_stamp(broken)
        except AssertionError:
            continue
        raise AssertionError(f"기준 파서가 {broken!r}를 통과시켰다 — fail-closed가 아니다")
    #: 정상 입력은 통과한다(파서가 전부 막으면 그것도 검사를 끊는 것이다).
    assert _parse_commit_stamp("2026-08-09\n") == datetime.date(2026, 8, 9)


def test_no_recorded_date_is_still_in_the_future() -> None:
    """🔴 **일어난 일의 시점이 아직 오지 않은 날짜면 거짓이다.**

    2026-08-09 실측: **자리 25**(출현 39). ⚠ **자리와 출현을 갈라 적는다** — 한 줄에
    날짜가 여섯 개 있는 자리가 있어(`#30` 등재문) 단위를 안 적으면 같은 데이터가 다른
    수로 읽힌다. 🔴 **그리고 수는 분류기에도 의존한다** — 문면 목록을 넓히면 늘어난다.
    ⇒ *"25건"* 이 참이려면 **기준일 + 세는 단위 + 분류기**가 함께 있어야 한다.
    """
    violations = [
        (path, number, token)
        for path, number, token in _future_past_tense_sites()
        if not any(pending in path for pending in _PENDING_NOTIFICATION)
    ]
    assert not violations, (
        f"아직 안 온 날짜를 지나간 일로 적었다({len(violations)}자리): {violations} — "
        f"정본은 `git log -S '<문장 일부>' -- <파일>`의 `%ad`다. 추측으로 고치지 마라"
    )


def test_the_pending_notification_still_violates() -> None:
    """🔴 **예외의 만료 조건** — 통보 후 고쳐지면 **이 검사가 red**가 된다.

    ⚠ 범위에서 빼면 통보가 끝나도 아무도 안 고친다.
    """
    violating = {path for path, _number, _token in _future_past_tense_sites()}
    stale = sorted(
        pending
        for pending in _PENDING_NOTIFICATION
        if not any(pending in path for path in violating)
    )
    assert not stale, (
        f"통보 대기 예외가 더는 위반이 아니다: {stale} — 고쳐졌으면 "
        "`_PENDING_NOTIFICATION`에서 지워라"
    )


# ── 인용과 피인용이 갈린 것을 고친 쪽이 말하는가 (99 #30·#02) ──────

#: src가 인용하는 04 §2.2 확정일 · 04 자신이 적는 확정일.
_CITED: Final = re.compile(r"04 §2\.2 8/(\d+) 확정")
_SECTION_OWN: Final = re.compile(r"두 축을 가른다 `\[확정 · 8/(\d+)\]`")
#: 고친 쪽이 갈림을 말하는 문면.
_DISCLOSURE: Final = "04 §2.2 자신의 문면은 아직"


def _citing_files() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in (_ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        match = _CITED.search(text)
        if match is not None:
            found[path.relative_to(_ROOT).as_posix()] = match.group(1)
    return found


def test_the_citation_scan_finds_both_sides() -> None:
    """🔴 검사 경로가 끊기면 통과가 아니라 실패다 — 한쪽을 못 읽으면 갈림을 못 본다."""
    citing = _citing_files()
    assert citing, "04 §2.2 확정일을 인용하는 src 자리를 못 찾았다"
    own = _SECTION_OWN.search(
        (_ROOT / "docs" / "04_api_contract.md").read_text(encoding="utf-8")
    )
    assert own is not None, "04 §2.2 자신의 확정일 표기를 못 찾았다 — 제목 문면이 바뀌었나"


def test_the_cited_section_date_divergence_is_disclosed() -> None:
    """🔴 **고칠 수 없는 쪽이 있으면 고친 쪽이 그 사실을 말한다.**

    `04_api_contract.md`는 **BE에 나간 계약 문서**라 날짜 정정에 통보 축이 붙는다(99 #30).
    그래서 src는 정본으로 고쳤고 04는 아직 **이전 날짜**다 — **지금 실제로 갈려 있다.**
    ⚠ 여기에 리터럴 날짜를 안 쓴다 — 쓰면 **위 날짜 검사가 이 docstring을 잡는다.**
    ⚠ 적어 두지 않으면 **다음 사람이 대조하고 참인 쪽(src)을 거짓으로 고친다.**

    🔴 **이 검사가 자동 만료다** — 04가 고쳐져 둘이 같아지면 **공개 문면이 남아 있는 것이
    red**가 되어 지우게 만든다(`test_the_pending_exception_still_violates`와 같은 형태).
    ⚠ *"주석이 있다"* 만 세지 않는다 — **갈림 자체**를 보고 그에 맞는 상태를 요구한다.
    """
    citing = _citing_files()
    own_match = _SECTION_OWN.search(
        (_ROOT / "docs" / "04_api_contract.md").read_text(encoding="utf-8")
    )
    assert own_match is not None
    own = own_match.group(1)

    for path, cited in citing.items():
        text = (_ROOT / path).read_text(encoding="utf-8")
        if cited != own:
            assert _DISCLOSURE in text, (
                f"{path}가 04 §2.2를 `8/{cited}`로 인용하는데 04 자신은 `8/{own}`이다 — "
                "갈렸는데 그 사실이 어디에도 없다. 고친 쪽이 말해야 다음 사람이 참인 쪽을 "
                "거짓으로 고치지 않는다(99 #30)"
            )
        else:
            assert _DISCLOSURE not in text, (
                f"{path}의 갈림 공개 문면이 낡았다 — 04가 `8/{own}`으로 맞춰졌으니 "
                "그 줄을 지워라(자동 만료)"
            )
