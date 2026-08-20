"""배포 스택(compose)이 **DDL 두 단계를 실제로 돌리는지** 고정한다.

🔴 **이건 스타일 규칙이 아니라 8일 장애의 재발 방지선이다.**

2026-08-12에 `STORE_BACKEND` 기본값이 `pg`로 뒤집히면서
`python -m ai.agents.checkpointer`가 배포 필수 단계가 됐다. 같은 날 세운 가드는
*"배포 명령이 초기화 함수를 실행하는가"* 를 물었고 계속 green이었다 — 그 질문의 답이
`src/` 안에 있었기 때문이다. **아무도 못 물은 질문은 "배포가 그 명령을 실제로
돌리는가"였고, 그 답이 사는 자리(`docker-compose.yml`)가 저장소 밖이라 검사가 닿을 수
없었다.** 그래서 8/12에 재현·문서화까지 해 놓고 8일 뒤(8/12~8/20) 그대로 터졌다 —
counsel·problem_generation·import 프로브 잡이 전량 `worker_internal_error`.

⚠ 증상이 조용하다. 앱은 정상 기동하고 `/v1/ready`도 200을 준다(테이블 0/4인 채로 8일간).
그래서 "돌려 보고 확인"이 아니라 **파일의 값으로** 고정한다.

⚠ **YAML을 정규식으로 읽지 않는다** — `yaml.safe_load`로 파싱해서 단언한다.
들여쓰기·따옴표·리스트 표기 변주에 안 깨져야 가드다.

관련: `docs/99_open_items.md` #117·#118·#119·#120 · 결정 로그 138 ·
`src/ai/agents/checkpointer.py` 머리말.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import pytest
import yaml  # type: ignore[import-untyped]  # PyYAML은 공식 타입 정보를 제공하지 않는다.

_ROOT: Final = Path(__file__).resolve().parents[3]

#: 배포 스택 — migrate·app·cloudflared. db는 공통 파일(`docker-compose.yml`)이 소유한다.
_DEPLOY_COMPOSE: Final = _ROOT / "docker-compose.deploy.yml"
#: 공통 스택 — db 하나. 맥(로컬)·윈도우(배포) 양쪽이 쓴다.
_COMMON_COMPOSE: Final = _ROOT / "docker-compose.yml"

#: 🔴 DDL 단계가 둘이다. alembic은 앱 소유 테이블만 만들고, LangGraph 체크포인트 4종은
#: `db/models.py`의 `Base.metadata` **밖**이라 alembic이 절대 안 만든다.
_ALEMBIC_STEP: Final = "alembic upgrade head"
_CHECKPOINTER_STEP: Final = "python -m ai.agents.checkpointer"


def _load(path: Path) -> dict[str, Any]:
    """compose 파일을 값으로 읽는다(정규식 금지 — 표기 변주에 안 깨지게)."""
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"{path.name}이 매핑이 아니다"
    return loaded


def _service(path: Path, name: str) -> dict[str, Any]:
    services = _load(path).get("services") or {}
    assert name in services, f"{path.name}에 `{name}` 서비스가 없다"
    service = services[name]
    assert isinstance(service, dict), f"{path.name}의 `{name}`이 매핑이 아니다"
    return service


def _command_text(service: dict[str, Any]) -> str:
    """`command`를 문자열·리스트 어느 표기든 하나의 텍스트로 눕힌다."""
    command = service.get("command")
    if command is None:
        return ""
    if isinstance(command, str):
        return command
    return " ".join(str(part) for part in command)


@pytest.mark.parametrize(
    ("step", "why"),
    [
        (
            _CHECKPOINTER_STEP,
            "🔴 8/12 가드가 못 물었던 바로 그 질문이다. 이 단계가 빠지면 체크포인트 "
            "4테이블이 안 생기고 counsel·problem_generation·import 프로브 잡이 전량 "
            "`worker_internal_error`로 죽는다 — 기동은 정상이고 /v1/ready도 200이라 조용하다",
        ),
        (
            _ALEMBIC_STEP,
            "한쪽만 남으면 다른 형태로 같은 일이 난다 — 앱 소유 테이블이 없는 채로 뜬다",
        ),
    ],
)
def test_the_migrate_service_runs_both_ddl_steps(step: str, why: str) -> None:
    """🔴 **배포가 그 명령을 실제로 돌리는가** — §C-4의 본체."""
    command = _command_text(_service(_DEPLOY_COMPOSE, "migrate"))
    assert step in command, f"migrate 서비스의 command에 `{step}`이 없다(현재: {command!r}). {why}"


def test_the_app_waits_for_migrate_to_succeed() -> None:
    """두 DDL이 있어도 앱이 먼저 뜨면 소용없다 — 순서를 compose가 강제해야 한다."""
    depends_on = _service(_DEPLOY_COMPOSE, "app").get("depends_on") or {}
    assert isinstance(depends_on, dict), (
        "app.depends_on이 리스트 표기다 — 조건을 못 건다. 매핑 표기로 두고 "
        "migrate에 service_completed_successfully를 걸어라"
    )
    assert "migrate" in depends_on, "app이 migrate를 기다리지 않는다"
    condition = (depends_on["migrate"] or {}).get("condition")
    assert condition == "service_completed_successfully", (
        f"app.depends_on.migrate.condition이 {condition!r}이다. "
        "`service_completed_successfully`가 아니면 DDL이 끝나기 전에 앱이 뜨고, "
        "첫 요청이 없는 테이블을 친다"
    )


#: 🔴 「빠지면 조용히 다른 값으로 도는 자리」 — 계약값·비밀에는 대체값을 두지 않는다.
#: `${VAR:-기본}`은 미설정뿐 아니라 **빈 값**에도 기본을 쓴다. `${VAR:?…}`는 둘 다 거부한다.
_NO_FALLBACK_VARS: Final = ("APP_PORT", "POSTGRES_PASSWORD")


@pytest.mark.parametrize("variable", _NO_FALLBACK_VARS)
def test_the_contract_values_have_no_silent_fallback(variable: str) -> None:
    """🔴 §A가 조용히 되돌아가는 것을 막는다.

    `APP_PORT`는 자유 변수가 아니다 — Cloudflare 대시보드의 origin 포트에 못이 박혀 있어,
    빠지면 **도메인은 살아 있고 컨테이너는 전부 healthy인데 502**가 된다.
    `POSTGRES_PASSWORD`는 빠지면 새 기계가 약한 비밀번호로 조용히 뜬다.
    """
    raw = "\n".join(path.read_text(encoding="utf-8") for path in (_COMMON_COMPOSE, _DEPLOY_COMPOSE))
    assert f"${{{variable}:-" not in raw, (
        f"`${{{variable}:-…}}` 대체값이 살아 있다. 계약값·비밀에는 대체값을 두지 않는다 "
        f"— `${{{variable}:?사유}}`로 두면 빠졌을 때 compose가 배포를 멈춘다"
    )
    assert f"${{{variable}:?" in raw, (
        f"`${{{variable}:?사유}}` 형태가 없다. 사유 문면까지 있어야 다음 사람이 "
        "무엇을 채워야 하는지 안다"
    )
