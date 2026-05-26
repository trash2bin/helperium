import os
import sys
import sqlite3
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from .models import Student, Discipline, Material, ScheduleEntry, Lesson, Grade, Teacher, Group

PROJECT_ROOT = Path(__file__).parent.parent

class Database:
    def __init__(self, db_path: str | None = None):
        if db_path is None:
            db_path = os.environ.get(
                "DB_PATH",
                str(PROJECT_ROOT / "university.db")
            )
        self.db_path = db_path
        self.conn: sqlite3.Connection = sqlite3.connect(
            db_path,
            check_same_thread=False
        )
        self.conn.row_factory = sqlite3.Row
        self.create_tables()
        self.load_fixtures()
        self._closed = False

    def create_tables(self):
        cursor = self.conn.cursor()

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            id TEXT PRIMARY KEY,
            name TEXT,
            speciality TEXT
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id TEXT PRIMARY KEY,
            name TEXT,
            group_id TEXT,
            course INTEGER,
            FOREIGN KEY (group_id) REFERENCES groups (id)
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS teachers (
            id TEXT PRIMARY KEY,
            name TEXT,
            disciplines_json TEXT
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS disciplines (
            id TEXT PRIMARY KEY,
            name TEXT,
            description TEXT
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS materials (
            id TEXT PRIMARY KEY,
            discipline_id TEXT,
            type TEXT,
            content TEXT,
            FOREIGN KEY (discipline_id) REFERENCES disciplines (id)
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS grades (
            id TEXT PRIMARY KEY,
            student_id TEXT,
            discipline_id TEXT,
            grade TEXT,
            date TEXT,
            FOREIGN KEY (student_id) REFERENCES students (id),
            FOREIGN KEY (discipline_id) REFERENCES disciplines (id)
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS schedule (
            id TEXT PRIMARY KEY,
            day TEXT,
            group_id TEXT,
            lessons_json TEXT,
            FOREIGN KEY (group_id) REFERENCES groups (id)
        )
        """)

        self.conn.commit()

    def load_fixtures(self):
        fixtures_path = Path(__file__).parent.parent / "fixtures.json"
        print(f"[DB] Looking for fixtures at: {fixtures_path}", file=sys.stderr)
        print(f"[DB] Exists: {fixtures_path.exists()}", file=sys.stderr)

        if not fixtures_path.exists():
            print(f"[DB] FIXTURES NOT FOUND at {fixtures_path}", file=sys.stderr)
            return

        with open(fixtures_path, "r", encoding='utf-8') as f:
            data = json.load(f)

        cursor = self.conn.cursor()

        # Load groups
        for group in data.get("groups", []):
            cursor.execute(
                "INSERT OR IGNORE INTO groups (id, name, speciality) VALUES (?, ?, ?)",
                (group["id"], group["name"], group["specialty"])
            )

        # Load students
        for student in data.get("students", []):
            cursor.execute(
                "INSERT OR IGNORE INTO students (id, name, group_id, course) VALUES (?, ?, ?, ?)",
                (student["id"], student["name"], student["group_id"], student["course"])
            )

        # Load teachers
        for teacher in data.get("teachers", []):
            cursor.execute(
                "INSERT OR IGNORE INTO teachers (id, name, disciplines_json) VALUES (?, ?, ?)",
                (teacher["id"], teacher["name"], json.dumps(teacher["disciplines"], ensure_ascii=False))
            )

        # Load disciplines
        for discipline in data.get("disciplines", []):
            cursor.execute(
                "INSERT OR IGNORE INTO disciplines (id, name, description) VALUES (?, ?, ?)",
                (discipline["id"], discipline["name"], discipline["description"])
            )

        # Load materials
        for material in data.get("materials", []):
            cursor.execute(
                "INSERT OR IGNORE INTO materials (id, discipline_id, type, content) VALUES (?, ?, ?, ?)",
                (material["id"], material["discipline_id"], material["type"], material["content"])
            )

        # Load grades
        for grade in data.get("grades", []):
            cursor.execute(
                "INSERT OR IGNORE INTO grades (id, student_id, discipline_id, grade, date) VALUES (?, ?, ?, ?, ?)",
                (grade["id"], grade["student_id"], grade["discipline_id"], str(grade["grade"]), grade["date"])
            )

        # Load schedule
        for entry in data.get("schedule", []):
            cursor.execute(
                "INSERT OR IGNORE INTO schedule (id, group_id, day, lessons_json) VALUES (?, ?, ?, ?)",
                (entry["id"], entry["group_id"], entry["day"], json.dumps(entry["lessons"], ensure_ascii=False))
            )

        self.conn.commit()
        print("[DB] Fixtures loaded successfully.")

    def get_group(self, group_id: str) -> Group | None:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM groups WHERE id = ?", (group_id,))
        row = cursor.fetchone()
        if row:
            return Group(
                id=row["id"],
                name=row["name"],
                speciality=row["speciality"],
            )
        return None

    def get_student(self, student_id: str) -> Optional[Student]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM students WHERE id = ?", (student_id,))
        row = cursor.fetchone()
        if row:
            return Student(
                id=row["id"],
                name=row["name"],
                group=self.get_group(row["group_id"]),
                course=row["course"],
            )
        return None

    def get_id_student(self, name: str | None) -> Optional[Student]:
        if name is None:
            return None

        cursor = self.conn.cursor()
        cursor.execute("SELECT id FROM students WHERE name = ?", (name,))
        row = cursor.fetchone()
        if row:
            return Student(
                id=row["id"],
                name=row["name"],
                group=self.get_group(row["group_id"]),
                course=row["course"],
            )
        return None

    def get_teacher_by_name(self, name: str) -> Optional[Teacher]:
        """Поиск учителя по имени"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM teachers WHERE name = ?", (name,))
        row = cursor.fetchone()
        if row:
            return Teacher(
                id=row["id"],
                name=row["name"],
                disciplines=json.loads(row["disciplines_json"])
            )
        return None

    def get_teacher_schedule(self, teacher_name: str, day: Optional[str] = None) -> List[ScheduleEntry]:
        """
        Получает расписание преподавателя.
        Так как расписание нормализовано по группам, нам нужно пройти по всем записям
        и найти те, где в lessons_json упоминается этот учитель.
        """
        cursor = self.conn.cursor()

        # Сначала найдем ID учителя, если он есть в базе
        teacher = self.get_teacher_by_name(teacher_name)
        if not teacher:
            return []

        query = "SELECT * FROM schedule"
        params = []

        if day:
            query += " WHERE day = ?"
            params.append(day)

        cursor.execute(query, params)
        rows = cursor.fetchall()

        result_schedule: List[ScheduleEntry] = []

        for row in rows:
            lessons_data = json.loads(row["lessons_json"])
            # Фильтруем уроки, которые ведет этот учитель
            teacher_lessons: List[Lesson] = [l for l in lessons_data if l.get("teacher_name") == teacher_name]

            if teacher_lessons:
                result_schedule.append(ScheduleEntry(
                    id=row["id"],
                    group=self.get_group(row["group_id"]),
                    day=row["day"],
                    lessons=teacher_lessons
                ))

        return result_schedule

    def get_schedule(self, group_id: str, day: Optional[str] = None) -> List[ScheduleEntry]:
        cursor = self.conn.cursor()

        if day:
            cursor.execute(
                "SELECT * FROM schedule WHERE group_id = ? AND day = ?",
                (group_id, day)
            )
        else:
            cursor.execute(
                "SELECT * FROM schedule WHERE group_id = ?",
                (group_id,)
            )

        rows = cursor.fetchall()
        result = []

        for row in rows:
            raw_lessons = json.loads(row["lessons_json"])
            lessons = []
            for lesson in raw_lessons:
                lessons.append(
                    Lesson(
                        discipline_id=lesson["discipline_id"],
                        discipline_name=lesson.get("discipline_name", "Неизвестно"),
                        teacher_name=lesson["teacher_name"],
                        room=lesson["room"],
                    )
                )
            result.append(
                ScheduleEntry(
                    id=row["id"],
                    group=self.get_group(row["group_id"]),
                    day=row["day"],
                    lessons=lessons
                )
            )
        return result

    def get_disciplines(self, student_id: str) -> List[Discipline]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT group_id FROM students WHERE id = ?", (student_id,))
        row = cursor.fetchone()
        if not row:
            return []
        group_id = row["group_id"]

        cursor.execute("SELECT lessons_json FROM schedule WHERE group_id = ?", (group_id,))
        rows = cursor.fetchall()

        discipline_ids = set()
        for row in rows:
            lessons = json.loads(row["lessons_json"])
            for lesson in lessons:
                discipline_ids.add(lesson["discipline_id"])

        if not discipline_ids:
            return []

        placeholders = ", ".join("?" * len(discipline_ids))
        cursor.execute(
            f"SELECT * FROM disciplines WHERE id IN ({placeholders})",
            list(discipline_ids)
        )
        return [
            Discipline(
                id=row["id"],
                name=row["name"],
                description=row["description"]
            )
            for row in cursor.fetchall()
        ]

    def get_materials(self, discipline_id: str, material_type: Optional[str] = None) -> List[Material]:
        cursor = self.conn.cursor()
        if material_type:
            cursor.execute(
                "SELECT * FROM materials WHERE discipline_id = ? AND type = ?",
                (discipline_id, material_type)
            )
        else:
            cursor.execute("SELECT * FROM materials WHERE discipline_id = ?", (discipline_id,))

        rows = cursor.fetchall()
        return [
            Material(
                id=row["id"],
                discipline_id=row["discipline_id"],
                type=row["type"],
                content=row["content"]
            )
            for row in rows
        ]

    def search_materials(self, query: str, discipline_id: Optional[str] = None) -> List[Material]:
        cursor = self.conn.cursor()
        if discipline_id:
            cursor.execute(
                "SELECT * FROM materials WHERE discipline_id = ? AND content LIKE ?",
                (discipline_id, f"%{query}%")
            )
        else:
            cursor.execute("SELECT * FROM materials WHERE content LIKE ?", (f"%{query}%",))

        rows = cursor.fetchall()
        return [
            Material(
                id=row["id"],
                discipline_id=row["discipline_id"],
                type=row["type"],
                content=row["content"]
            )
            for row in rows
        ]

    def get_student_grades(self, student_id: str, discipline_id: Optional[str] = None) -> List[Grade]:
        cursor = self.conn.cursor()
        query = """
            SELECT
                grades.id,
                grades.student_id,
                grades.discipline_id,
                disciplines.name AS discipline_name,
                grades.grade,
                grades.date
            FROM grades
            LEFT JOIN disciplines ON disciplines.id = grades.discipline_id
            WHERE grades.student_id = ?
        """
        params: list = [student_id]

        if discipline_id:
            query += " AND grades.discipline_id = ?"
            params.append(discipline_id)

        query += " ORDER BY grades.date DESC, disciplines.name ASC"

        cursor.execute(query, params)
        rows = cursor.fetchall()

        return [
            Grade(
                id=row["id"],
                student_id=row["student_id"],
                discipline_id=row["discipline_id"],
                discipline_name=row["discipline_name"] or "Неизвестная дисциплина",
                grade=row["grade"],
                date=row["date"],
            )
            for row in rows
        ]

    def close(self):
        if not self._closed:
            self.conn.close()
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
