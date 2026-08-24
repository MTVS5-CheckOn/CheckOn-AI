"""라벨 층별 통과 수 — **층마다 자기 수를 낸다**(99 #208 · 2026-08-24).

🔴 **이건 관측이 아니라 배선 가드다.** №66·№67·№71 이 네 회차 연속 「뒤집기 green」을
냈고 셋이 같은 모양이었다: **단위 검사가 층 함수를 직접 부르니, 라우터가 그 층을 아예
안 불러도 단위 검사도 종단 검사도 둘 다 통과**했다. 층을 «껐는데» 아무 데도 안 빨개졌다.

⇒ 처방: **층마다 통과 수를 기록하고, 종단 검사가 그 수를 본다.**
층이 안 불리면 그 칸은 **없다**(`-`) ⇒ 종단 검사가 red. 층이 불렸지만 아무것도 안 걸렀다면
그 칸은 **0** ⇒ 사실 그대로다. 🔴 **`-` 와 `0` 을 절대 같게 쓰지 않는다** — 그게 이 파일의
전부다(№63 이래의 관문: «답이 없으면 「없다」가 아니라 「미확인」»).

🔴 **`runtime/metrics.py` 를 만들지 않는다**(양자 승인 신설 · 99 #65 판정). 이건 A 소유
capability 안의 국소 자료구조이고, 나가는 것은 **구조화 로그 한 줄**뿐이다.
🔴 **본문·인용문을 담지 않는다**(불변식 3 · 99 #80) — 수와 사유 코드까지다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Final

#: 🔴 **미측정 표기** — «0건» 이 아니라 «그 층이 안 돌았다». 종단 검사가 이걸 본다.
UNMEASURED: Final = "-"


class LayerCountedTwice(RuntimeError):
    """같은 층이 한 요청에서 **두 번** 수를 냈다 — 배선이 겹쳤다는 뜻이다.

    ⚠ 조용히 덮어쓰면 «두 번 걸러졌다» 가 «한 번» 으로 보인다. 요청 하나에 층 하나다.
    """


class LabelLayerCounts:
    """한 요청의 층별 수 — 층이 **자기 칸만** 채운다.

    ⚠ 가변 객체를 층에 넘긴다. 순수 반환으로 모으지 않는 이유: 파서와 마스킹은
    **provider 안**에서 돌아 라우터가 그 수를 볼 길이 없다. 반환형을 바꾸면 계약
    (`LabelSuggestProvider`)이 산출물이 아닌 것을 나르게 된다 ⇒ **주입**이 맞다.
    """

    __slots__ = ("_gate_drops", "_values")

    def __init__(self) -> None:
        self._values: dict[str, int] = {}
        self._gate_drops: Mapping[str, int] | None = None

    def _put(self, layer: str, **counts: int) -> None:
        for key in counts:
            if key in self._values:
                raise LayerCountedTwice(f"{layer} 층이 두 번 수를 냈다: {key}")
        self._values.update(counts)

    def record_masking(self, *, kept: int, dropped: int) -> None:
        """마스킹 층 — 보낼 수 있는 이력만 남긴 결과(`keep_sendable_history`)."""
        self._put("마스킹", history_kept=kept, history_dropped=dropped)

    def record_parse(self, *, parsed: int, dropped: int) -> None:
        """파서 층 — 형식이 맞아 제안이 된 줄 · 버린 줄(`parse_suggestions`)."""
        self._put("파서", parsed=parsed, parse_dropped=dropped)

    def record_gate(self, *, passed: int, drop_reasons: Iterable[str]) -> None:
        """게이트 층 — 근거 실존을 통과한 수 · **사유별** 드롭(`ground_suggestions`).

        🔴 사유를 뭉치지 않는다: 없는 record_id 를 가리키는 것(**날조**)과 인용문이 안
        맞는 것(**변형**)은 대응이 다르다(`grounding.DROP_*`).
        """
        reasons = Counter(drop_reasons)
        self._put("게이트", gate_passed=passed, gate_dropped=sum(reasons.values()))
        self._gate_drops = dict(reasons)

    def record_merge(self, *, before: int, after: int) -> None:
        """병합 층 — 같은 축을 합치기 전후(`merge_duplicate_axes`)."""
        self._put("병합", merged_from=before, merged_to=after)

    def as_log_fields(self) -> str:
        """구조화 로그 한 줄 — 🔴 **안 돈 층은 `-`**(0 이 아니다).

        종단 검사가 이 문자열을 읽는다. 층을 끄면 그 칸이 `-` 가 되어 red.
        """
        fields = [
            f"{key}={self._values.get(key, UNMEASURED)}"
            for key in (
                "history_kept",
                "history_dropped",
                "parsed",
                "parse_dropped",
                "gate_passed",
                "gate_dropped",
                "merged_from",
                "merged_to",
            )
        ]
        if self._gate_drops:
            reasons = ",".join(
                f"{reason}:{count}" for reason, count in sorted(self._gate_drops.items())
            )
            fields.append(f"gate_reasons={reasons}")
        return " ".join(fields)


__all__ = ["UNMEASURED", "LabelLayerCounts", "LayerCountedTwice"]
