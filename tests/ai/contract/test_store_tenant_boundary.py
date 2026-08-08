"""counsel 저장소의 `get`이 **전부** 테넌트를 받는가 — 전수(99 #23).

🔴 **화이트리스트를 쓰지 않는다.** *"이 셋만 본다"* 로 적으면 넷째 저장소가 생겼을 때
검사가 **조용히 그 밖에 선다.** `stores.py`의 `*Store` Protocol을 **훑어서** 센다.

⚠ **이 검사는 시그니처를 본다 — 그것만으로는 부족하다.** 인자를 받고도 안 쓰면 통과하므로
구현 층(`test_pack_store_does_not_return_another_tenants_row`)과 경로
(`test_the_router_passes_the_tenant_into_the_pack_lookup`)를 함께 세웠다.
**셋이 각각 다른 이유로 red였다**(99 로그 91 — 뒤집기는 「값이 맞나」가 아니라
「경로가 이어졌나」를 본다).
"""

from __future__ import annotations

import inspect
from typing import Final, get_type_hints

from ai.composition.counsel import stores

#: `get`이 **참조 하나로 조회**하는 저장소만 대상이다 — `IdempotencyStore`처럼 키 구조가
#: 다른 것은 여기 축이 아니다. 판정 기준은 이름이 아니라 **첫 위치 인자가 `ref`인가**다.
_REF_PARAM: Final = "ref"
_TENANT_PARAM: Final = "tenant_id"


def _ref_lookup_protocols() -> list[type]:
    """`stores.py`의 Protocol 중 `get(self, ref, …)` 형태인 것 전수."""
    found = []
    for name in dir(stores):
        obj = getattr(stores, name, None)
        # `_is_protocol`은 typing이 Protocol 클래스에만 붙이는 표식이다 — 구현 클래스
        # (`InMemory*`)를 함께 세면 「Protocol이 계약을 말하는가」가 아니라
        # 「어느 구현이 지키는가」를 묻게 되어 검사 대상이 흐려진다.
        if not inspect.isclass(obj) or not getattr(obj, "_is_protocol", False):
            continue
        get = getattr(obj, "get", None)
        if get is None:
            continue
        params = list(inspect.signature(get).parameters)
        if len(params) >= 2 and params[1] == _REF_PARAM:
            found.append(obj)
    return found


def test_the_scan_finds_ref_lookup_stores() -> None:
    """🔴 **검사 경로가 끊기면 통과가 아니라 실패다.**

    0개로 세어지면 *"위반이 없다"* 가 아니라 **안 봤다**이다 — Protocol 정의 방식이
    바뀌거나 파일이 옮겨 가면 위 훑기가 조용히 빈 목록을 낸다
    (선례: `test_reset_pairing_guard.py::test_the_scan_finds_something`).
    """
    found = _ref_lookup_protocols()
    assert len(found) >= 3, (
        "`get(self, ref, …)` 형태의 저장소 Protocol을 못 찾았다 — 검사가 끊겼다. "
        f"찾은 것: {[cls.__name__ for cls in found]}"
    )


def test_every_counsel_store_get_takes_a_tenant() -> None:
    """🔴 **참조로 조회하는 저장소는 전부 테넌트를 받는다.**

    참조(`{scheme}://{uuid}`)는 **테넌트를 담지 않는다.** 그래서 저장소가 인자로 받지
    않으면 **행이 어느 테넌트 것인지 물을 방법이 없다.** 상위(라우터·워커)의 귀속 검증은
    이중 방어이고 **여기가 마지막 층**이다(`stores.py`가 자기 파일에 적어 둔 규율).
    """
    missing = []
    for cls in _ref_lookup_protocols():
        params = inspect.signature(cls.get).parameters  # type: ignore[attr-defined]
        param = params.get(_TENANT_PARAM)
        if param is None or param.kind is not inspect.Parameter.KEYWORD_ONLY:
            missing.append(cls.__name__)
            continue
        hints = get_type_hints(cls.get)  # type: ignore[attr-defined]
        if hints.get(_TENANT_PARAM) is not str:
            missing.append(f"{cls.__name__}(타입 불일치)")
    assert not missing, (
        f"참조 조회 저장소인데 `*, {_TENANT_PARAM}: str`이 없다: {missing}. "
        "참조는 테넌트를 담지 않으므로 저장소가 안 받으면 격리를 물을 자리가 사라진다"
    )
