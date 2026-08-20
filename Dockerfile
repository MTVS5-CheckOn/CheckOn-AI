# 체크온 AI 서비스 런타임 이미지 — uv 기반 (pyproject.toml · uv.lock 이 정본).
#
# 의존성 레이어와 소스 레이어를 나눈다 — 소스만 고치면 uv sync 를 다시 안 돈다.
# ⚠ 파이썬은 3.12 고정이다(.python-version · requires-python >=3.12).

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# UV_COMPILE_BYTECODE: 기동 시 .pyc 생성 비용을 빌드로 옮긴다(컨테이너는 매번 새 프로세스다).
# UV_LINK_MODE=copy : 캐시 마운트와 /app 이 다른 파일시스템이라 하드링크가 안 된다(경고 제거).
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# ① 의존성만 먼저 — 프로젝트 자체는 빼고(--no-install-project) 락 그대로 설치한다.
#    dev 그룹(mypy·pytest·ruff)은 런타임에 필요 없다.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

# ② 소스 — alembic.ini 는 script_location=src/ai/db/migrations 라 루트에 있어야 한다.
COPY pyproject.toml uv.lock README.md alembic.ini main.py ./
COPY src ./src

# ③ 프로젝트 설치(src 레이아웃 · hatchling) — 이제 `ai.api.app` 을 import 할 수 있다.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# uv run 없이도 venv 의 uvicorn·alembic 이 잡히게 한다.
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# ⚠ 기본 명령은 API 서버다. migrate 서비스는 compose 에서 command 로 덮어쓴다.
CMD ["uvicorn", "ai.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
