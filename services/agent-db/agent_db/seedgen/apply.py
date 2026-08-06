"""Apply seed data and DDL to a SQLite or PostgreSQL database.

Replicates data-service/internal/seedgen/seedgen.go (Apply, ApplyWithDDL,
insert helpers) in Python. All INSERTs use idempotent syntax
(INSERT OR IGNORE for SQLite, ON CONFLICT DO NOTHING for Postgres).
"""

from __future__ import annotations

import json as _json
import logging
from typing import Any, Callable, Optional, Protocol

from . import models

logger = logging.getLogger(__name__)


# ── Placeholder helpers ──


def _sqlite_placeholder(_: int) -> str:
    return "?"


def _postgres_placeholder(index: int) -> str:
    return f"${index}"


# ── INSERT builder ──


def _build_insert(
    table: str, cols: list[str], driver: str, ph_fn: Callable[[int], str]
) -> str:
    quoted = [f'"{c}"' for c in cols]
    phs = [ph_fn(i) for i in range(1, len(cols) + 1)]
    values = ", ".join(phs)

    if driver == "sqlite":
        prefix = "INSERT OR IGNORE"
        suffix = ""
    else:
        prefix = "INSERT"
        suffix = ' ON CONFLICT ("id") DO NOTHING'

    return f'{prefix} INTO "{table}" ({", ".join(quoted)}) VALUES ({values}){suffix}'


# ── Connection abstraction ──


class DBConnection(Protocol):
    def execute(self, sql: str, params: tuple = ()) -> Any: ...
    def executescript(self, script: str) -> None: ...
    def close(self) -> None: ...
    def commit(self) -> None: ...


# ── Seed data inserters ──


def _insert_groups(
    conn: DBConnection,
    groups: list[models.Group],
    driver: str,
    ph_fn: Callable[[int], str],
) -> None:
    if not groups:
        return
    q = _build_insert("groups", ["id", "name", "speciality"], driver, ph_fn)
    for g in groups:
        conn.execute(q, (g.id, g.name, g.speciality))


def _insert_disciplines(
    conn: DBConnection,
    disciplines: list[models.Discipline],
    driver: str,
    ph_fn: Callable[[int], str],
) -> None:
    if not disciplines:
        return
    q = _build_insert("disciplines", ["id", "name", "description"], driver, ph_fn)
    for d in disciplines:
        conn.execute(q, (d.id, d.name, d.description))


def _insert_teachers(
    conn: DBConnection,
    teachers: list[models.Teacher],
    driver: str,
    ph_fn: Callable[[int], str],
) -> None:
    if not teachers:
        return
    q = _build_insert("teachers", ["id", "name", "disciplines_json"], driver, ph_fn)
    for t in teachers:
        disc_json = _json.dumps(t.disciplines, ensure_ascii=False)
        conn.execute(q, (t.id, t.name, disc_json))


def _insert_students(
    conn: DBConnection,
    students: list[models.Student],
    driver: str,
    ph_fn: Callable[[int], str],
) -> None:
    if not students:
        return
    q = _build_insert("students", ["id", "name", "group_id", "course"], driver, ph_fn)
    for s in students:
        conn.execute(q, (s.id, s.name, s.group_id, s.course))


def _insert_schedule(
    conn: DBConnection,
    schedule: list[models.ScheduleEntry],
    driver: str,
    ph_fn: Callable[[int], str],
) -> None:
    if not schedule:
        return
    q = _build_insert(
        "schedule", ["id", "day", "group_id", "lessons_json"], driver, ph_fn
    )
    for e in schedule:
        lessons_json = _json.dumps(
            [_lesson_to_dict(les) for les in e.lessons], ensure_ascii=False
        )
        conn.execute(q, (e.id, e.day, e.group_id, lessons_json))


def _lesson_to_dict(lesson: models.Lesson) -> dict:
    return {
        "discipline_id": lesson.discipline_id,
        "discipline_name": lesson.discipline_name,
        "teacher_name": lesson.teacher_name,
        "type": lesson.type,
        "room": lesson.room,
        "time_slot": lesson.time_slot,
        "week_type": lesson.week_type,
    }


