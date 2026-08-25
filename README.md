# 체크온 (Check-On) — AI 서비스

**수능 대비 국어 학원의 개인 강사(고등)**를 위한 AI SaaS의 AI 서비스 저장소입니다.
(7/15 확정: 타겟을 수능 국어로 좁혀 시작 — 중등·내신 대비는 고도화 로드맵.)
감지(위험신호) → 우선순위 → 소통(상담 초안)과 약점 기반 문제 생성·검증 레이어를 담당합니다.

> 프로젝트는 3개 저장소로 구성됩니다: `ai`(이 저장소, Python) · `backend`(Java 21 / Spring Boot 4, PostgreSQL — 도메인 원본 소유) · `frontend`.
> AI 서비스는 백엔드로부터 **alias 처리된 스냅숏**을 REST로 받아 산출물(신호·초안·매핑·태그·문제 세트)만 돌려줍니다. 도메인 원본을 복제하지 않습니다.

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
# .env는 노션에서 받아 레포 루트에 둔다 (커밋 안 함 — .env.example은 두지 않는다)
uv run alembic upgrade head      # AI PG 스키마 (예정)
uv run uvicorn ai.api.app:app --reload   # ASGI 앱 = ai/api/app.py의 app
uv run pytest                    # 테스트 (LLM 스모크는 integration 마커라 기본 제외)
uv run ruff check . && uv run mypy .
```

**백엔드 시뮬레이션 리허설** (서버 기동 후, 다른 터미널에서 — day1 풀 스냅숏→증분 배치):

```bash
uv run python -m ai.evaluation.backend_sim   # 기본 3일치, 신호·lifecycle 표 출력
```

**PR 전 로컬 검증** (필수 — GitHub Actions 대신 아래 한 명령이 전체 게이트다):

> **고정 접속값:** `postgres:16` · `checkon`/`checkon`/`checkon_ai` · `5432`.
> `DATABASE_URL` 예: `postgresql+asyncpg://checkon:checkon@localhost:5432/checkon_ai`
> 🔴 integration에는 스키마 재생성·마이그레이션 왕복이 포함된다. 검증기는 원격 호스트나
> `checkon_ai`가 아닌 DB를 거부하지만, 이 로컬 DB도 반드시 **폐기 가능한 테스트 전용**으로 둔다.

🔴 **로컬 DB 정의는 이 저장소에 없다.** `docker-compose.yml`·`docker-compose.yaml`은
`.gitignore` 대상이라 **clone만 해서는 `docker compose up -d`가 안 된다.** 접속값 정본은
**위 문단**이고, 아래 둘 중 하나로 그 값을 갖는 DB를 띄우면 된다.

**ⓐ 파일 없이 한 줄로**(clone 직후 그대로 된다):

```bash
docker run -d --name checkon-ai-db \
  -e POSTGRES_USER=checkon -e POSTGRES_PASSWORD=checkon -e POSTGRES_DB=checkon_ai \
  -p 5432:5432 postgres:16
docker start checkon-ai-db                   # 두 번째부터는 이것만
```

**ⓑ compose를 쓰고 싶으면** 위 고정 접속값으로 `docker-compose.yml`을 **직접 만든다**
(`.gitignore`라 커밋되지 않는다) — 그 뒤 `docker compose up -d`.

```bash
uv run --frozen python -m ai.evaluation.pre_pr_verify
```

검증기는 ruff(`--no-cache`)·mypy(`--no-incremental`)·기본 pytest·실 PostgreSQL
integration을 순서대로 실행합니다. `CHECKON_ALLOW_REAL_LLM`이 켜져 있으면 즉시 실패하고,
실 LLM 보호 테스트 3건 외의 integration skip도 실패합니다. PR 본문에는 실행 OS와 두 pytest의
pass/skip 수를 적습니다. OS 민감 변경은 macOS와 Windows에서 각각 실행합니다.

**`.env` 키 (노션 공유 · 커밋 안 함):**

| 키 | 용도 | 기본/비고 |
| --- | --- | --- |
| `DATABASE_URL` | AI PG 접속(asyncpg) | `STORE_BACKEND=pg`일 때만 실접속 |
| `STORE_BACKEND` | 저장소 선택 | `memory`(기본·오프라인 테스트) \| `pg` |
| `OPENAI_BASE_URL` | **코드가 읽는다** — OpenAI API 엔드포인트 | `https://api.openai.com/v1` |
| `OPENAI_API_KEY` | **코드가 읽는다** — OpenAI API 키 | 제한 키면 completion·model read scope 필요 |
| `OPENAI_MODEL` | **코드가 읽는다** — OpenAI 모델 ID | 조직·프로젝트에서 접근 가능한 모델 |

> 🔴 **실서비스 LLM 백엔드는 OpenAI 단일입니다(8/6 재확정).** 폐기된 서버의 구 설정은
> 현재 설정 표면에 두지 않습니다. 결정 변경 이력은 `docs/99_open_items.md` ⓟ·B-5에만
> 보존합니다. `LLM_PROVIDER=openai_compat`는 벤더 선택지가 아니라 OpenAI API 어댑터의
> 기존 식별자이므로 유지합니다.

의존성: FastAPI · SQLAlchemy(+asyncpg) · Alembic · pandas/numpy/openpyxl · LangGraph(+postgres checkpointer) · **openai(OpenAI API용 — `llm/providers/`에서만 사용)**.
**LLM 백엔드 확정(8/6):** OpenAI 단일. `openai` SDK는 `llm/providers/` 안에서만 import하며, capability·contracts는 SDK에 직접 의존하지 않습니다(개발·PR 전 검증 기본은 Fake/Stub). ⚠ `LLM_PROVIDER=openai_compat`은 기존 어댑터 식별자라 유지하지만, 실서비스 `OPENAI_BASE_URL`은 OpenAI API를 가리켜야 합니다.

## 폴더 구조 (AI 아키텍처 지시서 기준 — 소유권은 `docs/02_ownership.md`)

패키지는 src 레이아웃: `src/ai/` 아래가 지시서 구조 그대로.

```
src/ai/
├── api/app.py          # FastAPI ASGI 엔트리포인트
├── contracts/          # capability 간 유일한 연결점 (양자 승인 정본은 docs/02 §4)
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

경계 규칙: 상대 capability 내부 파일 직접 수정 금지 — `contracts/`에 PR로. `docs/02_ownership.md` §4-1 의 **양자 승인 목록**은 두 명 승인 필수.

### counsel 배경 드레인 (99 #85 ① · №21)

`POST /v1/counsel/drafts` 는 잡을 큐에 넣고, **배경 드레인이 그 잡을 돌린다.**
로컬은 compose 를 안 쓰므로 **터미널 하나에서** 직접 띄운다:

```bash
uv run --frozen python -m ai.composition.counsel.drain    # STORE_BACKEND=pg 필요
```

⚠ **`STORE_BACKEND=memory` 에서는 기동을 거부한다** — 다른 프로세스의 잡을 볼 수 없어
배경 드레인이 뜻을 잃는다.
🔴 **배포(윈도우)는 compose 서비스로 띄운다** — 정의는 저장소 밖이고(`.gitignore`),
그 워커가 갖춰야 할 조건 넷은 `ai/composition/counsel/drain.py` docstring 에 있다(99 #128).
