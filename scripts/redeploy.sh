#!/usr/bin/env bash
#
# CheckOn-AI 재배포 — develop 최신을 받아 **컨테이너에** 반영한다.
#
# 🔴 왜 이 스크립트가 있나 — `git pull` 만으로는 서버가 안 바뀐다.
#    Dockerfile 이 `COPY src ./src` 로 소스를 **이미지에 굽고**, uvicorn 은 `--reload` 없이
#    돈다(승우님이 붙는 서버라 「빌드된 것만 나간다」가 안전하다). 그래서 pull 은 호스트
#    파일만 바꾸고 컨테이너 안은 5일 전 코드 그대로였다 — 2026-08-19 실측, 25개 파일 차이.
#    ⚠ 그 중 `counsel_openapi.py`(BE 코드 생성용)가 빠져서 **공개 /openapi.json 이 낡아
#      있었다.** 증상이 조용하다 — 서버는 200 을 잘 준다. 그래서 검증까지 여기서 한다.
#
# 🔴 `.env` 도 같은 병을 앓는다(2026-08-20 실측). env 는 `src/` 밖이라 소스 해시가 못 잡고,
#    compose 는 env_file 을 **컨테이너를 만들 때** 읽는다 — 파일만 고치면 도는 컨테이너는
#    옛 값을 그대로 들고 있다. 종전 이 스크립트는 그 상태를 「소스 동일 — 건너뛴다」로
#    **초록불 통과**시켰다. 그래서 지금은 소스와 env 를 **둘 다** 보고, 둘 다 검증한다.
#
# 사용법:
#   ./scripts/redeploy.sh              # pull → 빌드 → 반영 → 검증
#   ./scripts/redeploy.sh --no-pull    # 이미 받아둔 로컬 코드로만
#   ./scripts/redeploy.sh --force      # 소스가 같아도 강제로 다시 빌드
#
# 재빌드 비용: 의존성(pyproject·uv.lock)이 안 바뀌면 레이어 캐시가 살아서 소스 레이어만
# 다시 만든다 — 보통 5~10초. app 만 잠깐 재시작하고 db·cloudflared 는 안 끊긴다.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

# .env 를 이 스크립트의 환경으로 읽어들인다(PUBLIC_BASE_URL·COMPOSE_FILE 이 여기 있다).
# ⚠ set -a 로 export 한다 — docker compose 는 자기 몫을 스스로 읽지만, 이 스크립트가
#   직접 쓰는 값(PUBLIC_BASE_URL)은 셸에 없으면 못 본다.
if [ -f .env ]; then set -a; . ./.env; set +a; fi

# 🔴 실 주소를 저장소에 남기지 않는다 — .env 에서 읽는다(기존 규율).
#    ⚠ 없으면 여기서 멈춘다. 검증 단계(§6)가 조용히 건너뛰면 「배포됐다」가 거짓말이 된다.
: "${PUBLIC_BASE_URL:?PUBLIC_BASE_URL 이 .env 에 없다 — 공개 검증 대상 주소다}"
DOMAIN="$PUBLIC_BASE_URL"
PULL=1
FORCE=0
for a in "$@"; do
    case "$a" in
        --no-pull) PULL=0 ;;
        --force)   FORCE=1 ;;
    esac
done

# 🔴 BuildKit 은 기본으로 provenance/SBOM attestation 을 붙이는데, 그게 **매 빌드마다**
#    이미지 digest 를 바꾼다. 그러면 소스가 한 글자도 안 바뀌었는데 compose 가
#    「이미지가 달라졌다」고 보고 app 을 recreate 한다 — 승우님이 붙어 있는 동안
#    아무 이유 없이 5초씩 끊긴다(2026-08-19 실측: 무변경 재실행에 28초 + 재시작).
export BUILDX_NO_DEFAULT_ATTESTATIONS=1

