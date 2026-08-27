"""테스트 전역 플러그인 — 🔴 **`-m integration` 은 한 번에 하나만 돈다**(99 #251 · №108).

🔴 **왜 필요한가** — №107 이 `pre_pr_verify` 를 **겹쳐 두 번** 띄웠더니 두 실행이
**서로 다른 검사**에서 red 였고 직렬 재실행은 green 이었다(로그 218). 원인은
`test_migration_roundtrip` 의 `DROP SCHEMA public CASCADE` 가 **남의 상태를 지우는**
것이다 — #249 와 같은 뿌리지만 **겹이 다르다**: #249 는 한 프로세스 안 파일 사이라
`create_all` 되돌리기로 막혔고, 🔴 **프로세스 사이는 되돌릴 시점이 없다**(상대가
동시에 돌고 있다).

⚠ 🔴 **규율로는 못 막는다** — «게이트는 한 번에 하나만» 은 두 번 띄우는 순간 깨지고,
#224 가 «규율로 적는다» 를 **세 번 실패한 방법**으로 판정했다. ⇒ 🔴 `addopts` 의
`-p` 로 실어 **경로와 무관하게** 건다(`failure_name_pin` 과 같은 형식).

🔴 **왜 파일 잠금이 아니라 PG advisory lock 인가** — 🔴 **지키려는 상대가 DB 이기
때문이다.** 파일락은 «같은 경로」만 막는다 — 워크트리를 하나 더 파거나 다른 체크아웃
에서 돌리면 **같은 DB 를 쓰면서 락은 안 겹친다.** 🔴 그건 이 회차가 반복해서 밟은
«형태는 맞는데 **상대가 다르다**» 그 함정이다. 잠금은 **보호할 자원과 같은 자리**에
있어야 한다. 선례도 저장소 안이다 — `counsel_read_model.py` · `problem_store.py` ·
`inquiry_class_store.py` 가 `pg_advisory_xact_lock(hashtextextended(...))` 를 쓴다
(그쪽은 **트랜잭션** 범위, 이쪽은 **세션** 범위 — 세션 내내 잡아야 하므로).

🔴 **안 풀리는 잠금이 없다** — 세션 advisory lock 은 **연결이 끊기면 PG 가 자동 해제**
한다. 프로세스가 죽어도(kill · 패닉 · 정전) 남지 않는다. ⚠ 그래도 대기에는 상한을
둔다(불변식 6) — 상한을 넘으면 **명확한 사유로 죽는다**(«다른 실행이 돌고 있다»).

⚠ 🔴 **조용히 통과하지 않는다**(#202 · #226) — 잠금을 걸었으면 걸었다고, 못 걸었으면
왜 못 걸었는지 **화면에 적는다**. 안 그러면 «잠갔다」가 확인이 안 된다.
🔴 **오프라인 검사는 안 잠근다** — `-m integration` 일 때만이다(직렬화 비용).
"""

from __future__ import annotations

import os
import time
from typing import Any, Final

import pytest
from sqlalchemy.engine import make_url

#: 🔴 잠금 키 — 저장소 선례와 같은 `hashtextextended` 형식으로 PG 가 int8 로 만든다.
_LOCK_NAME: Final = "checkon-ai:pytest:-m integration"

#: 재시도 간격 — 🔴 **DB 밖에서** 쉰다(안에서 기다리면 보유자를 막는다).
_POLL_INTERVAL_S: Final = 1.0

#: 대기 상한. 🔴 값이 바뀌어도 코드 diff 가 안 생기게 env 로 연다(03 §1 하드코딩 금지).
#: 기본 300초 — integration 전량이 ~40초라 앞선 실행 하나를 넉넉히 기다린다.
_TIMEOUT_ENV: Final = "CHECKON_INTEGRATION_LOCK_TIMEOUT_S"
_DEFAULT_TIMEOUT_S: Final = 300.0

_held: Any | None = None


def wants_integration(markexpr: str) -> bool:
    """🔴 이 실행이 integration 을 **도는가** — 잠금은 그때만 건다.

    기본 `addopts` 는 `-m 'not integration and not external'` 이라 **안 잠근다**.
    `-m integration` · `-m 'integration and not external'` 이면 잠근다.
    """
    if not markexpr:
        return False
    normalized = " ".join(markexpr.split())
    if "not integration" in normalized:
        return False
    return "integration" in normalized


def _timeout_s() -> float:
    raw = os.environ.get(_TIMEOUT_ENV, "")
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_TIMEOUT_S
    #: 🔴 0·음수는 «상한 없음» 이 아니라 **잘못된 값**이다 — 기본으로 되돌린다.
    return value if value > 0 else _DEFAULT_TIMEOUT_S


