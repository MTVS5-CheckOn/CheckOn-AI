"""라벨 실측기가 라우터의 최종 소비 객체를 집계하는지 고정한다."""

import asyncio

from fake_provider import FakeProvider

from ai.composition.labels.provider import GatewayLabelSuggestProvider, build_label_gateway
from ai.evaluation.labels_llm_smoke import _history, _one


def test_duplicate_input_is_measured_as_one_final_suggestion() -> None:
    history = _history(("지난주 과제를 모두 제출했습니다",))
    duplicate = "\n".join(
        [
            "comm | data | 0.7 | cm_88 | 지난주 과제를 모두 제출했습니다",
            "comm | data | 0.8 | cm_88 | 지난주 과제를 모두 제출했습니다",
        ]
    )
    provider = GatewayLabelSuggestProvider(build_label_gateway(FakeProvider([duplicate])))

    run = asyncio.run(_one(provider, history))

    assert run.grounded == 2
    assert run.merged == 1
    assert run.axes == ("comm",)
    assert run.signature == (("comm", "data"),)
    assert run.merged == len(run.axes) == len(run.signature)
