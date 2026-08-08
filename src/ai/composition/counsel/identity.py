"""결정론 식별자 — 재시도가 같은 행을 가리킨다 (99 #24 · 불변식 8).

🔴 **`new_id` 생성기를 바꾸지 않는다.** counsel의 `new_id` 하나가 **여섯 자리**에서
값을 만든다(실측 8/8): 컨텍스트 번들 id · `job_id` · `execution_id` · draft id ·
agent_step id · 팩 레코드 id. 그중 **하나만** 결정론으로 바꾸면 생성기의 성격이 갈리고,
전부 바꾸면 주입 지점 열두 곳이 영향권이다(`new_id` 선언 전수).

⇒ **전용 유도 함수를 둔다** — 선례가 정확히 그 형태다
(`problem_generation/domain/identity.problem_item_id(set_id, slot_index)`).
"""

from __future__ import annotations

from uuid import UUID, uuid5

#: 네임스페이스 문자열 — 같은 `job_id`에서 다른 종류의 id를 유도할 때 섞이지 않게 한다.
#: ⚠ **바꾸면 기존 행의 키가 달라진다** — 이관 없이 바꾸지 마라.
_PACK_RESULT_NS = "counsel-pack-result"


def pack_result_id(job_id: UUID) -> UUID:
    """팩 결과 레코드의 PK — **잡 하나에 행 하나**다.

    🔴 **왜 결정론인가** — `_execute`의 ⑤(`_store_pack`)와 ⑥(`succeed`) 사이에서 죽으면
    lease 만료 → recovery → **⑤를 다시 돈다.** `job.result_ref`를 읽는 자리가 worker에
    0곳이라(전수) 재진입이 앞 저장을 모르고, `uuid4`면 **새 행**이 생겨 앞 행이 고아가 된다.
    같은 키면 저장소의 멱등 분기가 흡수한다.

    ⚠ **`AGENT_RUN.id`(=`job_id`)를 그대로 쓰지 않는다** — `06_erd.md`와 ORM docstring이
    *"PK는 `AGENT_RUN.id`가 아니다"* 를 명시하고, 같게 두면 `pack://` ref가 잡 id와
    구분되지 않아 **불투명 참조라는 성질이 흐려진다.**
    """
    return uuid5(job_id, _PACK_RESULT_NS)