def _sync_dsn() -> str:
    """async URL 을 psycopg(동기) DSN 으로 — 🔴 값은 **출력하지 않는다**(비밀번호)."""
    from ai.db.settings import get_db_settings

    return make_url(get_db_settings().database_url).set(
        drivername="postgresql"
    ).render_as_string(hide_password=False)


def pytest_configure(config: pytest.Config) -> None:
    """🔴 수집 전에 잡는다 — 첫 검사가 스키마를 만지기 전이어야 한다."""
    if not wants_integration(str(config.option.markexpr or "")):
        return
    _lock_integration_database()


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """-m '' 등으로 선택해도 실제 integration 실행이면 DB 방어를 빠뜨리지 않는다."""
    if _held is None and any(item.get_closest_marker("integration") for item in items):
        _lock_integration_database()


def _lock_integration_database() -> None:
    """검사 경로와 관계없이 같은 테스트 DB를 검증·선택하고 잠근다."""
    global _held
    # 2026-08-27: 직접 pytest도 배포 DB에 drop_all을 실행할 수 있었다. 잠금을 잡기
    # 전에 게이트와 같은 Settings 경로·가드를 적용하고 자식 프로세스에도 전달한다.
    from ai.db.settings import DbSettings, get_db_settings
    from ai.evaluation.pre_pr_verify import (
        PrePrVerificationError,
        verification_environment,
    )

    try:
        env = verification_environment(DbSettings(), env=os.environ.copy())
    except PrePrVerificationError as error:
        raise pytest.UsageError(str(error)) from error
    for key in ("DATABASE_URL", "TEST_DATABASE_URL", "AGENT_CHECKPOINT_DATABASE_URL"):
        os.environ[key] = env[key]
    get_db_settings.cache_clear()
    try:
        import psycopg

        connection = psycopg.connect(_sync_dsn(), autocommit=True)
    except Exception as error:  # noqa: BLE001 — 🔴 접속 불가는 skip 경로다(검사도 안 돈다)
        #: ⚠ 🔴 조용히 넘어가지 않는다 — 왜 안 잠갔는지 남긴다.
        print(f"\n  ⚠ integration 배타 잠금 없이 진행 — DB 접속 불가({type(error).__name__})")
        return
    started = time.monotonic()
    deadline = started + _timeout_s()
    announced = False
    while True:
        #: 🔴 **짧은 시도 + DB 밖 대기**다. 블로킹 `pg_advisory_lock` 으로 기다리면
        #: 그 세션이 **살아있는 트랜잭션**이 되고, 🔴 보유자 쪽의
        #: `CREATE INDEX CONCURRENTLY`(LangGraph 체크포인터 `setup`)가 **그걸 기다린다**
        #: — 대기자가 보유자를 막는다. 실측(№108 뒤집기 ①): 300초 대기 → 보유자
        #: 실행이 40초에서 **319초**로 늘었고 `pg_blocking_pids` 가 사슬을 보여줬다
        #: (`CREATE INDEX CONCURRENTLY` ← 대기자 ← 보유자). ⚠ 🔴 **잠금이 「막는 대상」이
        #: 의도와 달랐다** — 형태는 맞는데 상대가 하나 더 있었다.
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (_LOCK_NAME,)
            )
            row = cursor.fetchone()
        if row is not None and row[0]:
            break
        if not announced:
            announced = True
            print(
                f"\n  ⏳ 다른 `-m integration` 실행이 돌고 있다 — 최대 {_timeout_s():.0f}초"
                " 대기 (겹치면 서로의 스키마를 지운다 · 99 #251)"
            )
        #: 🔴 상한이 있는 루프다(불변식 6) — 마감을 넘으면 사유를 밝히고 죽는다.
        if time.monotonic() >= deadline:
            connection.close()
            pytest.exit(
                f"다른 `-m integration` 실행이 {_timeout_s():.0f}초 넘게 잡고 있다 —"
                " 게이트는 한 번에 하나만 돈다(99 #251 · 로그 218)",
                returncode=pytest.ExitCode.USAGE_ERROR,
            )
        time.sleep(_POLL_INTERVAL_S)
    _held = connection
    print(f"\n  🔒 integration 배타 잠금 획득(대기 {time.monotonic() - started:.1f}초)")


def pytest_unconfigure(config: pytest.Config) -> None:
    """🔴 연결을 닫으면 PG 가 잠금을 푼다 — 명시 해제에 기대지 않는다."""
    global _held
    if _held is None:
        return
    _held.close()
    _held = None


__all__ = ["wants_integration"]