red()  { printf '\033[31m%s\033[0m\n' "$*"; }
grn()  { printf '\033[32m%s\033[0m\n' "$*"; }
step() { printf '\n\033[1m▶ %s\033[0m\n' "$*"; }

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# 컨테이너가 **지금 들고 있는** env ↔ compose 가 **줄 예정인** env 를 대조해 다른 키만 뱉는다
# (같으면 무출력). 값은 절대 출력하지 않는다 — API 키·터널 토큰이 그대로 들어있다.
#
# 🔴 기대값을 `.env` 에서 직접 읽으면 안 된다. 두 군데서 틀어진다:
#    · compose 의 `environment:` 가 env_file 을 이긴다 — DATABASE_URL 은 **일부러** db:5432 로
#      덮인다. .env 원문(localhost:5434)과 비교하면 영원히 「불일치」다.
#    · env_file 의 따옴표는 compose 가 벗겨서 넣는다(`LANGSMITH_PROJECT="CHECKON"` → CHECKON).
#    `docker compose config` 는 그 둘을 **이미 적용한 최종값**을 준다. 그게 정본이다.
#
# ⚠ 값에 개행이 든 키는 못 본다(printenv 가 줄 단위라서). 지금 .env 엔 없다.
env_drift() {
    docker compose exec -T app printenv </dev/null 2>/dev/null | tr -d '\r' > "$TMP/cont.env" || return 0
    docker compose config --format json | python3 -c '
import json, sys
cur = {}
for line in open(sys.argv[1], encoding="utf-8"):
    key, sep, val = line.rstrip("\n").partition("=")
    if sep:
        cur[key] = val
exp = json.load(sys.stdin)["services"]["app"]["environment"]
for key, val in sorted(exp.items()):
    if val is not None and cur.get(key) != val:
        print(key)
' "$TMP/cont.env"
}

BEFORE=$(git rev-parse --short HEAD)
step "현재 $(git branch --show-current) @ ${BEFORE}"

# ── 1) 최신 받기
if [ $PULL -eq 1 ]; then
    step "git pull --ff-only"
    # --ff-only: 자동 머지 커밋을 만들지 않는다. 갈라졌으면 여기서 멈추고 사람이 판단한다.
    if ! git pull --ff-only; then
        red "pull 실패. 흔한 원인 두 가지 — 메시지를 보고 갈라라:"
        echo "  · could not read Username → 인증이다. WSL 에는 GitHub 자격증명이 없다(2026-08-19 실측)."
        echo "    Windows 쪽 git 은 이미 인증돼 있으니 그 자격증명 관리자를 빌려 쓴다:"
        echo "      git config --global credential.helper '"'"'!\"/mnt/c/Program Files/Git/mingw64/bin/git-credential-manager.exe\"'"'"'"
        echo "    ⚠ 경로에 공백이 있다 — ! 와 따옴표가 **둘 다** 필요하다."
        echo "      빼먹으면 \"/mnt/c/Program: not found\" 가 나고 인증 실패로 위장한다."
        echo "  · non-fast-forward → 로컬 커밋이 원격과 분기했다. 직접 정리한 뒤 다시 실행하라."
        exit 1
    fi
    AFTER=$(git rev-parse --short HEAD)
    if [ "$BEFORE" = "$AFTER" ]; then
        echo "  새 커밋 없음 (${AFTER})"
    else
        echo "  ${BEFORE} → ${AFTER}"
        git log --oneline "${BEFORE}..${AFTER}" | sed 's/^/    /'
    fi
fi

# ── 2) 빌드 + 기동
# migrate 가 db healthy 후 돌고, app 은 migrate 성공 종료 후 뜬다(compose 가 순서를 강제한다).
# 새 마이그레이션이 pull 로 들어왔으면 여기서 자동 적용된다.
# 소스도 env 도 그대로면 빌드도 재시작도 하지 않는다. 「재배포를 돌렸다」와 「재시작이
# 필요했다」는 다른 말이다 — 필요 없을 때 안 끊는 것이 이 스크립트의 일이다.
step "변경 감지"
HOST_H=$(find src -type f -not -path '*/__pycache__/*' | sort | xargs md5sum | md5sum | cut -d' ' -f1)
CONT_H=$(docker compose exec -T app sh -c "cd /app && find src -type f -not -path '*/__pycache__/*' | sort | xargs md5sum | md5sum" </dev/null 2>/dev/null | cut -d' ' -f1 || true)

SRC_CHANGED=1
[ -n "$CONT_H" ] && [ "$HOST_H" = "$CONT_H" ] && SRC_CHANGED=0

