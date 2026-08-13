"""콘솔 관측의 HTTP 배선 — 4xx 거부 사유 · 5xx 요약 · 2xx 한 줄 (PR-ψ).

🔴 **운영 상시 활성 금지 — 개발자 콘솔 전용.** 규율·개인정보·실패 격리는 전부
`ai.runtime.console` 모듈 docstring이 정본이다. 여기는 **FastAPI에 꽂는 배선**만 있다.

━━ 왜 두 파일로 갈랐나 ━━

`runtime/console.py`는 FastAPI를 모른다(`runtime/`은 웹 프레임워크를 import하지 않는다 —
`llm_payload.py`의 LLM 싱크도 같은 코어를 쓴다). 프레임워크에 붙는 코드만 여기 둔다.

그리고 **`api/app.py`가 양자 승인 파일이다.** 핸들러 본문이 거기 들어가면 승인 표면이
그만큼 커진다. 그래서 `install_console_handlers(app)` 한 줄로 끝나게 만들었다 —
app.py의 diff는 import 1줄 + 호출 1줄, **총 2줄**이다.

━━ 🔴 미들웨어에서 응답 본문을 읽지 않는다 ━━

`response.body`를 만지면 **스트리밍 응답이 깨진다**(본문을 소비해 버린다). 그래서 4xx의
사유는 응답에서 캐내지 않고 **예외 처리기가 ContextVar에 미리 남긴 것**을 꺼내 쓴다.
미들웨어가 읽는 것은 status_code·헤더·경과시간뿐이다.

━━ 출력 순서 (ContextVar가 필요한 이유) ━━

Starlette의 스택은 ``ServerErrorMiddleware → [사용자 미들웨어] → ExceptionMiddleware``다.
`RequestValidationError` 처리기는 **사용자 미들웨어보다 안쪽**에서 돌기 때문에, 처리기가
직접 출력하면 필드 줄이 헤더 줄보다 **먼저** 나온다. 처리기는 ContextVar에 담기만 하고
미들웨어가 헤더와 함께 한 덩어리로 낸다 —::

    [15:42:01] 400 POST /v1/detect  req=abc123  tenant=tn_9f3c…  (12ms)
      ✗ body.learning_events.0.source: Input should be 'DIAGNOSTIC' or 'ASSIGNMENT'  (입력='MANUAL')

⚠ 5xx는 반대다. 미분류 예외는 `ExceptionMiddleware`를 **뚫고** 올라오므로 미들웨어가
직접 잡아 요약을 찍고 **그대로 다시 올린다** — 트레이스백은 지우지 않는다(§2-A2).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from datetime import datetime
from inspect import isawaitable
from typing import Any, Final, cast

from fastapi import FastAPI, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from ai.runtime.console import (
    clip_value,
    emit,
    get_console_settings,
    group_note,
    guarded,
    origin_frame,
    root_cause,
)
from ai.runtime.errors import DomainException

#: 주입용 기본 시계 — 이 한 곳만 벽시계를 읽는다(03 §3, `run_store.py` 선례).
Clock = Callable[[], datetime]

#: Starlette 예외 처리기 시그니처 — 동기·비동기 둘 다 허용된다.
_Handler = Callable[[Request, Exception], Response | Awaitable[Response]]


def system_local_now() -> datetime:
    """프로덕션 기본 시계 — **로컬 시각**(aware). 테스트는 고정 시계를 주입한다.

    🔴 원장(`AI_RUN`·`LLM_CALL`)의 UTC와 **일부러 다르다.** 이 값은 저장되지 않고
    사람이 화면에서 백엔드 팀과 시각을 맞추는 데만 쓴다 — 리허설 중에 UTC를 암산하게
    만들면 관측 도구로서 실격이다. 저장되는 시각은 무엇도 이 함수를 쓰지 않는다.

    ⚠ 변환은 여기서 **끝난다.** 포맷 지점에서 다시 `astimezone()`을 부르지 않는다 —
    주입된 시계의 시각이 그대로 화면에 나와야 테스트가 구현을 되풀이하지 않는다.
    """
    return datetime.now().astimezone()


#: 인계 슬롯의 scope 키 — 예외 처리기가 넣고 미들웨어가 꺼낸다.
_DETAIL_KEY: Final = "_ai_console_detail"


def stash_detail_lines(request: Request, lines: Sequence[str]) -> None:
    """이번 요청의 사유 줄을 담는다(미들웨어가 헤더와 함께 낸다).

    🔴 **ContextVar가 아니라 `scope`인 이유 — 실측(8/13)으로 갈렸다.**

    `BaseHTTPMiddleware`(=`@app.middleware("http")`)는 하위 앱을
    **`task_group.start_soon()`으로 띄운다.** `asyncio` 태스크는 생성 시 컨텍스트를
    **복사**하므로 자식(예외 처리기)에서 한 `ContextVar.set()`은 **부모(미들웨어)에
    보이지 않는다** — 사유 줄이 조용히 사라지고 헤더 한 줄만 나온다.

    ⚠ `llm_payload.py`가 ContextVar를 쓰는 것과 모순이 아니다. 저쪽은 넣는 쪽과 꺼내는
    쪽이 **같은 태스크**(래퍼 → 그 직후 수집기)라 격리가 목적이고, 이쪽은 **태스크 경계를
    넘겨야** 하는 정반대 요구다.

    `scope`는 요청 하나당 하나이고 미들웨어·처리기가 **같은 dict 객체**를 보므로
    태스크 경계를 넘으면서도 요청 간 격리가 구조적으로 보장된다.
    """
    request.scope[_DETAIL_KEY] = tuple(lines)


def take_detail_lines(request: Request) -> tuple[str, ...]:
    """슬롯을 **비우고** 꺼낸다 — 같은 scope를 두 번 읽어도 중복 출력되지 않게."""
    lines: tuple[str, ...] = request.scope.pop(_DETAIL_KEY, ())
    return lines


def field_detail_lines(errors: Sequence[Mapping[str, Any]], *, max_fields: int) -> list[str]:
    """pydantic 오류 목록 → 사람이 읽는 사유 줄.

    🔴 **`(입력=…)`이 이 함수의 존재 이유다.** 규칙만 보여주면 *"패턴이 뭐였더라"* 가
    아니라 *"백엔드가 대체 뭘 보냈나"* 를 다시 물어봐야 한다 — 8/13 하루가 그렇게 갔다.
    규칙(msg)과 실제 값(input)이 **같은 줄에** 있어야 그 자리에서 끝난다.

    ⚠ 응답 본문에는 이 값이 **없다.** `_format_validation_error`가 `field`·`message`만
    남기고 `input`을 버리기 때문이다(라우터 소관이라 건드리지 않았다). 콘솔이 그 결손을
    메우는 것이 A-1의 전부다.
    """
    lines: list[str] = []
    for err in errors[:max_fields]:
        loc = ".".join(str(part) for part in err.get("loc", ()))
        msg = err.get("msg", "?")
        # `input` 키는 pydantic v2가 넣어 주지만 없는 오류 종류도 있다 — 없으면 그렇게 적는다.
        shown = clip_value(err["input"]) if "input" in err else "⟨입력 미기록⟩"
        lines.append(f"  ✗ {loc}: {msg}  (입력={shown})")
    if len(errors) > max_fields:
        lines.append(f"  … 외 {len(errors) - max_fields}건 (CONSOLE_MAX_FIELDS)")
    return lines


def domain_detail_lines(exc: DomainException, *, max_fields: int) -> list[str]:
    """`DomainException` → 사유 줄. 🔴 **이 저장소의 4xx는 거의 전부 이 경로다.**

    라우터들이 인자로 `Request`를 받고 `Model.model_validate()`를 직접 부르기 때문에
    (`detect.py :: post_detect` docstring — "계약에 없는 422가 생기고 현행 400
    `INVALID_SCHEMA`가 사라진다") FastAPI의 `RequestValidationError`는 **발생하지 않는다.**
    스키마 위반은 `SnapshotInvalid`(400)으로 올라온다.

    🔴 **입력값은 `__cause__`에 살아 있다.** 라우터가 `raise SnapshotInvalid(...) from exc`로
    올리므로 원본 pydantic `ValidationError`가 매달려 있고, 거기에는 응답에서 버려진
    `input`이 그대로 있다. 그 값을 여기서 되살린다.

    ⚠ `detail` 자체는 **노출 정책을 그대로 따른다**(`detail_is_exposed()` · 04 §2.3).
    5xx의 detail은 마스킹 실패 조각일 수 있어 콘솔에도 싣지 않는다 — app.py의
    `logger.warning`이 이미 그 경로를 맡고 있다.
    """
    lines = [f"  ✗ {exc.code}: {exc.message}"]
    cause = exc.__cause__
    if isinstance(cause, ValidationError):
        lines.extend(field_detail_lines(cause.errors(), max_fields=max_fields))
    elif exc.detail is not None and exc.detail_is_exposed():
        lines.append(f"    detail={clip_value(exc.detail)}")
    elif exc.detail is not None:
        lines.append("    detail=⟪미노출 — 마스킹 정책, logger.warning 참조⟫")
    return lines


def _request_line(
    *, stamp: str, status: int, request: Request, elapsed_ms: int
) -> str:
    """요청 한 줄 — status·메서드·경로·상관키·경과시간.

    🔴 헤더는 이 셋만 읽는다. Authorization·API 키는 **구조적으로** 찍힐 수 없다.
    """
    parts = [f"[{stamp}] {status} {request.method} {request.url.path}"]
    request_id = request.headers.get("X-Request-Id")
    if request_id:
        parts.append(f"req={request_id}")
    tenant = request.headers.get("X-Tenant-Id")
    if tenant:
        parts.append(f"tenant={tenant}")  # 별칭(tn_…) — 실명이 아니다(불변식 3)
    parts.append(f"({elapsed_ms}ms)")
    return "  ".join(parts)


def _failure_summary(exc: BaseException) -> Iterator[str]:
    """5xx 요약 3줄 — 트레이스백을 **대체하지 않고 앞에 붙인다**.

    8/13 실측: 200줄 트레이스백에서 쓸모 있던 것은 마지막 두 줄이었다. 그 두 줄을
    맨 앞으로 끌어오되 원본은 그대로 둔다 — 요약만 남기면 **다음에 새로운 오류가 났을 때**
    정보가 모자란다.
    """
    cause = root_cause(exc)
    yield f"   {type(cause).__name__}: {cause}{group_note(exc)}"
    yield f"   at {origin_frame(cause)}"
    yield "   (전체 트레이스백은 아래)"


def install_console_handlers(app: FastAPI, *, clock: Clock = system_local_now) -> None:
    """콘솔 관측을 앱에 꽂는다 — **app.py에서 부르는 유일한 진입점**.

    🔴 플래그가 전부 꺼져 있으면(기본값) 배선은 되지만 **아무것도 출력되지 않는다.**
    켜고 끄는 판정을 기동 시점이 아니라 요청 시점에 하는 이유는, 개발 중에 `.env`를
    고치고 `--reload`로만 반영하려 할 때 기동 캐시가 헷갈리게 만들기 때문이다.
    """

    @guarded
    def _emit_outcome(
        *, status: int, request: Request, elapsed_ms: int, exc: BaseException | None
    ) -> None:
        """관측 출력 전부 — 🔴 **`@guarded`라 예외를 올리지 않는다.**

        설정 읽기·시각 포맷·줄 조립·출력이 **모두** 이 안에 있다. `emit`만 감싸면
        부족하다 — 실제로는 렌더링에서 더 자주 터지고, 그게 요청을 죽이면 관측이
        서비스를 죽인 것이다(설계 원칙 §4).
        """
        settings = get_console_settings()
        failed = status >= 400
        if failed and not settings.console_error_log:
            return
        if not failed and not settings.console_ok_log:
            return
        stamp = clock().strftime("%H:%M:%S")
        head = _request_line(
            stamp=stamp, status=status, request=request, elapsed_ms=elapsed_ms
        )
        tail = list(_failure_summary(exc)) if exc is not None else list(take_detail_lines(request))
        emit([head, *tail])

    @app.middleware("http")
    async def _console_observer(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            # 🔴 미분류 예외 — 요약만 찍고 **그대로 올린다**. 여기서 삼키면 500 응답 계약과
            #    트레이스백이 둘 다 사라진다(app.py의 `_unhandled`가 응답을 만들고,
            #    Starlette의 `ServerErrorMiddleware`가 다시 올려 uvicorn이 전문을 찍는다).
            _emit_outcome(
                status=500,
                request=request,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                exc=exc,
            )
            raise
        _emit_outcome(
            status=response.status_code,
            request=request,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            exc=None,
        )
        return response

    # 🔴 **덮어쓰지 않고 감싼다.** FastAPI는 `RequestValidationError` 처리기를, app.py는
    #    `DomainException` 처리기를 이미 달아 뒀다. "이미 있으면 건너뛴다"로 짜면 콘솔
    #    사유가 **영영 안 붙고**(8/13 실측 — 헤더 한 줄만 나왔다), 덮어쓰면 에러 봉투
    #    계약(04 §2.3)이 조용히 바뀐다. 사유만 남기고 **원래 처리기에 그대로 위임**한다.
    # cast 이유: 프레임워크 처리기는 인자를 `RequestValidationError`로 좁혀 선언하는데
    # 우리는 그 타입일 때만 넘긴다(`_chain`이 해당 예외로만 불린다).
    _chain(app, RequestValidationError, cast(_Handler, request_validation_exception_handler))
    _chain(app, DomainException, None)


def _chain(app: FastAPI, exc_type: type[Exception], fallback: _Handler | None) -> None:
    """`exc_type` 처리기를 콘솔 싱크로 감싼다 — 응답 동작은 **바이트 동일**하게 유지.

    등록된 처리기가 없고 `fallback`도 없으면 **아무것도 하지 않는다** — 없던 처리기를
    새로 만들면 그 예외의 응답이 바뀐다(콘솔이 계약을 바꾸면 안 된다).
    """
    inner = app.exception_handlers.get(exc_type, fallback)
    if inner is None:
        return

    async def _handler(request: Request, exc: Exception) -> Response:
        _stash_reason(request, exc)
        # ⚠ Starlette는 **동기 처리기도** 허용한다 — 코루틴이라고 가정하면 남이 동기
        #   처리기를 달아 둔 앱에서 터진다. 둘 다 받는다.
        outcome = inner(request, exc)
        return await outcome if isawaitable(outcome) else outcome

    app.add_exception_handler(exc_type, _handler)


@guarded
def _stash_reason(request: Request, exc: Exception) -> None:
    """사유 줄을 만들어 슬롯에 담는다 — 🔴 실패해도 응답 경로는 그대로 흐른다."""
    settings = get_console_settings()
    if not settings.console_error_log:
        return
    if isinstance(exc, RequestValidationError):
        lines = field_detail_lines(exc.errors(), max_fields=settings.console_max_fields)
    elif isinstance(exc, DomainException):
        lines = domain_detail_lines(exc, max_fields=settings.console_max_fields)
    else:
        return
    stash_detail_lines(request, lines)


__all__ = [
    "Clock",
    "install_console_handlers",
    "stash_detail_lines",
    "system_local_now",
    "take_detail_lines",
    "domain_detail_lines",
    "field_detail_lines",
]
