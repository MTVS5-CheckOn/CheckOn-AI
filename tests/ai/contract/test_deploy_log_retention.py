"""배포 로그 보존 — 재생성이 증거를 지우지 못하게 한다 (99 #267 · #266).

🔴 **2026-08-27 05:25:39 UTC 의 `/v1/detect` 400 은 실제로 찍혔다.** `CONSOLE_ERROR_LOG=1` 이
운영에 들어가 있었고 `api/console.py` 가 `req=<X-Request-Id>` 와 `detail[].field` 까지 낸다.
그런데 20분 뒤 컨테이너가 재생성되면서 그 파일이 사라졌다 — json-file 드라이버는 로그를
컨테이너 수명에 묶고, `redeploy.sh` 는 소스가 바뀌면 app 을 다시 만든다.
⇒ **「봐도 없다」가 아니라 「있었는데 우리가 지웠다」였다.**

🔴 **이 파일이 무는 것은 순서다.** 덤프가 `up -d` 보다 **뒤**로 가면 기능은 그대로인데 뜻이
사라진다 — 이미 지워진 뒤에 뜨기 때문이다. 이름도 코드도 그대로인데 자리 하나로 무의미해지는
형태이고, 그건 99 #206 이 적어 둔 그 병이다.

⚠ **스크립트를 실행하지 않는다** — 텍스트로 읽어 단언한다. 실행하면 docker 를 띄우게 되고
  그건 다른 물음이 된다(그 물음은 윈도우에서 사람이 판다).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final

import pytest
import yaml  # type: ignore[import-untyped]  # PyYAML은 공식 타입 정보를 제공하지 않는다.

_ROOT: Final = Path(__file__).resolve().parents[3]
_COMPOSE: Final = _ROOT / "docker-compose.deploy.yml"
_REDEPLOY: Final = _ROOT / "scripts" / "redeploy.sh"
_GITIGNORE: Final = _ROOT / ".gitignore"

#: 덤프가 뜨는 서비스 — 요청 detail(app)과 LLM 산출(counsel-drain)이 거기 있다.
#: ⚠ `db`·`migrate`·`cloudflared` 는 **일부러 안 뜬다.** 조용한 절단이 아니라 적힌 선택이다.
_DUMPED: Final = ("app", "counsel-drain")


def _script() -> str:
    return _REDEPLOY.read_text(encoding="utf-8")


def _services() -> dict[str, Any]:
    parsed: dict[str, Any] = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    services: dict[str, Any] = parsed["services"]
    return services


# ───────────────────────── ① 회전 상한 ─────────────────────────


@pytest.mark.parametrize("service", ["migrate", "app", "counsel-drain", "cloudflared"])
def test_every_deploy_service_caps_its_log(service: str) -> None:
    """🔴 상한이 없으면 보존을 켜는 순간 디스크가 다음 사고가 된다."""
    options = (_services()[service].get("logging") or {}).get("options") or {}

    assert options.get("max-size"), f"{service}: max-size 가 없다"
    assert options.get("max-file"), f"{service}: max-file 이 없다"


def test_the_services_share_one_logging_block() -> None:
    """🔴 사본 넷이면 갈린다 — 한 앵커를 넷이 참조한다(03 §1)."""
    assert _COMPOSE.read_text(encoding="utf-8").count("logging: *log-rotation") == 4


# ───────────────────────── ② 순서 (이 파일의 존재 이유) ─────────────────────────


def test_the_dump_runs_before_every_recreate() -> None:
    """🔴 **덤프가 모든 `up -d` 보다 앞이다.** 뒤면 이미 지워진 것을 뜬다.

    ⚠ `--dry-run` 은 컨테이너를 안 만든다 — 대상에서 뺀다(그 사실을 여기 적어 둔다).
    """
    script = _script()
    dump_at = script.find("docker compose logs --no-color")
    assert dump_at != -1, "재생성 직전 덤프가 없다"

    recreates = [
        m.start()
        for m in re.finditer(r"docker compose up -d", script)
        if "--dry-run" not in script[m.start() : script.find("\n", m.start())]
        and not script[: m.start()].rsplit("\n", 1)[-1].lstrip().startswith(("#", "echo"))
    ]
    assert recreates, "재생성 호출이 하나도 없다 — 이 검사가 재려는 것이 없다"
    assert dump_at < min(recreates), (
        f"덤프({dump_at})가 재생성({min(recreates)})보다 뒤에 있다 — 지워진 뒤에 뜬다"
    )


def test_the_dump_is_outside_the_skip_branch() -> None:
    """🔴 조건문 **밖**이다 — 예측 못 한 재생성이 이번 사고의 형태였다."""
    script = _script()
    dump_at = script.find("step \"로그 덤프")
    guard_at = script.find("if [ $FORCE -eq 0 ]")

    assert dump_at != -1 and guard_at != -1
    assert dump_at < guard_at, "덤프가 재생성 판정 조건문 안으로 들어갔다"


# ───────────────────────── ③ 자르지 않는다 ─────────────────────────


def test_the_dump_is_not_truncated() -> None:
    """🔴 상한으로 자르면 **무엇을 왜 잘랐는지** 아무도 모른다(조용한 절단 금지)."""
    line = next(
        ln for ln in _script().splitlines() if "docker compose logs --no-color" in ln
    )

    assert "--tail" not in line, f"덤프가 잘린다: {line.strip()}"


@pytest.mark.parametrize("service", _DUMPED)
def test_the_dumped_services_are_named(service: str) -> None:
    """어느 서비스를 뜨는지가 스크립트에 값으로 있다 — 「전부겠지」로 읽히지 않게."""
    assert re.search(rf"for svc in .*\b{re.escape(service)}\b", _script())


# ───────────────────────── ④ 커밋되지 않는다 ─────────────────────────


def test_dumped_logs_are_never_committed() -> None:
    """🔴 덤프에는 학습 사실이 실릴 수 있다 — 저장소로 새면 되돌릴 수 없다."""
    ignored = _GITIGNORE.read_text(encoding="utf-8").splitlines()

    assert "logs/" in ignored


# ───────────────────────── ⑤ 플래그가 보인다 ─────────────────────────


def test_the_console_flag_is_printed_on_every_deploy() -> None:
    """🔴 「운영 상시 활성 금지」를 무는 것이 문면뿐이었다(99 #266).

    ⚠ 테스트로는 못 잡는다 — `console_env_pin` 이 `.env` 를 끊기 때문이고 그건 옳다(99 #63).
      그래서 **배포할 때마다 눈에 보이게** 하는 것이 처방이고, 이 검사가 그 처방을 문다.
    """
    script = _script()

    assert "CONSOLE_LLM_LOG" in script, "배포가 콘솔 플래그를 안 보여준다"
    assert 'LLM_LOG="?"' in script, "값을 못 읽었을 때 「모른다」가 아니라 다른 값으로 채운다"