def _insert_grades(
    conn: DBConnection,
    grades: list[models.Grade],
    driver: str,
    ph_fn: Callable[[int], str],
) -> None:
    if not grades:
        return
    q = _build_insert(
        "grades", ["id", "student_id", "discipline_id", "grade", "date"], driver, ph_fn
    )
    for g in grades:
        conn.execute(q, (g.id, g.student_id, g.discipline_id, g.grade, g.date))


# ── DDL splitting ──


def _split_ddl(ddl: str) -> list[str]:
    """Split multi-statement DDL into individual statements by ';'."""
    out: list[str] = []
    for stmt in ddl.split(";"):
        stmt = stmt.strip()
        if stmt:
            out.append(stmt)
    return out


# ── Public API ──


def apply_with_ddl(
    conn: DBConnection,
    ddl: str = "",
    seed: Optional[models.Seed] = None,
    driver: str = "sqlite",
) -> None:
    """Apply DDL and/or seed data to a database connection.

    Steps:
    1. Split and execute DDL statements.
    2. Insert seed data in FK order: groups → disciplines → teachers → students → schedule → grades.

    All INSERTs use idempotent syntax (INSERT OR IGNORE / ON CONFLICT DO NOTHING).
    """
    ph_fn = _sqlite_placeholder if driver == "sqlite" else _postgres_placeholder

    if ddl:
        for stmt in _split_ddl(ddl):
            if stmt:
                conn.execute(stmt)
        logger.info("DDL applied (%d statements)", len(_split_ddl(ddl)))

    if seed is None:
        seed = models.Seed()  # type: ignore[call-arg]

    _insert_groups(conn, seed.groups, driver, ph_fn)
    _insert_disciplines(conn, seed.disciplines, driver, ph_fn)
    _insert_teachers(conn, seed.teachers, driver, ph_fn)
    _insert_students(conn, seed.students, driver, ph_fn)
    _insert_schedule(conn, seed.schedule, driver, ph_fn)
    _insert_grades(conn, seed.grades, driver, ph_fn)

    logger.info(
        "Seed applied: %d groups, %d students, %d teachers, %d disciplines, %d schedule, %d grades",
        len(seed.groups),
        len(seed.students),
        len(seed.teachers),
        len(seed.disciplines),
        len(seed.schedule),
        len(seed.grades),
    )


def apply(
    conn: DBConnection,
    seed: models.Seed,
    driver: str = "sqlite",
) -> None:
    """Apply TestSeed-style data with the standard university DDL schema.

    Wraps apply_with_ddl with the embedded university DDL.
    """
    ddl = _UNIVERSITY_DDL
    apply_with_ddl(conn, ddl=ddl, seed=seed, driver=driver)


# ── Embedded DDL ──

_UNIVERSITY_DDL = """
CREATE TABLE IF NOT EXISTS groups (
    id TEXT PRIMARY KEY,
    name TEXT,
    speciality TEXT
);
CREATE TABLE IF NOT EXISTS students (
    id TEXT PRIMARY KEY,
    name TEXT,
    group_id TEXT,
    course INTEGER,
    FOREIGN KEY (group_id) REFERENCES groups (id)
);
CREATE TABLE IF NOT EXISTS teachers (
    id TEXT PRIMARY KEY,
    name TEXT,
    disciplines_json TEXT
);
CREATE TABLE IF NOT EXISTS disciplines (
    id TEXT PRIMARY KEY,
    name TEXT,
    description TEXT
);
CREATE TABLE IF NOT EXISTS grades (
    id TEXT PRIMARY KEY,
    student_id TEXT,
    discipline_id TEXT,
    grade TEXT,
    date TEXT,
    FOREIGN KEY (student_id) REFERENCES students (id),
    FOREIGN KEY (discipline_id) REFERENCES disciplines (id)
);
CREATE TABLE IF NOT EXISTS schedule (
    id TEXT PRIMARY KEY,
    day TEXT,
    group_id TEXT,
    lessons_json TEXT,
    FOREIGN KEY (group_id) REFERENCES groups (id)
);
"""
