# 체크온 (Check-On) — AI 서비스

**수능 대비 국어 학원의 개인 강사(고등)**를 위한 AI SaaS의 AI 서비스 저장소입니다.
(7/15 확정: 타겟을 수능 국어로 좁혀 시작 — 중등·내신 대비는 고도화 로드맵.)
감지(위험신호) → 우선순위 → 소통(상담 초안) 분석 레이어를 담당합니다.

> 프로젝트는 3개 저장소로 구성됩니다: `ai`(이 저장소, Python) · `backend`(Java 21 / Spring Boot 4, MySQL — 도메인 원본 소유) · `frontend`.
> AI 서비스는 백엔드로부터 **alias 처리된 스냅숏**을 REST로 받아 산출물(신호·초안·매핑·태그)만 돌려줍니다. 도메인 원본을 복제하지 않습니다.

## 핵심 3축

1. **학생 위험신호 감지** — 규칙 R1~R6(결정론, LLM 0회), 핵심은 R4 '숨은 위기'(정답률 유지 + 어절 정규화 풀이시간 급증)
2. **학부모 라벨 기반 상담 초안** — 4축 라벨 → 톤 매핑(코드) → 블록 생성 → 게이트 체인 → 채팅형 다듬기(핑퐁)
3. **학생 개인 맞춤 문제** — 출제·진단 파이프라인 (member-B 파트)

## 절대 원칙 (전 코드 공통 — 자세한 건 `CLAUDE.md`)

- LLM은 어디서도 **수치·판정을 확정하지 않는다** — 확정은 결정론 코드
- **evidence 없는 산출물은 존재하지 않는다**
- 승인·발송·확정은 전부 이 서비스 밖(HITL) — 발송 코드 없음
- **실명·연락처는 AI 경계를 넘지 않는다** (alias + masking + redaction)
- 모든 루프에 상한 (블록 재생성 ≤3 · 조사 루프 ≤5)

## 시작하기

```bash
# Python 3.12 (uv)
uv sync                          # uv.lock 기준 전체 설치
# .env는 노션에서 받아 레포 루트에 둔다 (DATABASE_URL 등 — 커밋 안 함)
uv run alembic upgrade head      # AI PG 스키마 (예정)
uv run uvicorn ai.api.app:app --reload   # ASGI 앱 = ai/api/app.py의 app
uv run pytest                    # 테스트
uv run ruff check . && uv run mypy .
```

**백엔드 시뮬레이션 리허설** (서버 기동 후, 다른 터미널에서 — day1 풀 스냅숏→증분 배치):

```bash
uv run python -m ai.evaluation.backend_sim   # 기본 3일치, 신호·lifecycle 표 출력
```

**실 PostgreSQL 로컬 검증** (선택 — `.env`에 `POSTGRES_*`·`DATABASE_URL`·`STORE_BACKEND=pg`):

```bash
docker compose up -d                         # postgres 컨테이너 (docker-compose.yml)
uv run alembic upgrade head                  # 스키마 (DATABASE_URL이 이 DB를 가리킴)
uv run pytest -m integration                 # PG 왕복·재시작 생존 통합 테스트
```

의존성: FastAPI · SQLAlchemy(+asyncpg) · Alembic · pandas/numpy/openpyxl · LangGraph(+postgres checkpointer).
**LLM 벤더 SDK는 아직 설치하지 않습니다** — 벤더 선정은 미확정(B-5), 개발은 FakeProvider로.

## 폴더 구조 (AI 아키텍처 지시서 기준 — 소유권은 `docs/02_ownership.md`)

패키지는 src 레이아웃: `src/ai/` 아래가 지시서 구조 그대로.

```
src/ai/
├── main.py             # FastAPI 엔트리포인트
├── contracts/          # capability 간 유일한 연결점 (양자 승인 7파일 포함)
├── evidence/ gates/ agents/ llm/ registry/ runtime/   # 플랫폼
├── detection/          # 감지 (결정론 · LLM 금지)
├── composition/        # 상담 초안 + 핑퐁 + 리포트 + 보조 ⓐⓑⓓ + 에이전트①
├── diagnosis/ problem_generation/                     # member-B
├── import_mapping/     # 엑셀 이전 + 에이전트② + 태깅ⓒ
└── evaluation/golden/  # 골든셋 (프로덕션 격리)
```

## 문서

**`docs/00_INDEX.md`부터 읽으세요.** 문서별 확정/논의중 상태가 표시되어 있습니다.
미확정 안건(Open-1~12 · B팀/백엔드 합의)은 `docs/99_open_items.md`에서 추적하고, 확정되면 해당 문서와 함께 갱신합니다.

## 팀

| | 담당 | 소유 |
| --- | --- | --- |
| 박진희 (member-A) | 탐지·소통·Import | detection · composition · import_mapping + 플랫폼 대부분 |
| 염준영 (member-B) | 진단·출제 | diagnosis · problem_generation · llm/ |

경계 규칙: 상대 capability 내부 파일 직접 수정 금지 — `contracts/`에 PR로. 양자 승인 7파일은 두 명 승인 필수.
