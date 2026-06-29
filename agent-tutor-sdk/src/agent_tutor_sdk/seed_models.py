"""Storage-shape Pydantic-модели для seed.json.

Эти модели описывают НОРМАЛИЗОВАННУЮ форму данных, которая пишется в seed.json
и читается data-service --seed. Это НЕ API-форма — API возвращает
Entity (generic-запись поля → значение через data-service).

Разница между storage и API формами (намеренная):
- Student.storage: {id, name, group_id, course}     // FK, нормализовано
- Student.api:    {id, full_name, group, course}   // денормализовано через JOIN

- Teacher.storage: {id, name, disciplines: [str]}   // name строкой
- Teacher.api:    {id, full_name, disciplines: [str]} // full_name

- ScheduleEntry.storage: {id, group_id, day, lessons: [...]}  // FK
- ScheduleEntry.api:    {id, group, day, lessons: [...]}     // объект

- Lesson.storage: +type, +time_slot, +week_type (доп. поля расписания)
- Lesson.api:    без этих полей (хранятся в БД, но не выдаются)

- Grade.storage: {id, student_id, discipline_id, grade, date}  // FK
- Grade.api:    +student_name, +discipline_name (денормализация через JOIN)

Эти модели используются:
1. seedgen.py: validate сгенерированный dict перед записью в seed.json
2. data-service: читает тот же JSON (Go-структура в internal/seedgen/seedgen.go)

Если Pydantic-валидация в seedgen падает — это drift между Go и Python.
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, ConfigDict, Field


_BASE_CONFIG = ConfigDict(extra="forbid")


class StorageGroup(BaseModel):
    """Storage-форма учебной группы. Совпадает с API-формой (нет денормализации)."""

    model_config = _BASE_CONFIG

    id: str = Field(description="UUID")
    name: str = Field(description="Название группы. Пример: ИВТ-21")
    speciality: str = Field(description="Специальность группы")


class StorageStudent(BaseModel):
    """Storage-форма студента. name вместо full_name, group_id FK вместо group-объекта."""

    model_config = _BASE_CONFIG

    id: str = Field(description="UUID")
    name: str = Field(description="ФИО студента. В API это full_name (денормализация).")
    group_id: str = Field(description="FK на groups.id")
    course: int = Field(description="Курс (1-6)")


class StorageTeacher(BaseModel):
    """Storage-форма преподавателя. name вместо full_name."""

    model_config = _BASE_CONFIG

    id: str = Field(description="UUID")
    name: str = Field(description="ФИО преподавателя. В API это full_name.")
    disciplines: List[str] = Field(
        default_factory=list,
        description="Список названий дисциплин (не FK — для простоты сидинга)",
    )


class StorageDiscipline(BaseModel):
    """Storage-форма дисциплины. Совпадает с API-формой."""

    model_config = _BASE_CONFIG

    id: str = Field(description="UUID")
    name: str = Field(description="Название дисциплины")
    description: str = Field(description="Краткое описание")


class StorageLesson(BaseModel):
    """Storage-форма занятия. Доп. поля (type, time_slot, week_type) есть только здесь."""

    model_config = _BASE_CONFIG

    discipline_id: str = Field(description="FK на disciplines.id")
    discipline_name: str = Field(description="Денормализованное имя для удобства вывода")
    teacher_name: str = Field(description="ФИО преподавателя")
    room: int = Field(description="Номер аудитории")
    type: str = Field(description="Тип занятия: лекция, практика, лабораторная")
    time_slot: str = Field(description="Слот расписания (например '09:00-10:30')")
    week_type: str = Field(description="Тип недели: верхняя/нижняя/обе")


class StorageScheduleEntry(BaseModel):
    """Storage-форма записи расписания. group_id FK вместо group-объекта."""

    model_config = _BASE_CONFIG

    id: str = Field(description="UUID")
    group_id: str = Field(description="FK на groups.id")
    day: str = Field(description="День недели")
    lessons: List[StorageLesson] = Field(default_factory=list)


class StorageGrade(BaseModel):
    """Storage-форма оценки. Без student_name и discipline_name (есть только в API)."""

    model_config = _BASE_CONFIG

    id: str = Field(description="UUID")
    student_id: str = Field(description="FK на students.id")
    discipline_id: str = Field(description="FK на disciplines.id")
    grade: str = Field(description="Значение: 5, 4, 3, 2, зачёт, незачёт")
    date: str = Field(description="Дата YYYY-MM-DD")


class StorageSeed(BaseModel):
    """Корневая структура seed.json."""

    model_config = _BASE_CONFIG

    groups: List[StorageGroup] = Field(default_factory=list)
    students: List[StorageStudent] = Field(default_factory=list)
    teachers: List[StorageTeacher] = Field(default_factory=list)
    disciplines: List[StorageDiscipline] = Field(default_factory=list)
    schedule: List[StorageScheduleEntry] = Field(default_factory=list)
    grades: List[StorageGrade] = Field(default_factory=list)