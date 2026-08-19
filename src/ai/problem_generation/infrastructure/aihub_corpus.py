"""AI Hub 71857(국어 교과 지문형 문제) → R-8 외부 대조 색인 어댑터.

⚠ **자료는 저장소에 반입하지 않는다**(CLAUDE.md §3 · 05 §1.1.4). 배포 환경이 경로를
`EXTERNAL_CORPUS_ROOT`로 주입하고, 여기서는 압축을 풀지 않은 채 zip 안의 라벨 JSON만
읽는다. 경로가 없으면 **코퍼스 없이 R-8을 여는 것을 막기 위해** 예외로 끝낸다(06 §1).

⚠ **비상업 전제에서만 쓴다**(09 §3 W18). 상용 전환 시 구축기관 협의가 선행 조건이다.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Iterator
from pathlib import Path

from ai.problem_generation.domain.external_corpus import ExternalCorpusIndex

#: 라벨 zip 이 사는 폴더명과 대조에 쓰는 주석 클래스.
LABEL_DIRECTORY = "02.라벨링데이터"
PASSAGE_CLASS = "지문"


class ExternalCorpusUnavailable(RuntimeError):
    """설정된 외부 대조 코퍼스를 읽을 수 없음."""


def _passages(archive: Path) -> Iterator[tuple[str, str]]:
    with zipfile.ZipFile(archive) as bundle:
        for name in bundle.namelist():
            if not name.endswith(".json"):
                continue
            try:
                payload = json.loads(bundle.read(name).decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise ExternalCorpusUnavailable(
                    f"외부 대조 코퍼스 라벨을 읽을 수 없다: {archive.name}/{name}"
                ) from error
            source = payload.get("source_data_info") or {}
            base_ref = str(source.get("source_data_name") or Path(name).stem)
            blocks = payload.get("learning_data_info") or []
            index = 0
            for block in blocks:
                if block.get("class_name") != PASSAGE_CLASS:
                    continue
                for entry in block.get("class_info_list") or []:
                    text = (entry.get("text_description") or "").strip()
                    if not text:
                        continue
                    index += 1
                    yield f"aihub-71857:{base_ref}#{index}", text


def load_aihub_external_corpus(root: Path) -> ExternalCorpusIndex:
    """라벨 zip 전수에서 지문 본문만 뽑아 대조 색인을 만든다."""

    if not root.is_dir():
        raise ExternalCorpusUnavailable(f"외부 대조 코퍼스 경로가 없다: {root}")
    archives = sorted(
        path
        for path in root.rglob("*.zip")
        if LABEL_DIRECTORY in path.parts or LABEL_DIRECTORY in str(path.parent)
    )
    if not archives:
        raise ExternalCorpusUnavailable(f"외부 대조 코퍼스 라벨 zip이 없다: {root}")
    documents: list[tuple[str, str]] = []
    for archive in archives:
        try:
            documents.extend(_passages(archive))
        except zipfile.BadZipFile as error:
            raise ExternalCorpusUnavailable(
                f"외부 대조 코퍼스 zip이 손상됐다: {archive}"
            ) from error
    if not documents:
        raise ExternalCorpusUnavailable(f"외부 대조 코퍼스에 지문이 없다: {root}")
    return ExternalCorpusIndex(documents)


__all__ = [
    "ExternalCorpusUnavailable",
    "load_aihub_external_corpus",
]
