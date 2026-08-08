"""counsel capability의 **버전 세트 — 생성 자리는 여기 하나다** (99 #20).

🔴 **「값의 정본을 어디 두느냐」가 아니라 「capability마다 한 자리에서 만들고 응답·원장이
그 하나를 참조한다」**가 규약이다(B 프레임 · pg 선례). pg는 `assembly.problem_versions()`를
`api/routers/problem.py`가 부른다 — **capability가 만들고 api가 참조한다.**

## 왜 이 모듈이 생겼나

종전에는 생성 자리가 **둘**이었다:

    api/routers/counsel.py   counsel_versions()   → 응답 meta.versions
                                                  → refine 턴의 AI_RUN
    composition/counsel/worker.py  VersionSet(…)  → 초안 잡의 AI_RUN

그래서 **같은 잡의 원장 두 행이 서로 다른 값**을 말했다(`pipeline` `"0.1"` vs `"0.1.0"`).
🔴 **값만 맞추면 자리가 둘로 남아 다음에 또 갈린다** — 그래서 자리를 없앴다.
`PROMPT_VERSION`이 이미 그 형태다(`prompt.py`가 소유하고 라우터·워커가 함께 import한다 —
워커가 *"prompt_version은 프롬프트 모듈이 소유한다"* 라고 적어 뒀다).

## 🔴 왜 `assembly.py`가 아닌가 (pg와 자리가 다른 이유)

pg는 `assembly.problem_versions()`인데 counsel의 `assembly.py`는 **`worker`를 import한다**
(`CounselPackRunner`). 워커가 거기서 버전을 가져오면 **순환 import**다. `prompt.py`에 두는
것도 안 골랐다 — **버전 세트는 프롬프트 소유가 아니다.** 의존이 `contracts`·`prompt`뿐인
**말단 모듈**을 새로 둔다.

## 🔴 공용 상수로 올리지 않는다 — **닫힌 판정**이다

`pipeline_version`이 여러 capability에서 우연히 `"0.1.0"`으로 같지만 **공용 상수로 올리지
않는다.** B 근거(8/8 · *"그 PR에서 저한테 물으실 것 없습니다"* — 동의 대기가 아니라 판정):

> ㊐는 **하나의 물건을 둘이 나눠 쓰는데 그 물건에 정책을 걸어야 하는** 자리다.
> `pipeline_version`은 **축이 둘인데 값이 우연히 같은** 자리다. 공용 상수로 올리면
> counsel을 `0.2`로 올릴 때 pg가 **같이 끌려가거나, 끌려가기 싫어서 못 올린다** —
> **값 일치를 구조 결합으로 바꾸는 거래**다.

⚠ **㊐ 형태가 아니다.** 「소유자가 둘이라 동의가 필요하다」는 **절차 논거**이고 위는
**구조 논거**다 — 절차로 막으면 동의가 오면 열리고, 구조로 막으면 안 열린다.
"""

from __future__ import annotations

from typing import Final

from ai.composition.counsel.prompt import PROMPT_VERSION
from ai.contracts.execution import VersionSet

#: 🔴 **이 네 상수의 자리도 여기 하나다.** 라우터가 자기 사본을 들면 그게 갈림의 씨앗이다.
_PIPELINE_VERSION: Final = "0.1.0"
_ENGINE_VERSION: Final = "counsel-pack-0.1"
_SCHEMA_VERSION: Final = "0.1"
_CONTRACT_VERSION: Final = "0.1"


def counsel_versions() -> VersionSet:
    """counsel 실행의 버전 세트 — **응답과 원장이 이 하나를 참조한다**.

    🔴 **선언 축이다**(04 §2.2) — *"이 응답을 낸 엔드포인트/capability가 어느 버전 위에서
    도는가"* 이지 *"이번 실행이 실제로 무엇을 썼는가"* 가 아니다. 실패 응답에도 실리므로
    **실행이 0인 시점에도 만들 수 있어야** 한다.

    ⚠ **`prompt_version`을 싣는다**(99 ㊔) — 종전에는 넷만 채워 응답의
    `meta.versions.prompt`가 `null`이었는데 **같은 실행의 `AI_RUN`에는 `"0.2"`가 있었다.**
    counsel은 프롬프트를 쓰는 capability다.

    ⚠ **counsel은 프롬프트가 둘인데 이 필드는 하나다** — `VersionSet.prompt_version`이
    단일 `str | None`이고 `contracts/execution.py`는 양자 승인 파일이라 늘릴 수 없다.
    초안 쪽(`prompt.PROMPT_VERSION` = `"0.2"`)을 싣는다 — `AI_RUN`이 이미 그 값을 쓰고,
    산출물이 초안이기 때문이다(plan은 그 입력을 고르는 보조 단계다).
    """
    return VersionSet(
        pipeline_version=_PIPELINE_VERSION,
        engine_version=_ENGINE_VERSION,
        schema_version=_SCHEMA_VERSION,
        contract_version=_CONTRACT_VERSION,
        prompt_version=PROMPT_VERSION,
    )


__all__ = ["counsel_versions"]
