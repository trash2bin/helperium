from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def load_fixtures(connection: sqlite3.Connection, fixtures_path: Path) -> None:
    if not fixtures_path.exists():
        logger.warning("Fixtures file not found: %s", fixtures_path)
        return

    data = json.loads(fixtures_path.read_text(encoding="utf-8"))

    _insert_many(
        connection,
        "INSERT OR IGNORE INTO groups (id, name, speciality) VALUES (?, ?, ?)",
        (
            (group["id"], group["name"], group["specialty"])
            for group in data.get("groups", [])
        ),
    )
    _insert_many(
        connection,
        "INSERT OR IGNORE INTO students (id, name, group_id, course) VALUES (?, ?, ?, ?)",
        (
            (student["id"], student["name"], student["group_id"], student["course"])
            for student in data.get("students", [])
        ),
    )
    _insert_many(
        connection,
        "INSERT OR IGNORE INTO teachers (id, name, disciplines_json) VALUES (?, ?, ?)",
        (
            (teacher["id"], teacher["name"], json.dumps(teacher["disciplines"], ensure_ascii=False))
            for teacher in data.get("teachers", [])
        ),
    )
    _insert_many(
        connection,
        "INSERT OR IGNORE INTO disciplines (id, name, description) VALUES (?, ?, ?)",
        (
            (discipline["id"], discipline["name"], discipline["description"])
            for discipline in data.get("disciplines", [])
        ),
    )
    _insert_many(
        connection,
        "INSERT OR IGNORE INTO materials (id, discipline_id, type, content) VALUES (?, ?, ?, ?)",
        (
            (material["id"], material["discipline_id"], material["type"], material["content"])
            for material in data.get("materials", [])
        ),
    )
    _insert_many(
        connection,
        "INSERT OR IGNORE INTO grades (id, student_id, discipline_id, grade, date) VALUES (?, ?, ?, ?, ?)",
        (
            (grade["id"], grade["student_id"], grade["discipline_id"], str(grade["grade"]), grade["date"])
            for grade in data.get("grades", [])
        ),
    )
    _insert_many(
        connection,
        "INSERT OR IGNORE INTO schedule (id, group_id, day, lessons_json) VALUES (?, ?, ?, ?)",
        (
            (entry["id"], entry["group_id"], entry["day"], json.dumps(entry["lessons"], ensure_ascii=False))
            for entry in data.get("schedule", [])
        ),
    )
    connection.commit()


def _insert_many(connection: sqlite3.Connection, sql: str, rows: Any) -> None:
    connection.executemany(sql, rows)
