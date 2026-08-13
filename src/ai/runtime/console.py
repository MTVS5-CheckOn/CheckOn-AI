"""개발자 콘솔 관측 — 4xx 거부 사유 · 5xx 요약 · LLM 문장 (PR-ψ).

🔴 **운영 상시 활성 금지 — 개발자 콘솔 전용.** 세 플래그는 전부 기본 꺼짐이고,
켜야만 무엇이든 출력한다. 기본 동작(응답·원장·로그)은 플래그와 무관하게 동일하다.

━━ 왜 필요한가 (2026-08-13 실측) ━━

백엔드 연동 리허설에서 콘솔에 남는 것은 이 한 줄뿐이었다::

    INFO: 192.168.0.10 - "POST /v1/detect HTTP/1.1" 400 Bad Request

**어느 필드가 왜 거부됐는지가 없다.** `source="MANUAL"` · `cl_unassigned` · enum 4종을
찾는 데 하루가 들었고 원인은 전부 이것이다. 반대로 500은 트레이스백이 200줄 찍혔다 —
**안 보여서가 아니라 너무 많아서** 문제였다. 그래서 이 모듈의 목표는 두 개다:
4xx는 **사유를 만들어 보여주고**, 5xx는 **요약 3줄을 앞에 덧붙인다**(트레이스백은 그대로 둔다).

━━ 🔴 개인정보 ━━

LLM 프롬프트에는 학생 학습 사실이 들어간다. 별칭(``st_…``)이라 직접 식별은 안 되지만
내용은 실데이터다. 그래서:

* **기본 꺼짐** — ``CONSOLE_LLM_LOG``가 명시적으로 켜져야만 동작한다
* **stdout 전용** — 파일·원장·외부 전송 금지. 핸들러는 ``StreamHandler(sys.stdout)`` 하나다
* **LangSmith와 무관** — 이 기능을 쓰려고 ``LANGSMITH_TRACING``을 켜지 마라(외부 전송이다)
* **마스킹 통과본만** — 이 모듈은 원문을 볼 수 없다. 호출부가 이미 redaction을 통과한
  문자열만 넘긴다(`db/repositories/llm_payload.py`의 `CapturedBody` 규약)
* **문자수 상한**(기본 2048) — 초과는 ``… (N자 생략)``
* **API 키·Authorization 헤더는 어떤 경로로도 출력하지 않는다** — 이 모듈은 요청 헤더를
  받지 않는다(구조적으로 찍을 수 없다). 받는 것은 status·path·request_id·tenant뿐이다
* ⚠ 화면 공유·녹화 중에는 켜지 마라

━━ 🔴 관측이 서비스를 죽이면 안 된다 ━━

출력 경로 전체가 `emit`의 단일 초크포인트를 지나고, 거기서 모든 예외를 삼킨다.
**조용히 삼키지는 않는다** — 실패 횟수를 세고 마지막 수단으로 stderr에 알린다
(`db/repositories/llm_payload.py`의 fail-open + 카운터와 같은 철학).

━━ print 를 쓰지 않는 이유 (ⓕ) ━━

이 저장소는 전 모듈이 `logging.getLogger(__name__)`을 쓴다. 그런데 콘솔 출력은
``uvicorn --log-level warning``에서도 보여야 하는데 INFO로 올리면 안 보이고, WARNING으로
올리면 정상 LLM 호출이 경고로 남는다. ⇒ **전용 로거**(``ai.console``)에 stdout 핸들러를
직접 달고 ``propagate=False``로 끊는다. 로거 체계를 따르면서 루트 레벨과 독립한다.
"""

from __future__ import annotations

import contextlib
import logging
import sys
from collections.abc import Callable, Iterable
from functools import lru_cache, wraps
from typing import Final

from pydantic_settings import BaseSettings, SettingsConfigDict

from ai.runtime.env_files import ENV_FILES

#: 전용 로거 — 루트/uvicorn 레벨과 독립한다(모듈 docstring 마지막 절).
LOGGER_NAME: Final = "ai.console"

