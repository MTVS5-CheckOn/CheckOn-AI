"""체크온 AI 서비스 — 패키지 루트 (src 레이아웃, CLAUDE.md §4).

🔴 **Windows에서만 asyncio 루프 정책을 갈아 끼운다** — 다른 곳에 둘 자리가 없다.

LangGraph 체크포인터(`AsyncPostgresSaver`)는 **psycopg**를 쓰고, psycopg의 async 구현은
Windows 기본 `ProactorEventLoop`를 **연결 시도 전에** `InterfaceError`로 거부한다. 그
경로를 상담·probe·문제생성 조립기가 전부 타므로, 정책을 안 걸면 Windows에서 그 세
capability가 **기동은 정상인데 요청마다 500**이다(실측 2026-08-12 · `/v1/counsel/drafts`).

⚠ **여기가 유일하게 가능한 자리다.** 운영 기동 명령은 `uvicorn ai.api.app:app`이고
uvicorn은 자기 루프를 직접 만든다 — 우리 코드가 끼어들 수 있는 지점은 **앱 모듈을
import하기 전에 반드시 실행되는 패키지 루트**뿐이다(`api/app.py`는 양자 승인 파일이라
손대지 않는다).

⚠ **macOS·Linux는 한 줄도 안 탄다** — 분기가 `sys.platform`이라 기존 기본 정책 그대로다.
⚠ **Proactor를 잃어도 잃는 기능이 없다** — `src/` 전체에 asyncio 서브프로세스 사용이
**0건**이다(Proactor가 Selector보다 나은 자리는 거기다). SQLAlchemy의 asyncpg 경로는
두 루프에서 다 돈다.
"""

from __future__ import annotations

import asyncio
import sys

if sys.platform == "win32":  # pragma: no cover - 분기 자체는 값으로 검사한다
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
