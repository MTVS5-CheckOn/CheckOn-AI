"""🔴 counsel 리셋과 **공용** 리셋의 병기를 잠근다 (99 ㊒ 후속).

#124가 `reset_counsel_stores()`에서 공용 잡 원장 리셋을 떼어 내면서 **호출부 14곳에 손으로
병기**했다. 지금 상태는 맞다 — 그런데 **다음 파일의 문제는 안 풀렸다.** 새 테스트가
`reset_counsel_stores()`만 부르면 조용히 샌다: 앞 테스트가 남긴 queued 잡을 이 파일의
`run_next`가 집어가고, 그 실패는 *"내 잡이 안 돌았다"* 로 보인다.

🔴 **그 PR의 로그 63이 *"등재는 옮겨 적는 일이 아니라 자리를 정하는 일"* 이라고 적었는데,
정작 같은 PR에서 자리를 안 정하고 복제했다.** 같은 형태가 이 프로젝트에서 일곱 번째다
(#108·#111·#114·#116·#117·#119·#120) — 선례는 `test_provider_access_guard`이고 결론도 같다:
**"한 곳에 규율을 적는다"로는 부족하고 경로 전수를 CI가 세야 한다.**

⚠ **판정 방식은 소스 텍스트다.** import든 호출이든 **이름이 파일에 있으면** 통과다. AST까지
갈 이유가 없다 — 우리가 막으려는 실수는 *"아예 안 쓴다"* 이지 *"잘못 쓴다"* 가 아니다.
**한계는 명확하다:** 주석에만 이름이 있어도 통과한다. 그 값싼 우회를 막자고 AST를 들이면
가드가 리팩터링마다 깨지고, 그러면 다음 사람이 가드를 지운다.

## 왜 `conftest.py` autouse 픽스처가 아닌가 (판정 · 8/8)

전역 autouse로 매 테스트 전 둘 다 리셋하는 안도 있었다. **채택하지 않았다:**

  ⓐ **의도적으로 상태를 유지하는 테스트를 깨뜨린다.** 멱등 재반환
     (`test_idempotent_replay_status`)과 재개 종단(#121 `test_counsel_runtime_lifetime`)은
     **요청 사이에 잡·체크포인트가 살아 있는 것**이 검증 대상이다. autouse가 그 사이에
     끼면 그 테스트들이 검증하려던 성질 자체가 사라진다.
  ⓑ 이 프로젝트는 *"조용히 해 주는 것"* 보다 **"빠뜨리면 알려주는 것"** 을 택해 왔다
     (`.get()` 침묵 폴백 대신 CI 검출 — 4-2 선례).

⚠ **이건 판정이지 정답이 아니다.** 반대 의견이 설 자리를 남긴다 — 병기 강제는 **호출부에
부담을 지운다**(두 줄을 늘 같이 써야 한다). 픽스처 하나로 끝나는 쪽이 낫다는 판단도
성립하며, 그 경우 위 ⓐ의 예외 테스트들은 **자기 픽스처로 옵트아웃**하면 된다. 다만 그
옵트아웃을 빠뜨리는 실수는 **조용하고**(테스트가 통과해 버린다) 지금 형태의 실수는
**시끄럽다**(가드가 red) — 그 비대칭이 지금 판정의 근거다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

#: 검사 대상 — 테스트 트리 전체.
_TESTS_ROOT: Final = Path("tests")

_COUNSEL_RESET: Final = "reset_counsel_stores"
_SHARED_RESET: Final = "reset_shared_agent_runtime"

#: ⚠ **이 파일 자신은 제외한다** — 가드는 두 이름을 **문자열로** 들고 있어야 검사할 수
#: 있으므로 자기 자신은 항상 "둘 다 참조"로 보인다. 무해하지만 목록에 남으면 다음 사람이
#: *"가드도 리셋을 부르나?"* 로 읽는다.
_SELF: Final = "test_reset_pairing_guard.py"


def _files_referencing_counsel_reset() -> list[Path]:
    return sorted(
        path
        for path in _TESTS_ROOT.rglob("test_*.py")
        if path.name != _SELF and _COUNSEL_RESET in path.read_text(encoding="utf-8")
    )


def test_the_scan_finds_something() -> None:
    """🔴 **검사 경로가 끊기면 통과가 아니라 실패다.**

    `reset_counsel_stores`를 참조하는 파일을 하나도 못 찾으면 그건 *"위반이 없다"* 가
    아니라 *"안 봤다"* 다 — 경로가 바뀌었거나 이름이 개명됐다는 뜻이다.
    조용한 통과가 7/30 트레이스 사고의 유형이고, `test_composition_redaction`이 같은
    이유로 같은 단정을 들고 있다.
    """
    found = _files_referencing_counsel_reset()
    assert found, (
        f"{_TESTS_ROOT}/ 에서 `{_COUNSEL_RESET}`를 참조하는 테스트 파일을 하나도 찾지 "
        "못했다 — 위반이 없는 게 아니라 검사가 끊긴 것이다(이름 개명·경로 변경 확인)"
    )


def test_counsel_reset_is_paired_with_shared_reset() -> None:
    """🔴 `reset_counsel_stores`를 쓰는 파일은 `reset_shared_agent_runtime`도 쓴다.

    ⚠ **화이트리스트를 두지 않는다.** 지금 13/13이 병기하므로 통과가 정상이고, 예외를
    허용하기 시작하면 **목록이 사고를 숨긴다**(#119 동명 가드와 같은 판단).
    """
    offenders = [
        path.as_posix()
        for path in _files_referencing_counsel_reset()
        if _SHARED_RESET not in path.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        f"{_COUNSEL_RESET}만 부르고 {_SHARED_RESET}는 안 부르는 파일이 있다:\n  "
        + "\n  ".join(offenders)
        + f"\n\n공용 잡 원장(A·B 공용 · 99 ㊒)을 안 비우면 **앞 테스트가 남긴 queued 잡을 "
        "이 파일의 run_next가 집어간다** — 그 실패는 '내 잡이 안 돌았다'로 보여 원인을 "
        f"엉뚱한 데서 찾게 된다.\n고치는 법: `from ai.db.store_factory import "
        f"{_SHARED_RESET}` 후 `{_COUNSEL_RESET}()` **옆에 나란히** 부른다.\n"
        "⚠ 반대로 잡을 적재하지 않는 파일이라 정말 필요 없다면, 그건 화이트리스트가 아니라 "
        f"`{_COUNSEL_RESET}` 자체가 필요 없다는 뜻일 수 있다 — 먼저 그쪽을 의심하라."
    )
