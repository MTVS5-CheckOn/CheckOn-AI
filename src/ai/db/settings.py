"""DB 접속 설정 — pydantic-settings (하드코딩 금지, 03_coding_rules §1).

소유: 박진희 (db). DATABASE_URL을 환경/.env에서 주입한다 — os.environ 직접 접근 금지.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

#: 지원하는 저장소 백엔드 — 🔴 **허용 값의 정본**이다(99 #38).
type StoreBackend = Literal["memory", "pg"]


class DbSettings(BaseSettings):
    """DB 설정. `.env` 또는 환경 변수로 접속정보와 저장소 종류를 주입한다."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://localhost/checkon_ai"
    """async 드라이버(asyncpg) URL. 실배포는 .env로 주입."""

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

    store_backend: StoreBackend = "memory"
    """저장소 선택 — **정확히 두 값만** 받는다(99 #38).

    `memory`는 **명시적으로 고른 테스트·데모 백엔드**이고, `pg`는 실 DB 적재다.
    `database_url`에 기본값이 있어 URL 유무로는 백엔드를 못 고른다 — 명시 플래그로 고른다.
    기본 회귀는 memory로 돌고, PR 전 로컬 검증의 integration 단계만 실 PG를 주입한다.

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
