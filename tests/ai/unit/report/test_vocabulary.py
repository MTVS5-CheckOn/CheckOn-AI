"""리포트 블록 어휘 YAML의 실패 닫힘 검증."""

from pathlib import Path

import pytest

from ai.contracts.report import ReportBlockKind
from ai.report.vocabulary import ReportVocabularyError, load_report_block_vocabulary


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "block_kinds.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_default_vocabulary_has_all_five_kinds() -> None:
    vocabulary = load_report_block_vocabulary()

    assert set(vocabulary.block_kinds) == set(ReportBlockKind)


@pytest.mark.parametrize(
    "body, reason",
    [
        (
            """version: report-block-kinds.v1
block_kinds: [greeting, fact, chart_analysis, suggestion]
""",
            "완전하지 않다",
        ),
        (
            """version: report-block-kinds.v1
block_kinds: [greeting, fact, chart_analysis, suggestion, closing, closing]
""",
            "중복",
        ),
        (
            """version: report-block-kinds.v1
block_kinds: [greeting, fact, chart_analysis, suggestion, closing]
unexpected: true
""",
            "unexpected",
        ),
        (
            """version: report-block-kinds.v1
block_kinds: [greeting, fact, chart_analysis, suggestion, unknown]
""",
            "unknown",
        ),
    ],
)
def test_vocabulary_fails_closed(tmp_path: Path, body: str, reason: str) -> None:
    with pytest.raises(ReportVocabularyError, match=reason):
        load_report_block_vocabulary(_write(tmp_path, body))


def test_malformed_yaml_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ReportVocabularyError, match="YAML"):
        load_report_block_vocabulary(_write(tmp_path, "block_kinds: [greeting"))
