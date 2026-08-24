"""🔴 **BE 인 척** `/v1/labels/suggest` 를 돌린다 — 연동 리허설(№81 · 실 LLM 0회).

`evaluation/backend_sim.py` 가 `/v1/detect` 축에서 하는 일과 같은 결이지만 **복제가 아니다** —
라벨은 **1콜 · 동기 200** 이라 훨씬 단순하고, 대신 이 도구만 하는 일이 둘 있다:

  ① 🔴 **확정까지 끝까지 간다** — 제안을 받아 `POST /v1/confirmations` 로 확정을 시도한다.
     04 :31 이 «`label` | `LABEL_SUGGESTION.id` | ❌ **400** `kind_not_implemented`» 라
     적었으니 **400 이 나야 맞다.** 🔴 그 400 을 **실제로 받아 보는 것**이 목적이다
     (문서가 그렇다고 적혀 있는 것과 그렇게 도는 것은 다르다 — 로그 195).
  ② 🔴 **BE 클라이언트 타임아웃을 세 값으로 걸어** 무엇이 오나 본다.
     `call_timeouts.yaml` 의 «AI 상한이 BE 보다 짧아야 한다» 는 근거가 **BE 쪽에서**
     어떻게 보이는지는 아직 아무도 안 봤다.

🔴 **실 LLM 0회** — `LLM_PROVIDER=fake` 로 서버를 띄우고, 느린 응답이 필요한 자리는
**대역 provider 에 지연을 주입**한다. 🔴 **로컬만** — 배포 주소는 거부한다(99 #111·#112).
⚠ 🔴 본문·인용문은 안 싣는다(불변식 3 · 99 #80) — 수와 키 이름까지다.

    uv run --frozen python -m ai.evaluation.labels_be_sim
"""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
import time
import uuid
from collections.abc import Sequence
from typing import Any, Final

import httpx
import uvicorn

from ai.api.app import create_app
from ai.api.routers import labels as labels_router
from ai.composition.labels.counters import LabelLayerCounts
from ai.composition.labels.provider import FakeLabelSuggestProvider
from ai.contracts.execution import ExecutionContext
from ai.contracts.labels import HistoryItem, SuggestedLabel

#: 🔴 코퍼스에 이미 있는 가명만 — 새 실명을 만들지 않는다.
_HISTORY: Final = [
    {"record_id": "cm_01", "text": "지난주 과제를 모두 제출했습니다"},
    {"record_id": "cm_02", "text": "상담 일정을 다시 잡고 싶다고 했습니다"},
    {"record_id": "cm_03", "text": "교재 진도를 확인했습니다"},
    {"record_id": "cm_04", "text": "주말 보충 참여 의사를 밝혔습니다"},
    {"record_id": "cm_05", "text": "연락이 사흘째 없습니다"},
]
_TENANT: Final = "t_be_sim"


def _body() -> dict[str, Any]:
    return {
        "guardian_ref": "gd_be_sim",
        "history": [
            {**item, "direction": "inbound", "at": "2026-08-20T09:00:00+09:00"}
            for item in _HISTORY
        ],
    }


def _headers() -> dict[str, str]:
    return {"X-Tenant-Id": _TENANT, "X-Request-Id": str(uuid.uuid4())}


class _SlowLabelProvider:
    """🔴 지연만 주는 대역 — **AI 가 자기 상한을 넘기게** 만든다(실 LLM 0회)."""

    def __init__(self, delay_s: float) -> None:
        self._delay = delay_s
        self._inner = FakeLabelSuggestProvider()
        self.finished = 0
        """🔴 **BE 가 끊은 뒤에도 우리가 끝까지 돌았나** — ⓐ 시나리오의 증거다."""

    async def suggest(
        self,
        *,
        guardian_ref: str,
        history: Sequence[HistoryItem],
        context: ExecutionContext,
        counts: LabelLayerCounts | None = None,
    ) -> tuple[SuggestedLabel, ...]:
        await asyncio.sleep(self._delay)
        result = await self._inner.suggest(
            guardian_ref=guardian_ref, history=history, context=context, counts=counts
        )
        #: 🔴 클라이언트가 이미 끊었어도 **이 줄은 실행된다** — 그게 «원가만 나간다» 다.
        self.finished += 1
        return result