#: 우리 핸들러의 이름표. 🔴 **"핸들러가 하나라도 있으면 건너뛴다"로 짜면 안 된다** —
#: 다른 누군가(테스트 러너·APM·로깅 설정)가 이 로거에 핸들러를 먼저 달아두면
#: stdout 핸들러가 **영영 안 붙고 콘솔이 조용히 죽는다**(8/13 실측: pytest가 `ai.console`에
#: 자기 핸들러를 달아 이 함수가 그대로 통과했다). 이름표로 **우리 것만** 찾는다.
_HANDLER_NAME: Final = "ai.console.stdout"

#: 예외 사슬을 거슬러 오를 때의 깊이 상한 — 무한 루프 금지(CLAUDE.md 불변식 6).
_MAX_CAUSE_DEPTH: Final = 50

#: 원장에 값이 없는 필드의 표시. 🔴 **값처럼 읽히면 안 된다** — "정보가 없는 것"과
#: "정보가 0인 것"이 화면에서 구분되지 않으면 다음 사람은 그 값이 애초에 존재하지
#: 않는다고 믿는다. 2026-08-13 반나절이 정확히 그 착시로 갔다.
MISSING_54: Final = "⟨미수집 · 99 #54⟩"


class ConsoleSettings(BaseSettings):
    """콘솔 관측 플래그 — `.env` 또는 환경 변수로 주입한다.

    🔴 **셸 전용이 아니다.** `.env`에 적어두면 어떤 기동 방식으로도 산다 — 셸에서만
    export하면 IDE·서비스 기동에서 조용히 꺼진다(8/13 두 번 당했다).

    ⚠ **이 문장은 2026-08-14까지 거짓이었다** — `env_file=".env"`는 상대경로라 실제로는
    *"**작업 디렉터리**에서 읽는다"* 였고, 저장소 루트가 아닌 데서 기동하면 **못 찾고
    조용히 기본값으로 떨어졌다.** ⇒ `ENV_FILES`(저장소 루트 앵커 + CWD)로 바꿔 참으로
    만들었다. 근거·실측은 `runtime/env_files.py` (99 #73).

    🔴 세 플래그 전부 **기본 False**다. 아무것도 안 하면 아무것도 출력되지 않는다.
    """

    model_config = SettingsConfigDict(env_file=ENV_FILES, extra="ignore")

    console_error_log: bool = False
    """A — 4xx 거부 사유 + 5xx 요약."""

    console_llm_log: bool = False
    """B — LLM 프롬프트/응답. 🔴 학습 실데이터가 흐른다(모듈 docstring 개인정보 절)."""

    console_ok_log: bool = False
    """2xx 요약 한 줄.

    ⚠ 완전 침묵은 그것대로 위험하다 — 서버가 도는지 죽었는지 모른다. 그래서 켤 수 있게
    두되 기본은 꺼짐이다(정상 요청이 화면을 채우면 A의 오류가 다시 묻힌다).
    """

    console_max_chars: int = 2048
    """프롬프트·응답 1건당 출력 문자 상한 — 초과는 `… (N자 생략)`.

    값이지 도메인 규칙이 아니라서 Settings에 둔다(03 §1). 원장의
    `llm_payload_max_chars`(32768)와 **다른 축이다** — 저쪽은 저장 가드고 이쪽은 가독성이다.
    """

    console_max_fields: int = 10
    """4xx 한 요청에서 출력할 필드 오류 개수 상한.

    pydantic은 중첩 리스트 하나가 틀리면 수백 건을 낼 수 있다 — 화면을 덮으면 A가 무의미하다.
    """


@lru_cache(maxsize=1)
def get_console_settings() -> ConsoleSettings:
    """콘솔 설정 — 프로세스 1회 로드(db/settings.py 선례)."""
    return ConsoleSettings()


def reset_console_settings() -> None:
    """테스트 격리용 — 설정·로거 캐시를 비운다.

    핸들러까지 떼는 이유: `StreamHandler`는 **생성 시점의 `sys.stdout`을 붙든다.** 캐시만
    비우면 `if not logger.handlers` 때문에 낡은 스트림이 그대로 남아, 출력 캡처가
    **조용히 빈 결과**를 내놓는다(테스트가 통과한 것처럼 보인다).
    """
    get_console_settings.cache_clear()
    _console_logger.cache_clear()
    logger = logging.getLogger(LOGGER_NAME)
    for handler in console_handlers():
        logger.removeHandler(handler)


