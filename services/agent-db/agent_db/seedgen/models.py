"""Domain models for seed generation and DDL creation.

These replicate the subset of helperium-go/config/types.go and
data-service/internal/seedgen/seedgen.go needed for Python-side
seed materialization (dev/e2e only).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from helperium_sdk.seed_models import (
    StorageSeed as Seed,
    StorageGroup as Group,
    StorageStudent as Student,
    StorageTeacher as Teacher,
    StorageDiscipline as Discipline,
    StorageScheduleEntry as ScheduleEntry,
    StorageLesson as Lesson,
    StorageGrade as Grade,
)


# ── FieldType (subset of helperium-go/config/types.go) ──


class FieldType:
    STRING = "string"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    JSON = "json"
    DATETIME = "datetime"
    DATE = "date"

    _ALL = {STRING, INT, FLOAT, BOOL, JSON, DATETIME, DATE}

    @classmethod
    def is_valid(cls, name: str) -> bool:
        return name in cls._ALL


# ── RelationKind ──


class RelationKind:
    MANY_TO_ONE = "many_to_one"
    ONE_TO_MANY = "one_to_many"
    MANY_TO_MANY = "many_to_many"


# ── Config models (used by seedgen) ──


@dataclass
class EntityField:
    name: str
    column: str
    type: str = FieldType.STRING
    nullable: Optional[bool] = None
    primary_key: Optional[bool] = None
    description: str = ""


@dataclass
class Relation:
    field: str
    kind: str = RelationKind.MANY_TO_ONE
    table: str = ""
    local_fk: str = ""
    target_fk: str = ""


@dataclass
class Entity:
    name: str
    table: str
    id_column: str
    fields: list[EntityField] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    description: str = ""


@dataclass
class DataSourceConfig:
    driver: str = "sqlite"  # "sqlite" or "postgres"
    dsn: str = ""


@dataclass
class ScenarioConfig:
    """Top-level config from a scenario's config.json (seedgen-relevant subset)."""

    version: int = 1
    data_source: DataSourceConfig = field(default_factory=DataSourceConfig)
    entities: list[Entity] = field(default_factory=list)


# ── TestSeed (moved from data-service/internal/seedgen/testdata.go) ──

TestSeed = Seed(
    groups=[
        Group(id="g1", name="ИВТ-21", speciality="Информационные системы и технологии"),
        Group(id="g2", name="ПИ-20", speciality="Программная инженерия"),
    ],
    disciplines=[
        Discipline(id="d1", name="Алгоритмы и структуры данных", description="Базы"),
        Discipline(id="d2", name="Базы данных", description="Реляционные"),
        Discipline(id="d3", name="Веб-технологии", description="HTTP"),
    ],
    teachers=[
        Teacher(
            id="t1",
            name="Оксана Ниловна Константинова",
            disciplines=["Базы данных", "Веб-технологии"],
        ),
    ],
    students=[
        Student(id="s1", name="Иван Петров Иванович", group_id="g1", course=2),
        Student(id="s2", name="Мария Сидорова Ивановна", group_id="g2", course=3),
    ],
    schedule=[
        ScheduleEntry(
            id="sch1",
            group_id="g1",
            day="Понедельник",
            lessons=[
                Lesson(
                    discipline_id="d1",
                    discipline_name="Алгоритмы и структуры данных",
                    teacher_name="Оксана Ниловна Константинова",
                    type="Лекция",
                    room=301,
                    time_slot="9:00-10:30",
                    week_type="числитель",
                ),
                Lesson(
                    discipline_id="d2",
                    discipline_name="Базы данных",
                    teacher_name="Оксана Ниловна Константинова",
                    type="Практика",
                    room=205,
                    time_slot="10:45-12:15",
                    week_type="знаменатель",
                ),
            ],
        ),
        ScheduleEntry(
            id="sch2",
            group_id="g1",
            day="Вторник",
            lessons=[
                Lesson(
                    discipline_id="d3",
                    discipline_name="Веб-технологии",
                    teacher_name="Другой Преподаватель",
                    type="Лекция",
                    room=310,
                    time_slot="11:00-12:30",
                    week_type="каждую",
                ),
            ],
        ),
    ],
    grades=[
        Grade(
            id="gr1", student_id="s1", discipline_id="d1", grade="5", date="2026-04-10"
        ),
        Grade(
            id="gr2", student_id="s1", discipline_id="d2", grade="4", date="2026-06-15"
        ),
        Grade(
            id="gr3", student_id="s2", discipline_id="d3", grade="3", date="2026-04-20"
        ),
    ],
)