class _Server:
    """로컬 uvicorn 을 스레드에서 띄운다 — 🔴 **진짜 소켓**이라야 타임아웃이 재진다."""

    def __init__(self, port: int) -> None:
        config = uvicorn.Config(
            create_app(), host="127.0.0.1", port=port, log_level="error"
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self.base = f"http://127.0.0.1:{port}"

    def __enter__(self) -> _Server:
        self._thread.start()
        for _ in range(200):
            if self._server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("서버가 안 떴다")

    def __exit__(self, *_: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


def _keys(value: object, prefix: str = "") -> list[str]:
    if isinstance(value, dict):
        out: list[str] = []
        for key, inner in value.items():
            out.append(prefix + key)
            out.extend(_keys(inner, prefix + key + "."))
        return out
    return []


def _step_shape(base: str) -> dict[str, Any]:
    """🔴 ① 제안을 받아 **키 모양**을 적고, 그대로 확정까지 시도한다."""
    print("\n━━ ① 제안 → 확정 (BE 흐름 끝까지) ━━")
    with httpx.Client(timeout=30.0) as client:
        suggest = client.post(
            f"{base}/v1/labels/suggest", json=_body(), headers=_headers()
        )
        print(f"  POST /v1/labels/suggest → {suggest.status_code}")
        suggestions = suggest.json()["data"]["suggestions"]
        print(f"  제안 {len(suggestions)}건")
        if not suggestions:
            print("  🔴 제안 0건 — 확정을 시도할 것이 없다")
            return {"suggestions": 0}
        first = suggestions[0]
        print(f"  🔴 제안 객체 키: {sorted(first)}")
        print(f"  🔴 중첩 경로   : {sorted(_keys(first))}")

        #: 🔴 **BE 가 다음에 할 일** — 강사가 수락하면 확정을 보낸다(04 §3.3).
        confirm = client.post(
            f"{base}/v1/confirmations",
            #: 🔴 **계약이 요구하는 그대로**(`ConfirmationRequest`) — 첫 시도에 제가
            #: `target_ref`·`accepted` 로 보냈다가 **400 `INVALID_SCHEMA`** 를 받았다.
            #: ⚠ 그건 «미구현» 이 아니라 «바디가 틀렸다» 라 **다른 사건**이다 —
            #: 🔴 BE 도 같은 실수를 할 수 있으므로 여기 적어 둔다(04 §3.3 이 정본).
            json={
                "kind": "label",
                "suggestion_id": str(first["suggestion_id"]),
                "action": "confirmed",
            },
            headers=_headers(),
        )
        error = (confirm.json() or {}).get("error") or {}
        print(f"  POST /v1/confirmations → {confirm.status_code} · code={error.get('code')}")
        print(f"    detail={json.dumps(error.get('detail'), ensure_ascii=False)[:120]}")
        return {
            "suggestions": len(suggestions),
            "keys": sorted(first),
            "confirm_status": confirm.status_code,
            "confirm_code": error.get("code"),
        }


def _step_timeouts(base: str, slow: _SlowLabelProvider) -> None:
    """🔴 ② BE 클라이언트 타임아웃 셋 — ⓐ 짧게 · ⓑ AI 상한 초과 · ⓒ 넉넉히."""
    print("\n━━ ② BE 타임아웃 셋 ━━")
    for label, client_timeout in (
        ("ⓐ BE 가 먼저 끊는다", 0.5),
        ("ⓑ AI 상한(20s) 초과", 40.0),
        ("ⓒ 넉넉하다", 40.0),
    ):
        before = slow.finished
        started = time.monotonic()
        try:
            with httpx.Client(timeout=client_timeout) as client:
                response = client.post(
                    f"{base}/v1/labels/suggest", json=_body(), headers=_headers()
                )
            outcome = f"{response.status_code}"
        except httpx.TimeoutException as error:
            outcome = f"🔴 클라 예외 {type(error).__name__}"
        elapsed = int((time.monotonic() - started) * 1000)
        #: 🔴 클라가 끊은 뒤 서버가 **끝까지 돌았나** — 원가가 나갔는지의 증거다.
        time.sleep(slow_delay_grace())
        print(
            f"  {label:20} 클라상한 {client_timeout:>5}s → {outcome:24} "
            f"({elapsed}ms) · 🔴 서버가 끝까지 돈 콜 {slow.finished - before}건"
        )


def slow_delay_grace() -> float:
    """클라가 끊은 뒤 서버가 마저 돌 시간 — 관측용 여유."""
    return 2.0


def main() -> None:
    parser = argparse.ArgumentParser(description="라벨 BE 연동 리허설(실 LLM 0회)")
    parser.add_argument("--port", type=int, default=8123)
    parser.add_argument(
        "--delay-s",
        type=float,
        default=1.5,
        help="대역 provider 지연(초) — BE 가 먼저 끊는 상황을 만든다",
    )
    args = parser.parse_args()

    slow = _SlowLabelProvider(args.delay_s)
    labels_router.set_label_suggest_provider(slow)
    try:
        with _Server(args.port) as server:
            print(f"🔴 로컬 서버 {server.base} · 지연 {args.delay_s}s · 실 LLM 0회")
            _step_shape(server.base)
            _step_timeouts(server.base, slow)
    finally:
        labels_router.reset_label_suggest_provider()


if __name__ == "__main__":
    main()


__all__ = ["main"]
