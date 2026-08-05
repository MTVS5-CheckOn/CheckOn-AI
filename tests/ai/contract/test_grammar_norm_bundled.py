"""어문 규범 자료가 패키지에 동봉돼 있는지 — R-1 대조의 전제.

자료가 없으면 R-1 문법 대조가 불가능해 **T1 전체와 T2 어휘 문항이 발행
차단**된다(`05` §1.1). 즉 이 파일들이 배포에 안 실리면 서비스가 서지 않는다.

절대 경로·외부 마운트를 쓰지 않는 이유가 여기 있다 — 환경마다 경로가 달라지는
구조를 두면 개발 PC에서만 돌고 서버에서 죽는다. 이 테스트가 그 회귀를 잡는다.
"""

from __future__ import annotations

import csv

import pytest

from ai.problem_generation.infrastructure.config import (
    DEFAULT_GRAMMAR_NORM_VERSION,
    VerificationConfigError,
    grammar_norm_dir,
)

#: R-1 대조와 grammar_rule ID 산출에 실제로 쓰이는 파일.
_REQUIRED = {
    "최신고지규정정보.csv": "조항 원문 + 해설 — R-1 대조 정본",
    "고시정보.csv": "시행일 — grammar_rule ID에 필요(05 §1.1.1)",
}

#: 보조 자료. 지금 R-1이 직접 쓰지는 않으나 같은 배포본에 함께 있어야
#: 버전이 갈리지 않는다.
_BUNDLED = {
    "발행고시규정정보.csv",
    "용례정보.csv",
    "관련규정정보.csv",
    "항별연혁정보.csv",
    "결정근거.csv",
    "북한규정정보.csv",
    "SOURCE.txt",
}


def test_grammar_norm_bundle_exists() -> None:
    """버전 디렉터리가 패키지 안에 있어야 한다."""
    path = grammar_norm_dir()
    assert path.is_dir()
    assert path.name == DEFAULT_GRAMMAR_NORM_VERSION


def test_missing_version_fails_closed() -> None:
    """없는 버전을 부르면 조용히 넘어가지 않는다.

    빈 경로를 돌려주면 R-1이 대조 없이 통과하는 길이 생긴다(불변식 2).
    """
    with pytest.raises(VerificationConfigError, match="어문 규범 자료"):
        grammar_norm_dir("존재하지-않는-버전")


@pytest.mark.parametrize("name", sorted(_REQUIRED), ids=lambda n: n)
def test_required_file_is_present_and_readable(name: str) -> None:
    """필수 파일은 존재하고 UTF-8로 읽히며 헤더가 있어야 한다."""
    path = grammar_norm_dir() / name
    assert path.is_file(), f"{name} 누락 — {_REQUIRED[name]}"
    header = path.read_text(encoding="utf-8").split("\n", 1)[0]
    assert "," in header, f"{name} 헤더를 읽을 수 없다"


def test_all_bundled_files_present() -> None:
    """8종 CSV와 SOURCE.txt가 한 배포본에 함께 있어야 버전이 갈리지 않는다."""
    present = {p.name for p in grammar_norm_dir().iterdir()}
    missing = (set(_REQUIRED) | _BUNDLED) - present
    assert not missing, f"동봉 누락: {sorted(missing)}"


def test_article_text_is_actually_populated() -> None:
    """조항 본문이 비어 있으면 R-1이 대조할 것이 없다.

    파일만 있고 내용이 비는 회귀를 잡는다.
    """
    path = grammar_norm_dir() / "최신고지규정정보.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    articles = [row for row in rows if row["구분"] == "항"]
    with_text = [row for row in articles if row["본문"].strip()]

    assert len(articles) > 1_000, f"항이 너무 적다: {len(articles)}"
    assert len(with_text) > 1_000, f"본문 있는 항이 너무 적다: {len(with_text)}"


def test_source_txt_records_license() -> None:
    """출처 표시가 의무이므로 라이선스 근거가 배포본에 함께 있어야 한다(05 §1.1.2)."""
    text = (grammar_norm_dir() / "SOURCE.txt").read_text(encoding="utf-8")
    for token in ("공공누리 제1유형", "출처 표시", "kogl.or.kr"):
        assert token in text, f"SOURCE.txt에 {token} 기록이 없다"
