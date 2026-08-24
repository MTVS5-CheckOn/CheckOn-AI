"""`/v1/meta/versions` 가 **모든 라우터 축**을 덮나 — 「둘 다 갱신됐나」 가드.

🔴 **왜 파생이 아니라 가드인가**(실측 2026-08-24 · `ops.py` 주석과 한 쌍):
`ROUTER_VERSION_SCOPES` 는 «**실패 응답**의 `meta.versions` 를 무엇으로 채우나» 이고
(경로별 · 11개), `meta_versions()` 의 `capabilities` 는 «운영자가 조회하는 **capability**
목록» 이다(중복을 뺀 축 · 6개). **뜻이 다르므로 1:1 파생이 틀린다** —
`confirmations` 는 `classify` 와 같은 engine 이고 `ops` 는 3경로 1축이다.

⇒ 대신 **engine_version 수준의 포함 관계**만 문다. 새 라우터가 생기면 여기서 red 가
나고, 「capability 로 노출한다 / 아래 면제 목록에 왜·언제와 함께 적는다」 둘 중 하나를
**의식적으로** 고르게 된다. №65 가 `labels_versions()` 를 만들고 `capabilities` 에
안 붙인 채 지나간 것이 이 검사가 없어서 생긴 일이다(99 #196 계열).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from ai.api.app import ROUTER_VERSION_SCOPES, create_app

# 🔴 capability 로 노출하지 **않는** 축 — «왜» 와 «언제 없어지나» 를 반드시 적는다.
_NOT_A_CAPABILITY = {
    # 왜: 운영 점검 축(health·ready·meta)이지 강사에게 파는 기능이 아니다.
    # 언제: 없어지지 않는다 — ops 는 영구히 capability 밖이다.
    "ops-0.1": "운영 점검 축 — capability 아님(영구)",
    # 왜: `/v1/reports` 는 B(염준영) 소유 축이고, capability 표기를 이 회차에서
    #     정하는 것은 남의 판정을 대신하는 것이다.
    # 언제: `ops.py` 표기 판정(8/24 회신)이 오면 그때 노출 여부가 정해진다.
    "report-0.1": "🔴 표기 판정 대기(8/24 회신) — B 축",
}


def _capabilities() -> dict[str, dict[str, str]]:
    """🔴 함수 직접 호출이 아니라 **응답 본문**으로 잰다(99 #208).

    실측 8/24: `meta_versions()` 는 **async** 이고 키는 `engine_version` 이 아니라
    **`engine`** 이다(`envelope.versions_dict` 가 `_version` 접미사를 뗀다).
    함수를 직접 부르는 검사는 그 둘을 **틀리게 쓰고도 통과**했을 것이다.
    """
    with TestClient(create_app()) as client:
        response = client.get(
            "/v1/meta/versions",
            headers={"X-Tenant-Id": "t-meta", "X-Request-Id": "r-meta"},
        )
    assert response.status_code == 200, response.text
    capabilities: dict[str, dict[str, str]] = response.json()["data"]["capabilities"]
    return capabilities


def test_every_router_scope_is_either_a_capability_or_excused() -> None:
    """🔴 **대조 집합을 중간 변수로 두지 않는다**(앵커 폭 · 2026-08-24 실측).

    뒤집기 ③(«대조를 상수로 박기»)이 **green 이었다** — `exposed = {...}` 로 지금 맞는
    값을 박아도 통과했다. 🔴 그건 «올바르다» 가 아니라 «이 문장이 응답에서 왔다는 걸
    이 검사가 안 잰다» 다(99 #211). ⚠ 지금 맞는 상수는 **어떤 단언으로도** 파생과
    구분되지 않는다 — 구분되는 건 «나중에 어긋날 때» 뿐이고 그건 ①②가 문다.
    ⇒ 응답을 **단언 안에서 바로** 읽어 상수를 꽂을 자리를 없앤다(박으려면 이 문장을
    **지워야** 하고, 그건 뒤집기가 아니라 삭제다).
    """
    #: 🔴 **무엇을 쟀나** — 축이 통째로 사라지면 「빠진 게 없다」로 green 이 된다(№68).
    assert len(ROUTER_VERSION_SCOPES) >= 9, len(ROUTER_VERSION_SCOPES)
    missing = {
        scope.prefix: scope.versions().engine_version
        for scope in ROUTER_VERSION_SCOPES
        if scope.versions().engine_version
        not in {caps["engine"] for caps in _capabilities().values()}
        and scope.versions().engine_version not in _NOT_A_CAPABILITY
    }
    assert not missing, (
        f"라우터 축이 `/v1/meta/versions` 에 없다: {missing}. "
        "capability 로 노출하든지, `_NOT_A_CAPABILITY` 에 왜·언제와 함께 적어라."
    )


def test_labels_is_actually_served_over_http() -> None:
    """승우님께 «다음 회차에 넣습니다» 로 고지한 칸 — HTTP 로 확인한다."""
    capabilities = _capabilities()
    assert "labels" in capabilities, sorted(capabilities)
    assert capabilities["labels"]["engine"] == "labels-suggest-0.1"


def test_the_excuse_list_only_holds_engines_that_exist() -> None:
    """면제 목록이 **죽은 문자열**로 남지 않게 — 그 축이 사라지면 여기서 red."""
    live = {scope.versions().engine_version for scope in ROUTER_VERSION_SCOPES}
    stale = set(_NOT_A_CAPABILITY) - live
    assert not stale, f"없어진 축이 면제 목록에 남았다: {stale}"
