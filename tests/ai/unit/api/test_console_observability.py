"""콘솔 관측 — 꺼짐 기본값 · 4xx 사유 · 실패 격리 (PR-ψ).

🔴 **동어반복을 피한다.** "플래그를 켰더니 켜졌다"는 검사는 아무것도 지키지 못한다.
여기서 지키는 불변식은 셋이다::

    ① 아무 설정도 안 하면 **아무것도 출력되지 않는다** (기본 동작 무변경)
    ② 4xx 줄에 **필드명과 입력값이 둘 다** 있다 (하나만 있으면 또 물어봐야 한다)
    ③ 출력부가 터져도 **요청은 산다** (관측이 서비스를 죽이면 안 된다)

🔴 ③이 가장 중요하다. 출력 함수가 예외를 던지도록 **강제해 놓고** 응답 코드를 본다.

⚠ 실 LLM 호출 0회 — 콘솔은 순수 렌더링이라 페이크조차 필요 없다.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from ai.api import console as api_console
from ai.api.console import install_console_handlers
from ai.contracts.execution import Capability, ExecutionContext, VersionSet
from ai.runtime.console import (
    LOGGER_NAME,
    MISSING_54,
    emit_failure_count,
    group_note,
    render_llm_call,
    reset_console_settings,
    reset_emit_failures,
    root_cause,
)

#: 8/13에 실제로 하루를 태운 값 — 규칙만 보여서는 못 찾았던 그 입력이다.
_BAD_SOURCE: Final = "MANUAL"

#: 고정 시계 — 주입값이 **변환 없이** 그대로 화면에 나와야 한다(`system_local_now` 참조).
_FIXED_NOW: Final = datetime(2026, 8, 13, 15, 42, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _isolate_console() -> Iterator[None]:
    """설정·로거·카운터를 매 테스트 앞뒤로 비운다(캐시가 새면 옆 테스트가 오염된다)."""
    reset_console_settings()
    reset_emit_failures()
    yield
    reset_console_settings()
    reset_emit_failures()


class _Recorder(logging.Handler):
    """`ai.console`에 직접 붙는 캡처 — stdout 캡처 타이밍에 의존하지 않는다.

    ⚠ `StreamHandler`는 **생성 시점의 `sys.stdout`을 붙든다.** 그 결합 시점이
    `TestClient` 워커 스레드 안이라 `capsys`로 잡으면 조용히 빈 결과가 나온다
    (테스트가 통과한 것처럼 보인다). 실제 싱크가 stdout인지는
    `test_console_sinks_to_stdout_only_and_does_not_propagate`가 따로 잠근다.
    """

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@pytest.fixture
def console_out() -> Iterator[_Recorder]:
    """콘솔이 낸 줄 전부."""
    recorder = _Recorder()
    logger = logging.getLogger(LOGGER_NAME)
    logger.addHandler(recorder)
    yield recorder
    logger.removeHandler(recorder)


class _Body(BaseModel):
    source: str = Field(pattern="^(DIAGNOSTIC|ASSIGNMENT)$")


def _llm_context() -> ExecutionContext:
    """LLM 싱크 검사용 실행 컨텍스트 — 값은 무엇이든 좋다(콘솔은 읽지 않는다)."""
    return ExecutionContext(
        execution_id=UUID("00000000-0000-4000-8000-000000000901"),
        tenant_id="tn_9f3c",
        capability=Capability.DETECTION,
        input_snapshot_hash="sha256:console",
        versions=VersionSet(
            pipeline_version="v2.1",
            engine_version="rules-1.0",
            schema_version="0.1",
            contract_version="0.1",
            prompt_version="v0.1",
        ),
    )


def _build_app() -> FastAPI:
    """콘솔 배선만 얹은 최소 앱 — 전체 앱을 띄우지 않고 이 모듈만 검사한다.

    시계를 고정 주입해 타임스탬프가 흔들리지 않게 한다(03 §3).
    """
    app = FastAPI()

    @app.post("/echo")
    async def _echo(body: _Body) -> dict[str, str]:
        return {"source": body.source}

    @app.get("/boom")
    async def _boom() -> dict[str, str]:
        raise ConnectionRefusedError("원격 컴퓨터가 연결을 거부했습니다")

    install_console_handlers(app, clock=lambda: _FIXED_NOW)
    return app


def test_console_off_by_default_prints_nothing(
    console_out: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 플래그가 없으면 조용하다 — 그리고 **응답은 종전과 같다**.

    배선을 얹은 것만으로 화면이 바뀌면 이 PR은 기본 동작을 바꾼 것이다.
    """
    for flag in ("CONSOLE_ERROR_LOG", "CONSOLE_LLM_LOG", "CONSOLE_OK_LOG"):
        monkeypatch.delenv(flag, raising=False)
    client = TestClient(_build_app())

    ok = client.post("/echo", json={"source": "DIAGNOSTIC"})
    bad = client.post("/echo", json={"source": _BAD_SOURCE})

    assert ok.status_code == 200
    assert bad.status_code == 422  # 응답 계약 무변경 — 콘솔은 응답에 손대지 않는다
    assert bad.json()["detail"][0]["loc"] == ["body", "source"]
    assert console_out.lines == []