# 🔴 소스 해시 **밖**의 변경은 compose 에게 물어본다 — 손으로 축을 세면 반드시 하나를 뺀다.
#    실제로 뺐다: env 를 축에 넣은 직후 `docker-compose.yml` 의 migrate command 를 고쳤는데
#    「소스·env 동일」로 건너뛰어 **반영이 안 됐다**(2026-08-20 실측). compose 는 compose 파일·
#    env_file 최종값·이미지 digest 를 전부 config-hash 에 넣으므로, 그 판단을 그대로 빌린다.
#    ⚠ `--dry-run` 은 아무것도 바꾸지 않는다.
PENDING=""
[ -n "$CONT_H" ] && PENDING=$(docker compose up -d --dry-run 2>&1 | awk '$NF=="Recreate"||$NF=="Create" {print $2}' | sort -u || true)

[ $SRC_CHANGED -eq 1 ] && [ -n "$CONT_H" ] && echo "  소스 차이 있음"
[ -n "$PENDING" ] && echo "  다시 만들 컨테이너: $(echo "$PENDING" | tr '\n' ' ')"

if [ $FORCE -eq 0 ] && [ -n "$CONT_H" ] && [ $SRC_CHANGED -eq 0 ] && [ -z "$PENDING" ]; then
    echo "  소스·설정 동일 — 빌드·재시작을 건너뛴다 (강제하려면 --force)"
elif [ -z "$CONT_H" ] || [ $SRC_CHANGED -eq 1 ] || [ $FORCE -eq 1 ]; then
    [ -z "$CONT_H" ] && echo "  컨테이너 미기동 — 빌드한다"
    step "빌드 + 기동"
    docker compose up -d --build
else
    # 소스는 그대로고 설정만 바뀐 경우 — 이미지는 손댈 게 없다. 빌드를 건너뛴다.
    # compose 가 위에서 말한 그 컨테이너만 다시 만든다(나머지는 Running 그대로 — 안 끊긴다).
    step "기동 (빌드 없음)"
    docker compose up -d
fi

# ── 3) migrate 결과 — 조용히 실패하면 앱이 없는 테이블을 친다
step "마이그레이션 확인"
MIG_EXIT=$(docker inspect --format '{{.State.ExitCode}}' checkon-ai-migrate-1 2>/dev/null || echo "?")
if [ "$MIG_EXIT" != "0" ]; then
    red "migrate 종료코드 ${MIG_EXIT} — 실패다. 로그:"
    docker compose logs migrate --tail 30
    exit 1
fi
echo "  exit 0 · $(docker compose exec -T app alembic current 2>/dev/null | tail -1)"

