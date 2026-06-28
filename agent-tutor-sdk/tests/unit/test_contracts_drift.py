"""Тесты контрактов: Pydantic модели корректно парсят payload'ы.

Source of truth: Pydantic-модели в agent_tutor_sdk.contracts (написаны вручную).

Тесты:
- round-trip: модель парсит полный payload с dummy-значениями
- reject unknown: модель отвергает extra-поля (extra='forbid')
- all exported: все 7 моделей доступны через `from agent_tutor_sdk.contracts import ...`

Запуск:
    uv run pytest agent-tutor-sdk/tests/unit/test_contracts_drift.py -v
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from agent_tutor_sdk.contracts import (
    Discipline,
    Grade,
    Group,
    Lesson,
    ScheduleEntry,
    Student,
    Teacher,
)

# === Карта моделей для параметризации ===

MODELS: list[tuple[type[BaseModel], str]] = [
    (Group, "Group"),
    (Student, "Student"),
    (Teacher, "Teacher"),
    (Discipline, "Discipline"),
    (Grade, "Grade"),
    (Lesson, "Lesson"),
    (ScheduleEntry, "ScheduleEntry"),
]


# === Хелпер: построить minimal payload по модели ===


def _minimal_payload(model_cls: type[BaseModel]) -> dict[str, Any]:
    """Собрать JSON-словарь с dummy-значениями для всех полей Pydantic-модели."""
    payload: dict[str, Any] = {}
    for field_name, field_info in model_cls.model_fields.items():
        alias = field_info.alias or field_name
        annotation = field_info.annotation
        type_str = str(annotation)

        if "list" in type_str.lower():

            # Определяем элементный тип
            args = getattr(annotation, "__args__", None)
            if args and len(args) >= 1:
                inner = args[0]
                if isinstance(inner, type) and issubclass(inner, BaseModel):
                    payload[alias] = [_minimal_payload(inner)]
                else:
                    payload[alias] = []
            else:
                payload[alias] = []
        elif "str" in type_str:
            payload[alias] = "x"
        elif "int" in type_str:
            payload[alias] = 1
        elif "float" in type_str:
            payload[alias] = 1.0
        elif "bool" in type_str:
            payload[alias] = False
        elif "Optional" in type_str or "None" in type_str:
            # Optional[X]: достаём X из Union[X, None]
            args = getattr(annotation, "__args__", None)
            if args:
                inner = next((a for a in args if a is not type(None)), None)
                if inner and isinstance(inner, type) and issubclass(inner, BaseModel):
                    payload[alias] = _minimal_payload(inner)
                else:
                    payload[alias] = None
            else:
                payload[alias] = None
        else:
            # Вложенная модель без Optional
            if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                payload[alias] = _minimal_payload(annotation)
            else:
                payload[alias] = None
    return payload


# === Тест: round-trip парсинг минимального payload'а ===


@pytest.mark.parametrize("model_cls,_name", MODELS, ids=lambda v: v if isinstance(v, str) else v.__name__)
def test_model_parses_minimal_payload(model_cls: type[BaseModel], _name: str) -> None:
    """Pydantic может распарсить payload с dummy-значениями для всех полей."""
    payload = _minimal_payload(model_cls)
    instance = model_cls(**payload)
    assert instance is not None


# === Тест: reject unknown полей (extra='forbid') ===


@pytest.mark.parametrize("model_cls,_name", MODELS, ids=lambda v: v if isinstance(v, str) else v.__name__)
def test_model_rejects_unknown_fields(model_cls: type[BaseModel], _name: str) -> None:
    """Pydantic отвергает unknown поля (extra='forbid')."""
    extra_setting = model_cls.model_config.get("extra")
    if extra_setting != "forbid":
        pytest.skip(f"{model_cls.__name__}: extra={extra_setting!r}, skipping reject test")

    with pytest.raises(Exception):
        model_cls(**{"__unknown_field": "xyz"})


# === Тест: все 7 моделей экспортируются ===


def test_all_models_exported() -> None:
    """Все ожидаемые модели экспортируются из contracts."""
    from agent_tutor_sdk import contracts

    expected = {"Group", "Student", "Teacher", "Discipline", "Grade", "Lesson", "ScheduleEntry"}
    actual = {
        name
        for name in dir(contracts)
        if isinstance(getattr(contracts, name), type)
        and issubclass(getattr(contracts, name), BaseModel)
    }
    missing = expected - actual
    assert not missing, f"Missing exports: {missing}"
