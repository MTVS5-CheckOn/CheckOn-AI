"""DB 접속 설정 — pydantic-settings (하드코딩 금지, 03_coding_rules §1).

소유: 박진희 (db). DATABASE_URL을 환경/.env에서 주입한다 — os.environ 직접 접근 금지.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ai.runtime.env_files import ENV_FILES

#: 지원하는 저장소 백엔드 — 🔴 **허용 값의 정본**이다(99 #38).
type StoreBackend = Literal["memory", "pg"]


class DbSettings(BaseSettings):
    """DB 설정. `.env` 또는 환경 변수로 접속정보와 저장소 종류를 주입한다."""

    model_config = SettingsConfigDict(env_file=ENV_FILES, extra="ignore")

    database_url: str = "postgresql+asyncpg://localhost/checkon_ai"
    """async 드라이버(asyncpg) URL. 실배포는 .env로 주입."""

    db_pool_size: int = Field(default=5, ge=1)
    """SQLAlchemy 커넥션 풀 크기 — 🔴 종전에는 **명시가 없어** 기본값에 맡겨져 있었다.

    ⚠ 값 자체가 №20 ⑤ 의 범인은 **아니었다**(아래 참조). 다만 «몇 개를 쓰는지 아무도 안
    적어 뒀다»가 관계식을 못 세우게 하고 있었다."""

    db_max_overflow: int = Field(default=10, ge=0)
    """풀을 넘겼을 때 임시로 더 여는 수. `db_pool_size + db_max_overflow` 가 **프로세스 하나의
    SQLAlchemy 커넥션 상한**이다."""

    db_max_connections: int = Field(default=100, ge=1)
    """PG 의 `max_connections` — 🔴 **우리가 정하는 값이 아니라 서버에서 읽어 적는 값**이다.

    실측(8/20 · 로컬): **100**. 관계식이 이 값을 넘지 않는지 검사가 지킨다.
    ⚠ 배포 값이 다르면 여기서 덮어야 한다(`DB_MAX_CONNECTIONS`)."""

    app_workers: int = Field(default=1, ge=1)
    """앱 프로세스(uvicorn worker) 수 — 관계식의 곱이다.

    🔴 **№20 ⑤ 의 진짜 범인은 SQLAlchemy 풀이 아니었다.** `store_backend=pg` 는 잡마다
    `AsyncPostgresSaver` 커넥션을 **새로** 열고(`counsel/assembly.py` — 요청 스코프),
    그건 **풀 밖**이다. 동시 300건이 곧 커넥션 300 시도였고 `TooManyConnectionsError` 가 났다.
    ⇒ 관계식은 **그 항을 포함해야 한다**:

        앱 워커 × (pool_size + max_overflow)          ← SQLAlchemy
      + 앱 워커 × 동시 요청당 체크포인터 커넥션        ← 🔴 풀 밖
      + 배경 드레인 동시성                             ← `counsel_drain_concurrency`
      ≤ PG max_connections

    ⚠ **동시 요청당 체크포인터 커넥션에 상한이 없다** — 그게 남은 결함이고 99 #127 이다.
    이 회차는 **드레인 몫만** 상한을 갖는다(세마포어)."""

    agent_checkpoint_database_url: str | None = None
    """LangGraph PostgresSaver용 psycopg URL.

    미지정 시 ``database_url``에서 SQLAlchemy 드라이버 표기만 제거해 사용한다.
    체크포인트를 별도 DB로 격리할 때만 환경 변수로 명시한다.
    """

    llm_payload_max_chars: int = 32_768
    """`LLM_PAYLOAD` 본문 1건당 문자 상한 — 초과분은 **버린다**(절단 금지 · 99 ㉝).

    메모리·저장 비용 가드지 도메인 값이 아니다. 그래서 Settings에 두고 값이 바뀌면
    코드 diff가 아니라 설정 diff가 생긴다(03 §1).

    기본값 근거: 실측 프롬프트 최대치가 **1.1KB**(분류 · counsel 초안 954자 · plan 616자)이고
    입력 계약에 길이 상한이 없어 이론상 무한하다. 관측된 최악의 30배로 잡아 정상 트래픽은
    절대 닿지 않게 하되, 폭주 입력이 프로세스 메모리를 밀어내지 못하게 막는다.
    """

    store_backend: StoreBackend = "pg"
    """저장소 선택 — **정확히 두 값만** 받는다(99 #38).

    🔴 **기본값은 `pg`다**(8/12 플립 · 99 #39). 종전 기본값 `memory`는 **아무도 안 고른
    기본**이라, 배포에서 `STORE_BACKEND`를 안 넣으면 **잡 원장·실행 원장·멱등 저장·읽기
    모델·`AGENT_STEP`이 전부 프로세스 메모리**로 떨어졌다 — 워커를 둘 띄우면 갈리고
    (99 ㉬) 프로세스가 죽으면 사라지는데 **아무것도 안 터진다.** 영속이 기본이고
    휘발이 **명시적 선택**이어야 그 사고가 조용하지 않다.

    `memory`는 이제 **명시적으로 고르는 테스트·데모 백엔드**다 — `STORE_BACKEND=memory`.
    `database_url`에 기본값이 있어 URL 유무로는 백엔드를 못 고른다 — 명시 플래그로 고른다.
    ⚠ **오프라인 회귀는 `tests/conftest.py`가 `memory`를 명시로 건다** — 기존 테스트들이
    memory 전제로 쓰였기 때문이고, 플립 전용 검사는 그 환경 변수를 **지우고** 돈다.

    🔴 **미등록 값은 기동 설정 오류로 거부한다.** 종전에는 `str`이라 `pgg`·`PG`·`postgres`·
    `"memory "`·`""` 가 전부 **조용히 memory로 강등**됐다 — 설정 오타 하나로 **잡 원장·실행
    원장·멱등 저장·읽기 모델·`AGENT_STEP` 영속성이 한꺼번에** 사라지는데 **아무것도 안 터진다.**
    ⚠ **대소문자·공백을 보정하거나 오타를 추측하지 않는다** — 보정하면 **설정에 적힌 것과
    실제로 도는 것이 갈린다.**
    ⚠ **정본은 이 타입 하나다** — 팩토리마다 `if unknown: raise`를 복제하면 새 팩토리가
    생길 때 **한쪽만 낡는다**(99 #02).
    """


@lru_cache
def get_db_settings() -> DbSettings:
    """설정 싱글턴 — 매 호출 재파싱 방지."""
    return DbSettings()