# ── 3-b) 🔴 **체크포인트 테이블** — alembic 이 안 만든다(LangGraph 소유 · Base.metadata 밖).
#    compose 의 migrate 가 `python -m ai.agents.checkpointer` 까지 돌지만, 여기서 **결과를
#    확인**한다. 「명령을 돌렸다」와 「테이블이 있다」는 다른 말이고, 없으면 counsel·
#    problem_generation·import 프로브 잡이 전부 `worker_internal_error` 로 죽는데
#    **기동은 정상이고 /v1/ready 는 200** 이라 조용하다(2026-08-20 · 8일간 실측).
step "체크포인트 테이블 확인"
CP_MISSING=$(docker compose exec -T db psql -U "${POSTGRES_USER:-checkon}" -d "${POSTGRES_DB:-checkon_ai}" -tAc "
SELECT t.name FROM (VALUES ('checkpoints'),('checkpoint_writes'),('checkpoint_blobs'),('checkpoint_migrations')) AS t(name)
WHERE NOT EXISTS (SELECT 1 FROM information_schema.tables i
                  WHERE i.table_schema='public' AND i.table_name=t.name);" </dev/null 2>/dev/null | tr -d '\r' | grep -v '^$' || true)
if [ -n "$CP_MISSING" ]; then
    red "  없는 테이블:"
    echo "$CP_MISSING" | sed 's/^/    /'
    echo "  → docker compose exec -T app python -m ai.agents.checkpointer 로 만들어라."
    exit 1
fi
grn "  4/4 존재"

# ── 4) app healthy 대기
step "app 기동 대기"
for i in $(seq 1 40); do
    H=$(docker inspect --format '{{.State.Health.Status}}' checkon-ai-app-1 2>/dev/null || echo starting)
    [ "$H" = "healthy" ] && { echo "  healthy (${i}s)"; break; }
    if [ "$i" = "40" ]; then
        red "40초 안에 healthy 가 안 됐다. 로그:"
        docker compose logs app --tail 30
        exit 1
    fi
    sleep 1
done

# ── 5) 🔴 **반영 검증** — 여기가 이 스크립트의 핵심이다.
#    「빌드 성공」과 「컨테이너가 새 코드로 돈다」는 다른 말이다. 캐시가 잘못 맞거나
#    .dockerignore 가 소스를 빼면 빌드는 웃으며 성공하고 옛 코드가 그대로 돈다.
# 🔴 `*.py` 만 보면 안 된다(2026-08-20 실측) — PR #299 는 파이썬을 **한 줄도 안 고치고**
#    `composition/buffer_lexicon.yaml` 만 바꿨다. .py 해시는 멀쩡히 일치해서 이 스크립트가
#    「반영됨」으로 통과시켰고, 실제로는 옛 사전이 돌고 있었다. 사전·프롬프트·설정이 죄다
#    데이터 파일인 코드베이스다 — **확장자로 거르지 말고 src 전체를 본다.**
# ⚠ `__pycache__` 만 뺀다. 호스트는 개발하며 .pyc 가 쌓이고 컨테이너는 빌드 때
#   UV_COMPILE_BYTECODE 로 제 것을 만든다 — 항상 다르고, 달라도 상관없다.
step "호스트 ↔ 컨테이너 소스 대조"
HOST_H=$(find src -type f -not -path '*/__pycache__/*' | sort | xargs md5sum | md5sum | cut -d' ' -f1)
CONT_H=$(docker compose exec -T app sh -c "cd /app && find src -type f -not -path '*/__pycache__/*' | sort | xargs md5sum | md5sum" | cut -d' ' -f1)
if [ "$HOST_H" != "$CONT_H" ]; then
    red "  불일치! 컨테이너가 옛 코드로 돌고 있다."
    red "  host=${HOST_H}  container=${CONT_H}"
    echo "  → docker compose build --no-cache app 로 다시 시도하라."
    exit 1
fi
grn "  일치 (${HOST_H})"

# ── 5-b) 🔴 **env 반영 검증** — 소스와 정확히 같은 이유로 조용히 틀어진다.
#    빌드는 웃으며 성공하고, 새 키가 안 들어간 채로 서버는 200 을 준다. `STORE_BACKEND` 가
#    안 들어가면 저장소가 통째로 다른 백엔드로 도는데도 health 는 초록이다.
#    ⚠ 비교 대상은 .env 원문이 아니라 `docker compose config` 의 최종값이다(env_drift 주석).
step "env 반영 대조"
LEFT=$(env_drift)
if [ -n "$LEFT" ]; then
    red "  불일치! 컨테이너가 옛 env 로 돌고 있다. 다른 키:"
    echo "$LEFT" | sed 's/^/    /'
    echo "  → docker compose up -d --force-recreate app 로 다시 시도하라."
    exit 1
fi
grn "  일치 ($(docker compose config --format json | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["services"]["app"]["environment"]))') 키)"

# ── 6) 공개 경로 — 승우님이 실제로 보는 주소로 확인한다
step "공개 엔드포인트"
FAIL=0
for p in /v1/health /v1/ready /openapi.json; do
    C=$(curl -s -m 15 -o /dev/null -w '%{http_code}' "${DOMAIN}${p}" || echo 000)
    if [ "$C" = "200" ]; then printf '  %s  %s\n' "$C" "$p"
    else red "  ${C}  ${p}"; FAIL=1; fi
done
[ $FAIL -eq 1 ] && { red "공개 경로 실패 — 터널을 확인하라: docker compose logs cloudflared --tail 30"; exit 1; }

grn "
재배포 완료 — $(git rev-parse --short HEAD)
BE Base URL: ${DOMAIN}"
