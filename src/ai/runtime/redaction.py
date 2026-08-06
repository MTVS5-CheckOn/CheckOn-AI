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
from collections.abc import Iterable, Mapping, Sequence
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
    honorific_words: frozenset[str]
    honorific_particles: tuple[str, ...]
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
    name_exclude: frozenset[str]
    p9_kakao: re.Pattern[str]
    p9_org: re.Pattern[str]
    p9_filename: re.Pattern[str]
    p9_filename_hangul: re.Pattern[str]


def _dedupe_overlaps(matches: list[re.Match[str]]) -> list[re.Match[str]]:
    """그룹1 구간이 겹치는 후보를 하나로 본다 — **밀도 오계산 방지**.

    같은 이름이 여러 패턴에 걸릴 수 있다(`서연이`는 별명형 패턴과 성씨 패턴 양쪽에
    매칭된다 — `서`가 성씨다). 겹침을 따로 세면 후보 1개짜리 문장이 밀도 2로 올라가
    **문장 통째가 ⟪확인필요⟫로** 바뀐다. 한 사람은 한 번만 센다.
    """
    kept: list[re.Match[str]] = []
    for match in matches:
        if kept and match.start(1) < kept[-1].end(1):
            continue
        kept.append(match)
    return kept


def _expand_placeholders(
    patterns: Iterable[object], substitutions: Mapping[str, str]
) -> list[str]:
    """`$name` 자리에 목록을 끼워 넣는다 — **조립만** 한다.

    정규식 자체는 yaml이 원본이고(§6 어휘·패턴 하드코딩 금지) 코드는 목록을 문자
    클래스·교대로 잇는 일만 한다. 성씨를 유한 집합으로 고정하는 것이 일반 명사 오탐을
    억제하는 유일한 장치이고, 조사 목록은 **호칭어 필터와 공유**해야 하므로(8/6) 둘 다
    데이터로 관리돼야 한다 — 코드가 정규식 문자열을 파싱하는 방향은 금지다.
    """
    expanded: list[str] = []
    for pattern in patterns:
        text = str(pattern)
        for key, value in substitutions.items():
            text = text.replace(f"${key}", value)
        expanded.append(text)
    return expanded


