"""Контрактные Pydantic-модели data-service.

Эти модели — стабильный контракт между data-service и его потребителями.
Поля названы семантически (full_name, value) и НЕ зависят от имён колонок БД.
При смене схемы БД эти модели не меняются — меняется только data-service.

Source of truth:
- Pydantic: эти модели (написаны вручную, соответствуют HTTP-ответам data-service)
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


# Базовый ConfigDict для всех моделей: соответствует JSON Schema `additionalProperties: false`.
# Любое unknown поле в HTTP-ответе data-service будет отвергнуто — это защищает
# от тихих изменений контракта со стороны Go-сервиса.
_BASE_CONFIG = ConfigDict(extra="forbid")


class Group(BaseModel):
    """Учебная группа."""

    model_config = _BASE_CONFIG

    id: str = Field(description="Уникальный идентификатор группы")
    name: str = Field(description="Название группы. Пример: ИВТ-21")
    speciality: str = Field(description="Специальность группы")


class Student(BaseModel):
    """Карточка студента."""

    model_config = _BASE_CONFIG

    id: str = Field(description="Уникальный идентификатор студента")
    full_name: str = Field(description="Полное ФИО студента")
    group: Optional[Group] = Field(
        default=None, description="Группа студента. null если не назначена"
    )
    course: Optional[int] = Field(
        default=None, description="Курс обучения (1–6). null если неизвестен"
    )


class Teacher(BaseModel):
    """Преподаватель."""

    model_config = _BASE_CONFIG

    id: str = Field(description="Уникальный идентификатор преподавателя")
    full_name: str = Field(description="Полное ФИО преподавателя")
    disciplines: List[str] = Field(
        default_factory=list, description="Список названий дисциплин"
    )


class Discipline(BaseModel):
    """Учебная дисциплина."""

    model_config = _BASE_CONFIG

    id: str = Field(description="Уникальный идентификатор дисциплины")
    name: str = Field(description="Название дисциплины")
    description: str = Field(description="Краткое описание")


class Grade(BaseModel):
    """Оценка студента."""

    model_config = _BASE_CONFIG

    id: str = Field(description="Уникальный идентификатор записи")
    student_id: str = Field(description="ID студента")
    student_name: str = Field(default="", description="Имя студента")
    discipline_id: str = Field(description="ID дисциплины")
    discipline_name: str = Field(description="Название дисциплины")
    value: str = Field(
        alias="grade",
        description="Значение оценки: 5, 4, 3, 2, зачёт, незачёт",
    )
    date: str = Field(description="Дата в формате YYYY-MM-DD")


class Lesson(BaseModel):
    """Одно занятие в расписании."""

    model_config = _BASE_CONFIG

    discipline_id: str = Field(description="ID дисциплины")
    discipline_name: str = Field(description="Название дисциплины")
    teacher_name: str = Field(description="ФИО преподавателя")
    room: int = Field(description="Номер аудитории")


class ScheduleEntry(BaseModel):
    """Запись расписания на один день."""

    model_config = _BASE_CONFIG

    id: str = Field(description="Уникальный идентификатор записи")
    group: Optional[Group] = Field(default=None, description="Группа")
    day: str = Field(
        description=(
            "День недели: Понедельник, Вторник, Среда, Четверг, Пятница, Суббота"
        )
    )
    lessons: List[Lesson] = Field(default_factory=list, description="Список занятий")