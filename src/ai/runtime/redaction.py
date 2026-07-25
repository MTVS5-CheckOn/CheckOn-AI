"""redaction 엔진 — 개인정보 2차 방어(LLM 전송·저장 직전).

정본: docs/policies/masking_redaction.md. 패턴은 redaction_patterns.yaml이 원본이며
이 코드는 순서(§2 파이프라인)·토큰 규격(§1)만 구현한다 — 정규식 하드코딩 없음.
소유: A 단독(02_ownership §5). LLM·벤더 SDK·datetime.now() 없음(순수 문자열 처리).

파이프라인(§2, P1ⓐ 명부는 백엔드 1차라 제외): 정규식 일괄(P2~P7) → 통계 인명(P1ⓑ)
→ 우회 휴리스틱(P8) → 잔여 위험 스코어링.

**단방향(§1):** 원문↔토큰 매핑을 반환값·로그 어디에도 남기지 않는다. 번호 부여용
카운터는 이 함수 호출 안에서만 살고 반환과 함께 폐기된다(복원 테이블 아님).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict

_PATTERNS_PATH = Path(__file__).parent / "redaction_patterns.yaml"


class Finding(BaseModel):
    """마스킹 1건 — 유형과 부여된 토큰만. 원문 값은 담지 않는다(단방향, §1)."""

    model_config = ConfigDict(frozen=True)

    type: str
    token: str


class RedactionResult(BaseModel):
    """redact() 반환값 — 소비자가 fail-closed 판단에 쓴다."""

    model_config = ConfigDict(frozen=True)

    masked_text: str
    findings: tuple[Finding, ...] = ()
    uncertain: bool = False
    """⟪확인필요⟫가 하나라도 있으면 True — 소비자는 전송 중단(fail-closed) 판단."""


@dataclass(frozen=True)
class _Batch:
    type_key: str
    patterns: tuple[re.Pattern[str], ...]
    whitelist: frozenset[str]
    context_words: tuple[str, ...]


@dataclass(frozen=True)
class _Config:
    tokens: dict[str, str]
    batch: tuple[_Batch, ...]
    honorific_type: str
    honorific: tuple[re.Pattern[str], ...]
    phone: tuple[re.Pattern[str], ...]
    korean_digits: dict[str, str]
    bypass_candidate: re.Pattern[str]
    separators: re.Pattern[str]
    residual: re.Pattern[str]
    contact_context: tuple[str, ...]
    name_candidates: tuple[re.Pattern[str], ...]
    density_threshold: int
    sentence_split: re.Pattern[str]
    literary_names: frozenset[str]
    p9_kakao: re.Pattern[str]
    p9_org: re.Pattern[str]
    p9_filename: re.Pattern[str]
    p9_filename_hangul: re.Pattern[str]


def _compile_all(raw: Iterable[object]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(str(p)) for p in raw)


@lru_cache
def _config() -> _Config:
    raw = yaml.safe_load(_PATTERNS_PATH.read_text(encoding="utf-8"))
    whitelists: dict[str, list[str]] = raw.get("whitelists", {})
    batch: list[_Batch] = []
    phone: tuple[re.Pattern[str], ...] = ()
    for item in raw["regex_batch"]:
        wl = (
            frozenset(whitelists.get(item["whitelist"], []))
            if "whitelist" in item
            else frozenset()
        )
        compiled = _compile_all(item["patterns"])
        batch.append(
            _Batch(
                type_key=str(item["type"]),
                patterns=compiled,
                whitelist=wl,
                context_words=tuple(item.get("context_words", ())),
            )
        )
        if item["id"] == "P2_phone":
            phone = compiled
    bypass = raw["bypass"]
    scoring = raw["scoring"]
    context_risk = raw["context_risk"]
    return _Config(
        tokens={str(k): str(v) for k, v in raw["tokens"].items()},
        batch=tuple(batch),
        honorific_type=str(raw["name_honorific"]["type"]),
        honorific=_compile_all(raw["name_honorific"]["patterns"]),
        phone=phone,
        korean_digits={str(k): str(v) for k, v in bypass["korean_digits"].items()},
        bypass_candidate=re.compile(str(bypass["candidate"])),
        separators=re.compile(str(bypass["separators"])),
        residual=re.compile(str(bypass["residual_hangul_digits"])),
        contact_context=tuple(bypass["contact_context"]),
        name_candidates=_compile_all(scoring["name_candidates"]),
        density_threshold=int(scoring["density_threshold"]),
        sentence_split=re.compile(f"({scoring['sentence_split']})"),
        literary_names=frozenset(whitelists.get("literary_names", [])),
        p9_kakao=re.compile(str(context_risk["kakao"])),
        p9_org=re.compile(str(context_risk["org"])),
        p9_filename=re.compile(str(context_risk["filename"])),
        p9_filename_hangul=re.compile(str(context_risk["filename_hangul"])),
    )


@dataclass
class _Redactor:
    """호출 1회의 상태(토큰 카운터·findings). 반환과 함께 폐기 — 복원 테이블 아님."""

    cfg: _Config
    counters: dict[str, dict[str, int]] = field(default_factory=dict)
    emitted: dict[str, str] = field(default_factory=dict)  # token -> type(라벨)
    uncertain: bool = False

    def _token(self, type_key: str, value: str) -> str:
        label = self.cfg.tokens[type_key]
        registry = self.counters.setdefault(type_key, {})
        if value not in registry:
            registry[value] = len(registry) + 1
        token = f"⟪{label}{registry[value]}⟫"
        self.emitted[token] = label
        return token

    def _uncertain_token(self) -> str:
        token = f"⟪{self.cfg.tokens['uncertain']}⟫"
        self.emitted[token] = self.cfg.tokens["uncertain"]
        self.uncertain = True
        return token

    # ── 1) 정규식 일괄 (P2~P7) ─────────────────────────────
    def _mask_batch(self, text: str) -> str:
        for batch in self.cfg.batch:
            for pattern in batch.patterns:
                text = self._sub_pattern(text, pattern, batch)
        return text

    def _sub_pattern(self, text: str, pattern: re.Pattern[str], batch: _Batch) -> str:
        def repl(match: re.Match[str]) -> str:
            value = match.group(0)
            # 접미사 매칭 — '최고'·복합어('장애아동'→'아동') 오탐을 제외(§6 whack-a-mole 완화)
            if any(value.endswith(word) for word in batch.whitelist):
                return value
            if batch.context_words and not self._context_near(text, match, batch.context_words):
                return value
            return self._token(batch.type_key, value)

        return pattern.sub(repl, text)

    @staticmethod
    def _context_near(text: str, match: re.Match[str], words: tuple[str, ...]) -> bool:
        window = text[max(0, match.start() - 4) : match.end() + 4]
        return any(word in window for word in words)

    # ── 2) 통계 인명 (P1ⓑ 호칭 결합) ───────────────────────
    def _mask_honorific(self, text: str) -> str:
        for pattern in self.cfg.honorific:
            def repl(match: re.Match[str]) -> str:
                return self._token(self.cfg.honorific_type, match.group(1)) + match.group(2)

            text = pattern.sub(repl, text)
        return text

    # ── 3) 우회 휴리스틱 (P8) ──────────────────────────────
    def _mask_bypass(self, text: str) -> str:
        def repl(match: re.Match[str]) -> str:
            raw = match.group(0)
            decoded = raw
            for hangul, digit in self.cfg.korean_digits.items():
                decoded = decoded.replace(hangul, digit)
            stripped = self.cfg.separators.sub("", decoded)
            if any(p.search(stripped) for p in self.cfg.phone):
                return self._token("phone", stripped)
            return raw

        text = self.cfg.bypass_candidate.sub(repl, text)
        return self._residual_fallback(text)

    def _residual_fallback(self, text: str) -> str:
        def repl(match: re.Match[str]) -> str:
            window = text[max(0, match.start() - 10) : match.end() + 10]
            if any(word in window for word in self.cfg.contact_context):
                return self._uncertain_token()
            return match.group(0)

        return self.cfg.residual.sub(repl, text)

    # ── 3b) 문맥어 기반 잔여 위험 (P9 · fail-closed) ─────────
    def _mask_context_risk(self, text: str) -> str:
        def repl_group1(match: re.Match[str]) -> str:
            # 그룹1(카톡 ID)만 치환, 앞 문맥어는 유지
            prefix = match.group(0)[: match.start(1) - match.start(0)]
            return prefix + self._uncertain_token()

        def repl_org(match: re.Match[str]) -> str:
            # 고유명사 후보(그룹1)만 치환, 기관어(그룹2)는 유지
            return self._uncertain_token() + match.group(2)

        def repl_filename(match: re.Match[str]) -> str:
            # 파일명 토큰 속 한글 조각을 치환(확장자·영숫자는 유지)
            return self.cfg.p9_filename_hangul.sub(
                lambda _: self._uncertain_token(), match.group(0)
            )

        text = self.cfg.p9_kakao.sub(repl_group1, text)
        text = self.cfg.p9_org.sub(repl_org, text)
        return self.cfg.p9_filename.sub(repl_filename, text)

    # ── 4) 잔여 위험 스코어링 (밀도 ≥ threshold → 문장 통째) ──
    def _mask_scoring(self, text: str) -> str:
        parts = self.cfg.sentence_split.split(text)
        # split(캡처그룹) → [문장, 구분자, 문장, 구분자, …]. 짝수 인덱스가 문장.
        return "".join(
            part if index % 2 else self._score_sentence(part)
            for index, part in enumerate(parts)
        )

    def _score_sentence(self, sentence: str) -> str:
        matches = self._name_candidates(sentence)
        if len(matches) >= self.cfg.density_threshold:
            return self._uncertain_token()
        if len(matches) == 1:
            match = matches[0]
            return sentence[: match.start(1)] + self._uncertain_token() + sentence[match.end(1) :]
        return sentence

    def _name_candidates(self, sentence: str) -> list[re.Match[str]]:
        found: list[re.Match[str]] = []
        for pattern in self.cfg.name_candidates:
            for match in pattern.finditer(sentence):
                base = match.group(1)
                stem = base[:-1] if base.endswith("이") else base
                if stem in self.cfg.literary_names or base in self.cfg.literary_names:
                    continue
                found.append(match)
        return sorted(found, key=lambda m: m.start())

    def run(self, text: str) -> RedactionResult:
        text = self._mask_batch(text)
        text = self._mask_honorific(text)
        text = self._mask_bypass(text)
        text = self._mask_context_risk(text)
        text = self._mask_scoring(text)
        findings = tuple(
            Finding(type=label, token=token) for token, label in self.emitted.items()
        )
        return RedactionResult(masked_text=text, findings=findings, uncertain=self.uncertain)


def redact(text: str) -> RedactionResult:
    """텍스트를 마스킹한다 — 순수 함수(같은 입력 → 같은 출력, 토큰 번호 포함).

    반환의 uncertain=True면 소비자가 fail-closed(전송 중단)로 판단한다(불변식 3).
    """
    return _Redactor(cfg=_config()).run(text)


__all__ = ["Finding", "RedactionResult", "redact"]
