"""프롬프트 registry 응답 스키마명이 실제 contracts 모델을 가리키는지 검증한다.

응답 모델의 위치 규약은 최상위 ``ai.contracts.*`` 모듈이다. registry가 import 경로 대신
짧은 클래스명만 저장하므로 이 패키지의 모든 모듈을 동적으로 import해 Pydantic
``BaseModel`` 하위 클래스를 찾는다. 다른 계층에 응답 모델을 두면 이 가드가 찾지 못해
실패하며, 같은 이름을 두 모듈에 정의해도 어느 클래스를 뜻하는지 모호하므로 실패한다.
"""

from __future__ import annotations

import inspect
import pkgutil
from importlib import import_module

from pydantic import BaseModel

import ai.contracts
from ai.llm.prompts.loader import load_prompt_registry


def _registered_schema_names() -> tuple[str, ...]:
    return tuple(
        entry.response_schema_name for entry in load_prompt_registry().prompts
    )


def _contract_models() -> dict[str, list[type[BaseModel]]]:
    models: dict[str, list[type[BaseModel]]] = {}
    prefix = f"{ai.contracts.__name__}."
    for module_info in pkgutil.iter_modules(ai.contracts.__path__, prefix):
        module = import_module(module_info.name)
        for name, candidate in inspect.getmembers(module, inspect.isclass):
            if (
                candidate.__module__ == module.__name__
                and issubclass(candidate, BaseModel)
            ):
                models.setdefault(name, []).append(candidate)
    return models


def test_the_scan_finds_registered_response_schemas() -> None:
    names = _registered_schema_names()
    models = _contract_models()
    assert names, "프롬프트 registry를 0건 훑었다 — 검사 경로가 끊겼다"
    assert models, "contracts BaseModel을 0건 훑었다 — 검사 경로가 끊겼다"
    assert len(names) == 7, "등재 프롬프트 수가 바뀌었다 — 새 응답 스키마도 이 가드가 봐야 한다"


def test_every_registered_response_schema_is_a_unique_contract_model() -> None:
    models = _contract_models()
    invalid = {
        name: [model.__module__ for model in models.get(name, [])]
        for name in _registered_schema_names()
        if len(models.get(name, [])) != 1
    }
    assert invalid == {}, (
        "response_schema_name은 유일한 ai.contracts BaseModel이어야 한다: "
        f"{invalid}"
    )