@lru_cache(maxsize=1)
def _console_logger() -> logging.Logger:
    """전용 stdout 로거.

    `propagate=False`가 핵심이다 — 루트로 새면 ① uvicorn 포매터가 박스 그림을 깨고
    ② `--log-level warning`에서 INFO가 잘린다. 여기서 레벨을 직접 쥔다.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not any(handler.name == _HANDLER_NAME for handler in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)  # 🔴 stdout 전용 — 파일 싱크 금지
        handler.set_name(_HANDLER_NAME)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    return logger


def console_handlers() -> list[logging.Handler]:
    """이 모듈이 단 핸들러만 — 검사·진단용(남의 핸들러는 세지 않는다)."""
    return [
        handler
        for handler in logging.getLogger(LOGGER_NAME).handlers
        if handler.name == _HANDLER_NAME
    ]


#: 출력 실패 누적 — 조용한 누락 금지(CLAUDE.md §6 `except: pass` 금지의 취지).
_emit_failures = 0


def emit_failure_count() -> int:
    """출력 경로가 삼킨 예외 횟수 — 테스트·진단용."""
    return _emit_failures


def reset_emit_failures() -> None:
    """테스트 격리용 — 실패 카운터를 0으로."""
    global _emit_failures
    _emit_failures = 0


def _record_failure() -> None:
    """삼킨 예외를 **센다** — 조용히 사라지게 두지 않는다."""
    global _emit_failures
    _emit_failures += 1
    # 최후 수단. 이것마저 실패하면 할 수 있는 것이 없다 — 요청은 살려 보낸다.
    with contextlib.suppress(Exception):
        print(
            f"[ai.console] 콘솔 출력 실패 (누적 {_emit_failures}회)",
            file=sys.__stderr__,
            flush=True,
        )


def guarded[**P](fn: Callable[P, None]) -> Callable[P, None]:
    """🔴 **관측이 서비스를 죽이면 안 된다** — 이 데코레이터가 그 방어선이다.

    감싼 함수는 어떤 예외도 호출부로 올리지 않는다. `emit`만 막으면 부족하다 —
    **렌더링에서 터지는 경우**(예상 못한 타입, 인코딩, 상한 계산)가 실제로 더 흔하다.
    그래서 출력 함수는 렌더링까지 통째로 이 데코레이터 안에 둔다.

    ⚠ 중첩되어도 무해하다(안쪽이 이미 삼키면 바깥은 그냥 통과한다).
    """

    @wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 — 🔴 관측이 서비스를 죽이면 안 된다(설계 원칙 §4)
            _record_failure()

    return wrapper


@guarded
def emit(lines: Iterable[str]) -> None:
    """콘솔에 여러 줄을 내보낸다 — 🔴 **어떤 경우에도 예외를 올리지 않는다**."""
    logger = _console_logger()
    for line in lines:
        logger.info(line)


def clip_text(text: str, limit: int | None = None) -> str:
    """본문 절단 — 초과분은 `… (N자 생략)`으로 **사실을 남기고** 자른다.

    조용히 자르면 잘린 줄 모른다. 생략된 글자 수를 적는 이유다.
    """
    cap = get_console_settings().console_max_chars if limit is None else limit
    if len(text) <= cap:
        return text
    return f"{text[:cap]}… ({len(text) - cap}자 생략)"


def clip_value(value: object, limit: int | None = None) -> str:
    """거부된 **입력값**의 표시형 — `repr`로 찍는다.

    🔴 `repr`이어야 하는 이유: `'MANUAL'`과 `MANUAL`, `''`와 공백, `'cl_unassigned '`의
    뒤 공백이 구분되어야 한다. 오늘 하루를 만든 값들이 정확히 그런 것들이었다.
    """
    return clip_text(repr(value), limit)


def _next_in_chain(exc: BaseException) -> BaseException | None:
    """사슬의 다음 칸 — 🔴 **`__suppress_context__`를 존중한다.**

    `raise X from None`(또는 `from Y`)은 앞선 예외를 **의도적으로 끊은 것**이고 CPython의
    트레이스백도 그때 `__context__`를 찍지 않는다. 여기서 그걸 무시하고 `__context__`를
    따라가면 **프레임워크 내부가 근본 원인으로 둔갑한다.**

    🔴 실측(8/13) — 이 함수가 없을 때 500 요약이 이렇게 나왔다::

        EndOfStream:
        at .venv/…/anyio/streams/memory.py:111 receive_nowait

    진짜 원인은 `ConnectionRefusedError`였다. Starlette의 `BaseHTTPMiddleware`가
    `except anyio.EndOfStream:` 안에서 `raise app_exc from None`을 하기 때문에
    `__context__`에 무관한 스트림 예외가 매달린다. **요약이 틀리면 없느니만 못하다** —
    리허설 중에 엉뚱한 파일을 열게 만든다.
    """
    # 🔴 **ExceptionGroup을 뚫는다.** `BaseHTTPMiddleware`는 하위 앱을 anyio `TaskGroup`에
    #    태우므로 미분류 예외가 `ExceptionGroup`에 싸여 올라온다. 안 뚫으면 요약이 이렇게
    #    나온다(8/13 실측)::
    #
    #        ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)
    #        at .venv/…/anyio/_backends/_asyncio.py:815 __aexit__
    #
    #    진짜 원인은 `ConnectionRefusedError`였다. 그룹 껍데기는 **정보가 0인 줄**이다.
    if isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        return exc.exceptions[0]
    nxt = exc.__cause__
    if nxt is None:
        nxt = None if exc.__suppress_context__ else exc.__context__
    # 🔴 **그룹 쪽으로는 거슬러 올라가지 않는다.** 8/13 실측: 미들웨어가 받은 예외는
    #    올바른 `ConnectionRefusedError`인데 그 `__context__`가 **자기를 담고 있는**
    #    `ExceptionGroup`을 가리켰다(TaskGroup 안에서 재-raise되기 때문). 따라가면
    #    이미 손에 쥔 진짜 원인을 버리고 껍데기로 **후퇴한다** — 사실상 순환이다.
    if isinstance(nxt, BaseExceptionGroup):
        return None
    return nxt


def group_note(exc: BaseException) -> str:
    """그룹에 형제 예외가 더 있으면 그 사실을 적는다 — 요약이 **하나만** 보여주기 때문이다.

    ⚠ 형제를 조용히 버리면 "원인이 하나였다"로 읽힌다. 개수를 적고 전문을 가리킨다.
    """
    siblings = 0
    current: BaseException | None = exc
    for _ in range(_MAX_CAUSE_DEPTH):
        if current is None:
            break
        if isinstance(current, BaseExceptionGroup):
            siblings += len(current.exceptions) - 1
        current = _next_in_chain(current)
    return f" (형제 예외 {siblings}건 더 — 전문 참조)" if siblings > 0 else ""


def root_cause(exc: BaseException) -> BaseException:
    """예외 사슬의 뿌리 — 순환·과대 깊이 방어.

    200줄 트레이스백에서 쓸모 있던 것은 마지막 두 줄이었다. 그 두 줄을 앞으로 끌어온다.
    """
    seen: set[int] = {id(exc)}
    current = exc
    for _ in range(_MAX_CAUSE_DEPTH):
        nxt = _next_in_chain(current)
        if nxt is None or id(nxt) in seen:
            break
        seen.add(id(nxt))
        current = nxt
    return current


def origin_frame(exc: BaseException) -> str:
    """예외가 난 **마지막 프레임** — `경로:줄 함수` 한 줄.

    트레이스백을 대체하지 않는다. 어디를 볼지 먼저 알려줄 뿐이다.
    """
    tb = exc.__traceback__
    if tb is None:
        return "(프레임 없음)"
    last = tb
    for _ in range(_MAX_CAUSE_DEPTH):
        if last.tb_next is None:
            break
        last = last.tb_next
    frame = last.tb_frame
    return f"{frame.f_code.co_filename}:{last.tb_lineno} {frame.f_code.co_name}"


def render_llm_call(
    *,
    role: str,
    model: str,
    prompt_masked: str,
    response_masked: str,
    tokens_in: int | None,
    tokens_out: int | None,
    latency_ms: int | None,
    outcome: str,
    seed: int | None = None,
    oversized: bool = False,
) -> list[str]:
    """LLM 호출 1건의 콘솔 박스.

    🔴 **인자는 전부 마스킹 통과본이다.** 이 함수는 원문을 볼 수 없다 — 호출부
    (`db/repositories/llm_payload.py`)가 `CapturedBody` 규약대로 가린 뒤 넘긴다.

    🔴 **`finish_reason`·`reasoning_tokens`는 자리표시자로 찍는다.** 두 값은 파이프라인
    어디에도 없다 — 어댑터가 `finish_reason`을 아예 읽지 않고(`LLMResult`에 필드가 없다),
    추론 토큰은 `TokenUsage`에 없다(99 #54). **조용히 빼면 다음 사람은 그 값이 애초에
    존재하지 않는다고 믿는다** — 8/13 반나절이 정확히 그 착시로 갔다. 그래서 값이 아니라
    `⟪미수집⟫`으로 **매 호출마다 화면에 띄워** 안건이 잊히지 않게 한다.
    ⚠ #54가 닫히면 이 두 줄은 실제 값으로 바뀐다(99 #54 완료 조건에 명시).
    """
    head = f"┌─ LLM {role} · {model}"
    if seed is not None:
        head += f" · seed={seed}"
    lines = [head]
    if oversized:
        # 본문이 원장 상한을 넘어 마스킹조차 하지 않았다 — 가리지 않은 문자열은 찍지 않는다.
        lines.append("│ ⟪본문 상한 초과 — 원장·콘솔 모두 미보관⟫")
    else:
        lines.append(f"│ PROMPT ({_tok(tokens_in)})")
        lines.extend(_body_lines(prompt_masked))
        lines.append(f"│ RESPONSE ({_tok(tokens_out)} · {_secs(latency_ms)})")
        lines.extend(_body_lines(response_masked) if response_masked else ["│   ⟪응답 없음⟫"])
    lines.append(f"│ finish_reason    = {MISSING_54}")
    lines.append(f"│ reasoning_tokens = {MISSING_54}")
    lines.append(f"└─ outcome={outcome}")
    return lines


def _tok(count: int | None) -> str:
    """토큰 수 — 없으면 `?`다. 🔴 **0으로 적지 않는다**(99 #54: 절단 호출의 토큰이 0으로
    남아 가장 비싼 호출이 원가 집계에서 사라졌다. 미측정과 0은 다른 사실이다)."""
    return "? tok" if count is None else f"{count} tok"


def _secs(latency_ms: int | None) -> str:
    return "?s" if latency_ms is None else f"{latency_ms / 1000:.1f}s"


def _body_lines(text: str) -> list[str]:
    """본문을 `│   ` 들여쓰기로 감싼다 — 상한 초과분은 `… (N자 생략)`."""
    return [f"│   {line}" for line in clip_text(text).splitlines() or [""]]


@guarded
def emit_llm_call(
    *,
    role: str,
    model: str,
    prompt_masked: str,
    response_masked: str,
    tokens_in: int | None,
    tokens_out: int | None,
    latency_ms: int | None,
    outcome: str,
    seed: int | None = None,
    oversized: bool = False,
) -> None:
    """LLM 박스를 콘솔에 낸다 — 🔴 **기본 꺼짐**(`CONSOLE_LLM_LOG`).

    렌더링까지 `@guarded` 안에 있다 — 여기서 무엇이 터져도 LLM 호출은 살아서 돌아간다.
    """
    if not get_console_settings().console_llm_log:
        return
    emit(
        render_llm_call(
            role=role,
            model=model,
            prompt_masked=prompt_masked,
            response_masked=response_masked,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            outcome=outcome,
            seed=seed,
            oversized=oversized,
        )
    )


__all__ = [
    "LOGGER_NAME",
    "MISSING_54",
    "ConsoleSettings",
    "console_handlers",
    "clip_text",
    "clip_value",
    "emit",
    "emit_failure_count",
    "group_note",
    "emit_llm_call",
    "get_console_settings",
    "guarded",
    "origin_frame",
    "render_llm_call",
    "reset_console_settings",
    "reset_emit_failures",
    "root_cause",
]
