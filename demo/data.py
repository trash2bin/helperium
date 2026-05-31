from __future__ import annotations

import json
import sqlite3
from typing import Any

from db.database import Database


class DemoDataRepository:
    def __init__(self) -> None:
        self.db = Database()

    def overview(self) -> dict[str, Any]:
        conn = self.db.conn
        return {
            "stats": self._stats(conn),
            "students": self._students(conn),
            "teachers": self._teachers(conn),
            "disciplines": self._disciplines(conn),
            "schedule": self._schedule(conn),
            "documents": self._documents(conn),
            "grades": self._grades(conn),
        }

    def _stats(self, conn: sqlite3.Connection) -> dict[str, int]:
        names = ["students", "teachers", "disciplines", "documents", "grades", "schedule"]
        return {name: self._count(conn, name) for name in names}

    @staticmethod
    def _count(conn: sqlite3.Connection, table: str) -> int:
        return int(conn.execute(f"SELECT COUNT(*) AS total FROM {table}").fetchone()["total"])

    @staticmethod
    def _students(conn: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT students.id, students.name, students.course,
                   groups.name AS group_name, groups.speciality
            FROM students
            LEFT JOIN groups ON groups.id = students.group_id
            ORDER BY groups.name, students.name
            """
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _teachers(conn: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = conn.execute("SELECT id, name, disciplines_json FROM teachers ORDER BY name").fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "disciplines": json.loads(row["disciplines_json"] or "[]"),
            }
            for row in rows
        ]

    @staticmethod
    def _disciplines(conn: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = conn.execute("SELECT id, name, description FROM disciplines ORDER BY name").fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _schedule(conn: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT schedule.id, schedule.day, groups.name AS group_name, schedule.lessons_json
            FROM schedule
            LEFT JOIN groups ON groups.id = schedule.group_id
            ORDER BY groups.name, schedule.day
            """
        ).fetchall()
        return [
            {
                "id": row["id"],
                "day": row["day"],
                "group_name": row["group_name"],
                "lessons": json.loads(row["lessons_json"] or "[]"),
            }
            for row in rows
        ]

    @staticmethod
    def _documents(conn: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT documents.id, documents.title, documents.source_path,
                   documents.mime_type, documents.discipline_id,
                   disciplines.name AS discipline_name, documents.created_at
            FROM documents
            LEFT JOIN disciplines ON disciplines.id = documents.discipline_id
            ORDER BY documents.created_at DESC
            LIMIT 40
            """
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _grades(conn: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT grades.id, students.name AS student_name,
                   disciplines.name AS discipline_name, grades.grade, grades.date
            FROM grades
            LEFT JOIN students ON students.id = grades.student_id
            LEFT JOIN disciplines ON disciplines.id = grades.discipline_id
            ORDER BY grades.date DESC
            LIMIT 80
            """
        ).fetchall()
        return [dict(row) for row in rows]
