"""LangGraph 공식 PostgresSaver의 연결·스키마 초기화 경계.

🔴 **스키마 초기화는 배포 단계다** (99 #39). `STORE_BACKEND` 기본값이 `pg`가 된 뒤로는
선택 사항이 아니다 — 체크포인트 테이블이 없으면 counsel·probe·problem_generation 잡이
`relation "checkpoints" does not exist` → `worker_internal_error`로 **전부 실패**하는데
**기동은 정상이고 응답도 500이 아니라 「실패한 잡」**이라 조용하다.

```
uv run --frozen python -m ai.agents.checkpointer
```

⚠ **Alembic과 별도 단계다** — LangGraph가 자기 테이블을 소유한다(`db/models.py`의
`Base.metadata`에 없다). ⚠ **앱 기동에 붙이지 않았다** — 기동 중 DDL은 배포 판정이고
`api/app.py`는 양자 승인 파일이다. ⚠ **재실행해도 안전하다**(`setup()`이 멱등).
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.engine import make_url

from ai.db.settings import DbSettings, get_db_settings


def checkpoint_connection_string(settings: DbSettings) -> str:
    """SQLAlchemy async URL을 PostgresSaver가 받는 psycopg URL로 변환한다."""

    raw_url = settings.agent_checkpoint_database_url or settings.database_url
    url = make_url(raw_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("LangGraph 체크포인터는 PostgreSQL URL만 지원한다")
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


@asynccontextmanager
async def open_checkpointer(
    settings: DbSettings | None = None,
) -> AsyncIterator[AsyncPostgresSaver]:
    """요청 수명보다 긴 애플리케이션 수명주기에서 PostgresSaver를 연다."""

    resolved = settings or get_db_settings()
    connection_string = checkpoint_connection_string(resolved)
    async with AsyncPostgresSaver.from_conn_string(connection_string) as saver:
        yield saver


async def setup_checkpointer_schema(settings: DbSettings | None = None) -> None:
    """배포·마이그레이션 단계에서 LangGraph 체크포인트 테이블을 초기화한다.

    🔴 **`store_backend`를 보지 않는다.** 이 함수는 *"지금 pg로 도는가"* 가 아니라
    *"이 DB를 준비하라"* 는 명령이다 — `STORE_BACKEND=memory`로 도는 배포에서도
    **다음 플립을 위해 미리** 준비할 수 있어야 하고, 조건을 걸면 그 실행이 조용히
    아무것도 안 하고 **성공으로 끝난다.**
    """

    async with open_checkpointer(settings) as saver:
        await saver.setup()


def main() -> int:
    """CLI 진입 — `python -m ai.agents.checkpointer`.

    🔴 **책임을 나눈다** — 실제 비동기 작업은 `setup_checkpointer_schema()`가 하고
    여기서는 **진입과 종료 코드 전파**만 한다. 그래야 검사가 *"배포 명령이 정해진
    초기화 함수를 실행하는가"* 를 물을 수 있다(호출처를 문자열로 세면 **자기 docstring도
    호출처가 된다** — 실측 · 99 #39).

    🔴 **실패를 0으로 삼키지 않는다.** 배포 스크립트가 이 종료 코드로 중단을 판단한다.
    ⚠ **접속정보를 찍지 않는다** — 예외 문면에 DSN·비밀번호가 실릴 수 있으므로
    **예외 타입만** 내보낸다(`--verbose` 같은 우회로도 두지 않는다).
    """
    try:
        asyncio.run(setup_checkpointer_schema())
    except Exception as exc:  # noqa: BLE001 — 배포 명령이라 종류를 안 가리고 비정상 종료한다
        print(
            f"체크포인터 스키마 준비 실패: {type(exc).__name__} — 배포를 중단한다",
            file=sys.stderr,
        )
        return 1
    print("체크포인터 스키마 준비 완료(재실행 가능)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
