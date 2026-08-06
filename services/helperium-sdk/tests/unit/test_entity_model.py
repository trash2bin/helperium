"""Тесты `Entity` — generic-модели для data-service.

Проверяет:
- минимальный payload (только id)
- extra-поля сохраняются (extra='allow')
- доступ к полям через атрибуты (entity.name, entity.full_name)
"""

from __future__ import annotations

from helperium_sdk.models import Entity


def test_minimal_payload() -> None:
    """Entity принимает payload только с id."""
    entity = Entity(id="abc")
    assert entity.id == "abc"


def test_extra_fields_stored() -> None:
    """Entity сохраняет и отдаёт extra-поля."""
    entity = Entity(id="1", name="Math", description="Calculus")
    assert entity.id == "1"
    assert entity.name == "Math"
    assert entity.description == "Calculus"


def test_extra_not_required() -> None:
    """Entity не требует extra-поля (только id обязателен)."""
    entity = Entity(id="x")
    assert entity.id == "x"


def test_model_dump_includes_extras() -> None:
    """model_dump() включает extra-поля."""
    entity = Entity(id="42", full_name="Ivan Petrov", course=3)
    dumped = entity.model_dump(mode="json")
    assert dumped["id"] == "42"
    assert dumped["full_name"] == "Ivan Petrov"
    assert dumped["course"] == 3


def test_nested_object() -> None:
    """Entity может содержать вложенный dict."""
    entity = Entity(id="g1", name="Group A", speciality="CS")
    assert entity.speciality == "CS"
    assert entity.name == "Group A"


def test_student_shape() -> None:
    """Entity с полями студента (как приходит из data-service)."""
    entity = Entity(
        id="s1",
        full_name="Alice",
        group={"id": "g1", "name": "IVT-21", "speciality": "CS"},
        course=3,
    )
    assert entity.full_name == "Alice"
    assert entity.group["id"] == "g1"
    assert entity.course == 3


def test_grade_shape() -> None:
    """Entity с полями оценки."""
    entity = Entity(
        id="gr1",
        student_id="s1",
        student_name="Alice",
        discipline_id="d1",
        discipline_name="Math",
        grade="5",
        date="2024-01-15",
    )
    assert entity.student_id == "s1"
    assert entity.grade == "5"
