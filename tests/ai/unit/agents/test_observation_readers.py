"""관측 카운터에 **읽는 자리**가 있는가 — 기준을 하나로 세운다 (로그 59 · 99 #23·㊐ ⓑ).

🔴 **기준을 먼저 정한다.** 선례가 셋인데 도달 수준이 서로 달라 *"리더가 있다"* 가 무엇을
뜻하는지 애매했다(8/8 실측):

| 값 | 리포트 등재 | 테스트가 세는 리더 | 판정·렌더 소비 |
| --- | --- | --- | --- |
| `collector_evicted_runs` | ✅ | 🔴 **없다** | ✅ 셋(마크다운·판정) |
| `emphasis_scanned` | ✅ | ✅(별 파일) | 없다 |
| `job_ledger_size`·`added` | ✅ | ✅ | 없다 |

⚠ **두 축이 서로 다른 방향으로 미달이었다** — `collector_evicted_runs`는 **소비는 있는데
가드가 없어** 다음 사람이 소비 자리를 지워도 red가 안 난다. 나머지는 가드는 있고 소비가
없다.

⇒ 🔴 **최소선 = 「리포트 등재 + 테스트가 세는 리더」.** 판정·렌더 소비는 그 위 단계이고
값의 성질에 따라 다르다(가용성 신호는 판정에 쓰고, 규모·경계 신호는 사람이 본다).
**소비가 있는 쪽도 가드는 있어야 한다** — 소비는 지워질 수 있고 가드는 그것을 잡는다.

⚠ 기준을 세우면서 자기 것만 예외로 두면 그 기준은 다음에 안 지켜진다(로그 92) ⇒
이 파일이 **저장소 카운터 전부**를 같은 기준으로 센다. ⚠ `emphasis_scanned`는 축이 달라
제외했고 그 이유를 목록 옆에 적었다(아래).
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

_SRC_ROOT: Final = Path(__file__).resolve().parents[3].parent / "src" / "ai"

#: (값 이름들, 그 값을 **만드는** 파일) — 리더는 그 파일 **밖**에 있어야 한다.
_OBSERVATIONS: Final = (
    (("job_ledger_size", "job_ledger_added"), "job_store.py"),
    (("pack_miss_absent", "pack_miss_foreign_tenant"), "pack_store.py"),
    (("collector_evicted_runs",), "run_store.py"),
)

#: ⚠ **`emphasis_scanned`는 이 검사의 축이 아니다** — 그건 **리포트 키 자신**(리더 쪽 끝)이고
#: 만드는 자리와 읽는 자리가 같은 파일이라 *"소유 파일 밖에 리더가 있나"* 가 성립하지 않는다.
#: 🔴 그 값의 가드는 `test_runner_observability.py::test_the_plan_masking_log_has_a_reader`가
#: 따로 든다(스캔 배선 + 리포트 등재를 소스에서 확인). **검사의 이름을 실제로 보는 것보다
#: 넓히지 않으려고 여기서 뺐다**(로그 85) — 표에 넣으면 이 파일이 그것까지 본다고 읽힌다.
_NOT_A_STORE_COUNTER: Final = ("emphasis_scanned",)


def test_the_scan_reaches_the_source_tree() -> None:
    """🔴 검사 경로가 끊기면 통과가 아니라 실패다."""
    assert len(list(_SRC_ROOT.rglob("*.py"))) > 50, f"소스 트리를 못 찾았다: {_SRC_ROOT}"


def test_the_observation_table_is_not_empty() -> None:
    """⚠ 목록이 비면 *"위반 없음"* 이 아니라 **아무것도 안 본 것**이다."""
    assert len(_OBSERVATIONS) >= 3, _OBSERVATIONS


def test_every_observation_counter_has_a_reader() -> None:
    """🔴 **세는 것과 보는 것은 다른 일이고, 세기만 하면 세지 않은 것과 결과가 같다.**

    `evicted_runs`는 카운터와 경고를 다 갖고도 **읽는 사람이 0명이라 두 달을 살았고**
    그게 99 ㉸(워커 원장 누락)가 오래 안 보인 실질 이유였다.
    """
    orphans: list[str] = []
    for names, owner in _OBSERVATIONS:
        readers = [
            path
            for path in _SRC_ROOT.rglob("*.py")
            if path.name != owner
            and any(name in path.read_text(encoding="utf-8") for name in names)
        ]
        if not readers:
            orphans.append(f"{'·'.join(names)}(소유 {owner})")
    assert not orphans, (
        f"관측 카운터를 소유 파일 밖에서 읽는 자리가 0곳이다: {orphans} — "
        "카운터만 있고 읽는 사람이 0명이면 관측이 아니라 죽은 코드다(로그 59)"
    )