def test_validation_error_line_carries_field_and_input(
    console_out: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 **필드명과 입력값이 둘 다** 한 줄에 있어야 한다.

    규칙만 보이면 *"백엔드가 대체 뭘 보냈나"* 를 다시 물어봐야 한다 — 8/13이 그랬다.
    ⚠ 고의 파괴: `field_detail_lines`에서 `(입력=…)`을 빼면 마지막 단언이 깨진다.
    """
    monkeypatch.setenv("CONSOLE_ERROR_LOG", "1")
    client = TestClient(_build_app())

    response = client.post(
        "/echo",
        json={"source": _BAD_SOURCE},
        headers={"X-Request-Id": "abc123", "X-Tenant-Id": "tn_9f3c"},
    )
    out = "\n".join(console_out.lines)

    assert response.status_code == 422
    assert "[15:42:01] 422 POST /echo" in out
    assert "req=abc123" in out
    assert "body.source" in out  # ← 어느 필드인가
    assert f"'{_BAD_SOURCE}'" in out  # ← 🔴 무엇을 보냈는가 (이 줄이 고의 파괴 지점)


def test_console_failure_does_not_break_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """🔴 **출력부가 터져도 응답은 정상이다** — 관측이 서비스를 죽이면 안 된다.

    출력 함수가 반드시 예외를 던지도록 강제해 놓고 200을 확인한다. 그리고 실패가
    **조용히 사라지지 않았는지**(카운터)도 본다 — 삼키되 세는 것이 이 모듈의 규약이다.
    """
    monkeypatch.setenv("CONSOLE_OK_LOG", "1")
    monkeypatch.setenv("CONSOLE_ERROR_LOG", "1")

    def _explode(**_: object) -> str:
        raise RuntimeError("콘솔 렌더링이 터졌다")

    monkeypatch.setattr(api_console, "_request_line", _explode)
    client = TestClient(_build_app())

    response = client.post("/echo", json={"source": "DIAGNOSTIC"})

    assert response.status_code == 200
    assert response.json() == {"source": "DIAGNOSTIC"}
    assert emit_failure_count() >= 1  # 삼켰지만 세었다 (조용한 누락 금지)


def test_server_error_prints_three_line_summary_and_keeps_raising(
    console_out: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5xx는 요약 3줄을 **앞에 붙이고** 예외를 그대로 올린다.

    🔴 예외를 다시 올리는 것이 트레이스백 보존의 전부다 — 여기서 삼키면 uvicorn이
    찍을 전문이 사라진다(§2-A2 "트레이스백을 지우지 마라").
    """
    monkeypatch.setenv("CONSOLE_ERROR_LOG", "1")
    client = TestClient(_build_app(), raise_server_exceptions=False)

    response = client.get("/boom", headers={"X-Request-Id": "abc123"})
    out = "\n".join(console_out.lines)

    assert response.status_code == 500  # 예외가 계속 올라가 500이 됐다 = 안 삼켰다
    assert "[15:42:01] 500 GET /boom" in out
    assert "ConnectionRefusedError: 원격 컴퓨터가 연결을 거부했습니다" in out
    assert "at " in out and "_boom" in out  # 마지막 프레임
    assert "(전체 트레이스백은 아래)" in out


def test_authorization_header_never_reaches_the_console(
    console_out: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 API 키·Authorization은 어떤 경로로도 출력되지 않는다.

    구조적 보증이다 — `_request_line`이 읽는 헤더는 X-Request-Id·X-Tenant-Id 둘뿐이다.
    """
    monkeypatch.setenv("CONSOLE_ERROR_LOG", "1")
    client = TestClient(_build_app())

    client.post(
        "/echo",
        json={"source": _BAD_SOURCE},
        headers={"Authorization": "Bearer sk-super-secret", "X-Api-Key": "sk-also-secret"},
    )
    out = "\n".join(console_out.lines)

    assert "sk-super-secret" not in out
    assert "sk-also-secret" not in out
    assert "Bearer" not in out


def test_llm_box_marks_uncollected_fields_instead_of_dropping_them() -> None:
    """🔴 `finish_reason`·`reasoning_tokens`는 **자리표시자로 남는다**(99 #54).

    조용히 빼면 다음 사람은 그 값이 애초에 존재하지 않는다고 믿는다 — 8/13 반나절이
    정확히 그 착시였다. 값처럼 읽히지 않게 `⟪미수집⟫`으로 찍는다.
    """
    lines = render_llm_call(
        role="narrator",
        model="gpt-5.6-luna",
        prompt_masked="당신은 학습 코치입니다",
        response_masked="이번 주 정답률이",
        tokens_in=537,
        tokens_out=41,
        latency_ms=2300,
        outcome="ok",
        seed=20260805,
    )
    box = "\n".join(lines)

    assert box.startswith("┌─ LLM narrator · gpt-5.6-luna · seed=20260805")
    assert "PROMPT (537 tok)" in box
    assert "RESPONSE (41 tok · 2.3s)" in box
    assert f"finish_reason    = {MISSING_54}" in box
    assert f"reasoning_tokens = {MISSING_54}" in box
    assert box.endswith("└─ outcome=ok")


def test_llm_box_never_prints_unmasked_body_when_oversized() -> None:
    """상한 초과분은 마스킹을 **안 거친** 문자열이다 — 찍지 않고 사실만 남긴다."""
    box = "\n".join(
        render_llm_call(
            role="counselor",
            model="gpt-5.6-luna",
            prompt_masked="",
            response_masked="",
            tokens_in=None,
            tokens_out=None,
            latency_ms=None,
            outcome="ok",
            oversized=True,
        )
    )

    assert "⟪본문 상한 초과 — 원장·콘솔 모두 미보관⟫" in box
    assert "PROMPT" not in box


def test_root_cause_does_not_retreat_into_the_wrapper_group() -> None:
    """🔴 **anyio TaskGroup 모양** — 진짜 원인을 쥐고 있는데 껍데기로 후퇴하면 안 된다.

    8/13 실측: 미들웨어가 받은 예외는 올바른 `ConnectionRefusedError`인데 그
    `__context__`가 **자기를 담고 있는** `ExceptionGroup`을 가리켰다(`BaseHTTPMiddleware`가
    하위 앱을 TaskGroup에 태우기 때문). 따라가면 요약이 이렇게 나온다::

        ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)
        at .venv/…/anyio/_backends/_asyncio.py:815 __aexit__

    **요약이 틀리면 없느니만 못하다** — 리허설 중에 엉뚱한 파일을 열게 만든다.
    """
    real = ConnectionRefusedError("[Errno 61] Connection refused")
    real.__context__ = ExceptionGroup("unhandled errors in a TaskGroup", [real])

    assert root_cause(real) is real


def test_root_cause_unwraps_a_group_it_starts_at() -> None:
    """반대 방향 — 시작점이 그룹이면 **뚫고 들어간다**(껍데기는 정보가 0인 줄이다)."""
    real = ConnectionRefusedError("[Errno 61] Connection refused")
    group = ExceptionGroup("unhandled errors in a TaskGroup", [real, ValueError("형제")])

    assert root_cause(group) is real
    assert group_note(group) == " (형제 예외 1건 더 — 전문 참조)"


def test_root_cause_survives_a_cyclic_exception_chain() -> None:
    """예외 사슬이 순환해도 멈춘다 — 모든 루프에 상한(CLAUDE.md 불변식 6)."""
    first = ValueError("첫째")
    second = KeyError("둘째")
    first.__cause__ = second
    second.__cause__ = first  # 🔴 순환

    assert root_cause(first) is second


def test_real_app_shows_the_rejected_field_without_changing_the_envelope(
    console_out: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 **A-1의 진짜 표적** — 실제 라우터의 400에서 필드명과 입력값이 보인다.

    이 저장소의 라우터는 인자로 `Request`를 받고 `model_validate()`를 직접 부른다
    (`detect.py :: post_detect` — *"계약에 없는 422가 생기고 현행 400 `INVALID_SCHEMA`가
    사라진다"*). 그래서 **`RequestValidationError`는 발생하지 않는다.** 사유는
    `SnapshotInvalid.__cause__`에 매달린 pydantic 오류에만 남아 있고, 응답 본문은
    `_format_validation_error`가 `input`을 버린 뒤라 **거기엔 없다.**

    ⇒ 콘솔이 그 결손을 메우되, **응답은 한 바이트도 바뀌지 않아야 한다.**
    """
    from ai.api.app import create_app

    body = {
        "snapshot_meta": {"snapshot_hash": "sha256:x"},
        "learning_events": [{"student_ref": "st_01", "source": _BAD_SOURCE}],
    }
    headers = {"X-Tenant-Id": "tn_9f3c", "X-Request-Id": "abc123", "Idempotency-Key": "k-1"}

    monkeypatch.delenv("CONSOLE_ERROR_LOG", raising=False)
    reset_console_settings()
    silent = TestClient(create_app()).post("/v1/detect", json=body, headers=headers)
    assert console_out.lines == []  # 꺼져 있을 때는 조용하다

    monkeypatch.setenv("CONSOLE_ERROR_LOG", "1")
    reset_console_settings()
    loud = TestClient(create_app()).post("/v1/detect", json=body, headers=headers)
    out = "\n".join(console_out.lines)

    # 🔴 응답은 종전과 동일하다 — 콘솔은 추가 싱크일 뿐이다
    assert silent.status_code == loud.status_code == 400
    assert silent.json() == loud.json()
    assert loud.json()["error"]["code"] == "INVALID_SCHEMA"

    # 🔴 그리고 화면에는 응답에 없는 것이 있다: 어느 필드에 무엇이 들어왔는가
    assert "400 POST /v1/detect" in out
    assert "INVALID_SCHEMA" in out
    assert "source" in out
    assert f"'{_BAD_SOURCE}'" in out  # ← 응답 본문에는 없는 값 (이 줄이 A-1의 존재 이유)


def test_console_never_invents_a_handler_that_was_not_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """🔴 없던 처리기를 새로 만들지 않는다 — 만들면 그 예외의 응답이 바뀐다.

    최소 앱에는 `DomainException` 처리기가 없다. 콘솔은 감쌀 대상이 없으므로
    **등록하지 않고 지나가야** 한다.
    """
    from ai.runtime.errors import DomainException

    monkeypatch.setenv("CONSOLE_ERROR_LOG", "1")
    app = _build_app()

    assert DomainException not in app.exception_handlers


def test_console_sinks_to_stdout_only_and_does_not_propagate() -> None:
    """🔴 **stdout 전용 · 루트로 새지 않는다** — 개인정보 절의 구조적 보증.

    ① 우리 핸들러가 정확히 하나이고 그 스트림이 `sys.stdout`이다 → 파일·외부 싱크가 없다
    ② `propagate=False` → uvicorn 루트 로거를 타고 **파일 핸들러로 흘러가지 않는다**
      (그리고 화면에 두 번 찍히지도 않는다)
    """
    import sys

    from ai.runtime.console import _console_logger, console_handlers

    logger = _console_logger()
    ours = console_handlers()

    assert logger.propagate is False
    # 🔴 남이 이 로거에 핸들러를 먼저 달아도 우리 stdout 핸들러는 **반드시** 붙는다
    #    (pytest가 실제로 그렇게 한다 — 이 단언이 그 회귀를 잠근다)
    assert len(ours) == 1
    assert isinstance(ours[0], logging.StreamHandler)
    assert ours[0].stream is sys.stdout


def test_llm_sink_prints_masked_body_without_touching_the_ledger(
    console_out: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B — 포착 래퍼가 콘솔 싱크를 겸한다. 🔴 **원장 동작은 그대로다.**

    같은 호출에서 ① 콘솔에 박스가 찍히고 ② 인계 슬롯(`take_captured`)에는 종전과
    똑같은 `CapturedBody`가 남는지 **둘 다** 본다 — 싱크를 얹다가 원장을 밀어내면
    이 PR은 실패다.

    ⚠ 실 LLM 호출 0회 — `FakeProvider`다.
    """
    import asyncio

    from fake_provider import FakeProvider

    from ai.contracts.execution import GenerationParams
    from ai.contracts.llm import LLMRequest, ModelRole
    from ai.db.repositories.llm_payload import (
        CapturedBody,
        capture_payloads,
        reset_captured,
        take_captured,
    )

    monkeypatch.setenv("CONSOLE_LLM_LOG", "1")
    reset_captured()

    provider = capture_payloads(FakeProvider(["이번 주 정답률이 평소보다 낮습니다"]))
    request = LLMRequest(
        role=ModelRole.NARRATOR,
        prompt="당신은 학습 코치입니다",
        prompt_id="brief.v1",
        prompt_version="1.0",
        generation_params=GenerationParams(seed=20260805),
    )

    async def _call() -> tuple[str | None, object]:
        # ⚠ 인계 슬롯은 ContextVar다 — `asyncio.run` 밖에서 꺼내면 **다른 컨텍스트**라
        #   항상 None이 나온다(원장이 깨진 게 아니라 읽는 자리가 틀린 것).
        result = await provider.complete(request, _llm_context())
        return result.text, take_captured()

    text, captured = asyncio.run(_call())
    box = "\n".join(console_out.lines)

    assert text == "이번 주 정답률이 평소보다 낮습니다"
    assert "┌─ LLM narrator · fake-model · seed=20260805" in box
    assert "당신은 학습 코치입니다" in box
    assert MISSING_54 in box  # 🔴 결손이 매 호출마다 화면에 뜬다
    assert "└─ outcome=ok" in box
    # 🔴 원장 경로 무변경 — 인계 슬롯에 종전과 같은 본문이 그대로 남아 있다
    assert isinstance(captured, CapturedBody)
    assert captured.request_masked == "당신은 학습 코치입니다"
    assert captured.response_masked == "이번 주 정답률이 평소보다 낮습니다"


def test_llm_sink_is_silent_by_default(
    console_out: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 B도 기본 꺼짐이다 — 학습 실데이터가 흐르는 경로라 이게 가장 중요하다."""
    import asyncio

    from fake_provider import FakeProvider

    from ai.contracts.llm import LLMRequest, ModelRole
    from ai.db.repositories.llm_payload import capture_payloads, reset_captured

    monkeypatch.delenv("CONSOLE_LLM_LOG", raising=False)
    reset_captured()

    provider = capture_payloads(FakeProvider(["응답"]))
    asyncio.run(
        provider.complete(
            LLMRequest(
                role=ModelRole.NARRATOR,
                prompt="학생 st_9f3c의 정답률",
                prompt_id="brief.v1",
                prompt_version="1.0",
            ),
            _llm_context(),
        )
    )

    assert console_out.lines == []
