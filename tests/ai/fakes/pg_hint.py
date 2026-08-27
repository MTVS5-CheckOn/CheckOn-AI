"""실 PG 미가용 skip 문면 — **한 곳에서만** 만든다.

🔴 **안내가 23곳에 복제돼 있었고, 그래서 낡은 채로 오래 남았다.** 종전 문면은
`docker compose -f compose.dev.yml up`이었는데 그 파일이 저장소에 없었고, #221이 9곳을
`docker compose up -d`로 고쳤다 — **나머지는 그때 안 고쳐졌고**(99 #02) 고친 쪽도 여전히
문제였다: `docker-compose.yml`도 **저장소에 없다**(`.gitignore` 대상).

⇒ clone한 새 개발자에게 `docker compose up -d`는 **여전히 막다른 길**이다.

🔴 **명령을 여기 적지 않는다** — 접속값·기동 절차의 정본은 README 한 곳이다. 명령을 문면에
넣으면 그날부터 **정본이 둘**이 되고, 지금 고치는 결함이 그대로 재발한다.
"""

from __future__ import annotations

from typing import Final

#: 🔴 **저장소에 로컬 DB 정의가 없다는 사실**까지 말한다 — 그걸 안 적으면 다음 사람이
#: *"compose 파일이 어디 있지"* 를 다시 찾는다(그게 이 결함의 실제 증상이었다).
PG_UNAVAILABLE: Final = (
    "실 PG 미가용 — README 「PR 전 로컬 검증」의 기존 컨테이너·테스트 DB 준비 절차를 따른다"
    "(로컬 DB 정의는 저장소에 없다 · checkon_ai_test는 CREATE DATABASE로 먼저 만든다 · "
    "배포 DB 사용 금지)"
)


def pg_unavailable(note: str = "") -> str:
    """skip 사유 — `note`에는 안건 번호처럼 **그 검사에만 있는 맥락**만 붙인다."""
    return f"{PG_UNAVAILABLE} {note}".rstrip() if note else PG_UNAVAILABLE


__all__ = ["PG_UNAVAILABLE", "pg_unavailable"]
