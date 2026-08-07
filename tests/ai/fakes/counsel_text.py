"""대역 초안 문면 — **`src`의 정본을 재수출**한다 (99 #13·#14).

🔴 **문면 자체는 여기 없다.** `ai.composition.counsel.provider`의 `GATE_FLOOR_TAIL`이
정본이다 — `FakeCounselProvider`(그것도 `src`에 산다)가 같은 꼬리를 써야 하고, 두 곳에
두면 갈린다(#02 부류). 왜 그 문면이어야 하는지도 거기 적혀 있다.

⚠ **이 파일이 있는 이유는 import 규약뿐이다** — 테스트는 평면 import를 쓴다
(`from counsel_text import draft`). `tests`는 패키지가 아니라 `import tests.…`가 안 되고
(`pythonpath = ["src"]`), `tests/ai/conftest.py`가 이 디렉터리를 경로에 넣는다.
`fake_provider` 선례와 같은 형태다.
"""

from __future__ import annotations

from typing import Final

from ai.composition.counsel.provider import GATE_FLOOR_TAIL, gate_floor_draft

draft = gate_floor_draft
"""대역 초안 — 뜻은 그대로 두고 길이만 올린다. 정본은 `provider.gate_floor_draft`."""

#: 아무 내용도 심지 않은 기본 대역 초안 — *"통과하는 평범한 초안"* 이 필요한 자리.
DEFAULT_DRAFT: Final = gate_floor_draft("이번 기간 학습 상황을 정리해 드립니다.")

__all__ = ["DEFAULT_DRAFT", "GATE_FLOOR_TAIL", "draft"]