def _strip_particle(word: str, particles: Sequence[str]) -> str:
    """어절에서 조사 하나를 벗긴다 — 호칭어 필터가 `학생의`를 `학생`으로 보게.

    🔴 **`startswith` 비교를 쓰지 않는 이유**가 이 함수의 존재 이유다. 접두 비교는
    `학생회`까지 호칭어로 보고 스킵해 **미탐 방향**으로 샌다. 조사 목록으로 정확히
    벗기면 `학생의`만 스킵되고 `학생회`는 그대로 후보로 남는다.

    긴 조사부터 본다(`한테`가 `한`보다 먼저) — 목록 순서에 판정이 의존하지 않게.
    조사만으로 이루어진 어절은 벗기지 않는다(빈 문자열이 호칭어 목록에 걸릴 여지 제거).
    """
    for particle in sorted(particles, key=len, reverse=True):
        if len(word) > len(particle) and word.endswith(particle):
            return word[: -len(particle)]
    return word


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
    #: 🔴 `$surnames`는 **두 곳**이 쓴다 — 스코어링과 호칭(관계어 갈래). 조립을 한 번만
    #: 하고 양쪽에 넘긴다: 따로 만들면 목록이 갈릴 수 있고, 그게 `patterns`↔`words`가
    #: 갈려 조사 결합형이 우회했던 ⓛ와 같은 형태다.
    surname_class = "".join(str(name) for name in scoring.get("surnames", []))
    context_risk = raw["context_risk"]
    particles: dict[str, list[str]] = raw["particles"]
    korean_particles = tuple(str(word) for word in particles["korean"])
    return _Config(
        tokens={str(k): str(v) for k, v in raw["tokens"].items()},
        batch=tuple(batch),
        honorific_type=str(raw["name_honorific"]["type"]),
        honorific=_compile_all(
            _expand_placeholders(
                raw["name_honorific"]["patterns"], {"surnames": surname_class}
            )
        ),
        honorific_words=frozenset(
            str(word) for word in raw["name_honorific"].get("words", [])
        ),
        honorific_particles=korean_particles,
        phone=phone,
        korean_digits={str(k): str(v) for k, v in bypass["korean_digits"].items()},
        bypass_candidate=re.compile(str(bypass["candidate"])),
        separators=re.compile(str(bypass["separators"])),
        residual=re.compile(str(bypass["residual_hangul_digits"])),
        contact_context=tuple(bypass["contact_context"]),
        name_candidates=_compile_all(
            _expand_placeholders(
                scoring["name_candidates"],
                {
                    "surnames": surname_class,
                    "particles_korean": "|".join(korean_particles),
                    "particles_latin": "|".join(
                        str(word) for word in particles["latin"]
                    ),
                },
            )
        ),
        density_threshold=int(scoring["density_threshold"]),
        sentence_split=re.compile(f"({scoring['sentence_split']})"),
        literary_names=frozenset(whitelists.get("literary_names", [])),
        name_exclude=frozenset(whitelists.get("name_exclude", [])),
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
            text = self._sub_honorific(pattern, text)
        return text

    def _sub_honorific(self, pattern: re.Pattern[str], source: str) -> str:
        """호칭 결합 패턴 1개 적용.

        🔴 **여기에 `_follows_token`을 걸지 않는다 — 걸면 미탐이 난다.** 실측(8/6)::

            "010-1234-5678 서연 어머니"
              토큰 인접 스킵 있음 → "⟪연락처1⟫ 서연 어머니"      ← `서연`이 남는다(미탐)
              토큰 인접 스킵 없음 → "⟪연락처1⟫ ⟪이름1⟫ 어머니"   ← 올바름

        `_name_candidates`(스코어링)에서는 같은 스킵이 **맞다** — 스코어링은 파이프라인
        **마지막** 단계라 직전 토큰은 "이 패스에서 이미 처리된 자리"를 뜻한다. 반면 호칭
        단계는 `_mask_batch`(연락처·학교·주소) **직후**라 직전 토큰이 남의 유형이고, 그
        뒤 어절은 **아직 마스킹되지 않은 실제 이름**일 수 있다. 같은 함수라도 단계에 따라
        전제가 다르다. 미탐은 오탐보다 나쁘다(불변식 3 fail-closed · 코퍼스 게이트).

        `source`를 명시 인자로 받는 것은 클로저가 루프 변수를 붙잡지 않게 하기 위함이다
        (ruff B023 — 지금은 맞게 도는 코드지만 패턴을 추가하는 다음 사람이 틀리기 쉽다).
        """

        def repl(match: re.Match[str]) -> str:
            # 🔴 그룹1이 호칭어 자신이면 이름이 아니다 — "⟪이름1⟫ 학생 어머니"의
            # `학생`. 치환하면 호칭이 사라지고 잔여가 새 인명 후보가 된다.
            #
            # 🔴 (8/6) 비교 전에 **조사를 벗긴다.** 종전 정확 일치라 `학생의`·`학생을`은
            # 목록에 없어 우회했고, 그 결과 "○○ 학생의 어머니입니다"가 2차 redact에서
            # 새 finding을 만들어 트립와이어가 전송을 막았다(불필요한 폴백).
            # 학부모 문의의 전형 문면이라 실제로 터진 형태다.
            candidate = _strip_particle(match.group(1), self.cfg.honorific_particles)
            if candidate in self.cfg.honorific_words:
                return match.group(0)
            # 🔴 (8/6) 관계어 갈래가 `$surnames`를 쓰면서 스코어링과 **같은 실패 모드**를
            # 물려받는다 — 성씨 한 글자가 흔한 한자어의 첫 글자와 겹친다(`정`답률 형태).
            # 이미 그 목적으로 있는 목록을 재사용한다. ⚠ 목록을 늘리는 게 아니다
            # (제외 목록 부풀리기는 미탐 방향으로 작용한다 — 99 ⓓ에서 기각됨).
            if candidate in self.cfg.name_exclude:
                return match.group(0)
            return self._token(self.cfg.honorific_type, match.group(1)) + match.group(2)

        return pattern.sub(repl, source)

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
                if base in self.cfg.name_exclude or stem in self.cfg.name_exclude:
                    continue  # 성씨와 첫 글자가 겹치는 학원 도메인 어휘(성적·문의…)
                if self._follows_token(sentence, match):
                    continue  # 이미 마스킹된 자리 옆 잔여 어절 — 재검출 금지(B-3)
                found.append(match)
        return _dedupe_overlaps(sorted(found, key=lambda m: m.start(1)))

    def _follows_token(self, sentence: str, match: re.Match[str]) -> bool:
        """직전 어절이 `⟪…⟫` 토큰인가 — 그러면 이 어절은 후보가 아니다.

        마스킹이 이름을 지우고 나면 **남은 어절이 새 후보로 보인다**(`⟪이름1⟫ 학생
        어머니입니다`의 `학생`). 그 자리는 이미 처리된 자리이므로 다시 세지 않는다.
        """
        head = sentence[: match.start(1)].rstrip()
        return head.endswith("⟫")

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
