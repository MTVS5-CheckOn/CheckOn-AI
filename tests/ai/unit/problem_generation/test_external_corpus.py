"""R-8 외부 대조 — 지문 색인·포함도와 AI Hub 어댑터의 fail-closed 검증."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from ai.problem_generation.domain.external_corpus import (
    ExternalCorpusIndex,
    shingles,
)
from ai.problem_generation.infrastructure.aihub_corpus import (
    ExternalCorpusUnavailable,
    load_aihub_external_corpus,
)

_PASSAGE = (
    "높임 표현을 적절하게 사용하기 위해서는 말하는 사람과 듣는 사람의 관계, "
    "말하는 사람과 서술의 주체 또는 객체와의 관계, 그리고 대화가 일어나는 맥락 등을 "
    "모두 고려해야 한다. 높임 표현을 적절하게 쓰면 자연스럽게 언어 예절을 지켜 대화를 "
    "할 수 있기 때문에 상대와의 관계를 원만하게 유지하면서 의사소통을 할 수 있다."
)
_UNRELATED = (
    "전자기 유도는 도선을 지나는 자기력선속이 시간에 따라 변할 때 기전력이 생기는 "
    "현상이다. 발전기는 이 원리를 이용해 회전 운동을 전기 에너지로 바꾼다. 코일의 "
    "감은 수와 자석의 세기가 커질수록 유도되는 기전력의 크기도 함께 커진다."
)


def _index() -> ExternalCorpusIndex:
    return ExternalCorpusIndex((("corpus:1", _PASSAGE), ("corpus:2", _UNRELATED)))


def test_shingles_are_stable_across_calls_and_processes() -> None:
    #: 프로세스 소금이 섞이는 `hash()`를 쓰면 같은 입력이 실행마다 다른 표본이 된다.
    assert shingles(_PASSAGE) == shingles(_PASSAGE)
    assert shingles(_PASSAGE)
    assert 12345 not in shingles("")


def test_verbatim_reuse_is_full_containment() -> None:
    match = _index().closest(_PASSAGE)

    assert match is not None
    assert match.source_ref == "corpus:1"
    assert match.containment == pytest.approx(1.0)


def test_independent_text_does_not_match_the_corpus() -> None:
    independent = (
        "봄이 오면 마당 한켠의 목련이 먼저 피었다. 아버지는 해마다 그 나무 아래에 "
        "평상을 내어놓고 오래 앉아 계셨다. 나는 그 곁에서 책장을 넘기며 계절이 "
        "바뀌는 소리를 들었다."
    )

    assert _index().closest(independent) is None


def test_partial_reuse_is_measured_against_the_query_length() -> None:
    #: 분모가 질의 쪽이라 「긴 외부 지문이 전재를 희석」하지 않는다.
    reused = _PASSAGE[:120]
    match = _index().closest(reused)

    assert match is not None
    assert match.containment > 0.9


def test_fingerprint_depends_only_on_indexed_documents() -> None:
    same = ExternalCorpusIndex((("corpus:2", _UNRELATED), ("corpus:1", _PASSAGE)))
    other = ExternalCorpusIndex((("corpus:1", _PASSAGE),))

    assert _index().fingerprint == same.fingerprint
    assert _index().fingerprint != other.fingerprint


def _write_label_archive(root: Path, *, passages: tuple[str, ...]) -> Path:
    label_dir = root / "1.데이터" / "Training" / "02.라벨링데이터"
    label_dir.mkdir(parents=True)
    archive = label_dir / "TL_04.고등학교 1학년.zip"
    payload = {
        "source_data_info": {"source_data_name": "S2_고등_1_000001"},
        "learning_data_info": [
            {"class_name": "문항", "class_info_list": [{"text_description": "발문"}]},
            {
                "class_name": "지문",
                "class_info_list": [
                    {"text_description": text} for text in passages
                ],
            },
        ],
    }
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("S2_고등_1_000001.json", json.dumps(payload, ensure_ascii=False))
    return archive


def test_adapter_indexes_only_passage_blocks(tmp_path: Path) -> None:
    _write_label_archive(tmp_path, passages=(_PASSAGE, _UNRELATED))

    index = load_aihub_external_corpus(tmp_path)

    assert index.size == 2
    match = index.closest(_PASSAGE)
    assert match is not None
    assert match.source_ref.startswith("aihub-71857:S2_고등_1_000001#")
    #: 발문은 정형이라 대조 축이 아니다 — 색인에 들어가면 오탐만 낸다.
    assert index.closest("발문") is None


def test_missing_corpus_root_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ExternalCorpusUnavailable, match="경로가 없다"):
        load_aihub_external_corpus(tmp_path / "없는경로")


def test_corpus_root_without_label_archives_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "1.데이터").mkdir()

    with pytest.raises(ExternalCorpusUnavailable, match="라벨 zip이 없다"):
        load_aihub_external_corpus(tmp_path)


def test_corpus_with_no_passage_text_fails_closed(tmp_path: Path) -> None:
    _write_label_archive(tmp_path, passages=("",))

    with pytest.raises(ExternalCorpusUnavailable, match="지문이 없다"):
        load_aihub_external_corpus(tmp_path)


def test_damaged_archive_fails_closed(tmp_path: Path) -> None:
    archive = _write_label_archive(tmp_path, passages=(_PASSAGE,))
    archive.write_bytes(b"not a zip at all")

    with pytest.raises(ExternalCorpusUnavailable, match="손상"):
        load_aihub_external_corpus(tmp_path)
