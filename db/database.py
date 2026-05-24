import sqlite3
import json
from pathlib import Path
from typing import List, Dict, Any
from .models import Student, Discipline, Material, ScheduleEntry

class Database:
    def __init__(self, db_path: str = "university.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.create_tables()
        self.load_fixtures()

    def create_tables(self):
        cursor = self.conn.cursor()

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id TEXT PRIMARY KEY,
            name TEXT,
            group_name TEXT,
            course INTEGER,
            specialty TEXT
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
        CREATE TABLE IF NOT EXISTS schedule (
            id TEXT PRIMARY KEY,
            group_name TEXT,
            day TEXT,
            time TEXT,
            discipline_id TEXT,
            room INTEGER,
            FOREIGN KEY (discipline_id) REFERENCES disciplines (id)
        )
        """)

        self.conn.commit()

    def load_fixtures(self):
        fixtures_path = Path(__file__).parent.parent / "fixtures.json"
        if fixtures_path.exists():
            with open(fixtures_path, "r") as f:
                data = json.load(f)

            cursor = self.conn.cursor()

            # Load students
            for student in data["students"]:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO students (id, name, group_name, course, specialty)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (student["id"], student["name"], student["group"], student["course"], student["specialty"])
                )

            # Load disciplines
            for discipline in data["disciplines"]:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO disciplines (id, name, description)
                    VALUES (?, ?, ?)
                    """,
                    (discipline["id"], discipline["name"], discipline["description"])
                )

            # Load materials
            for material in data["materials"]:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO materials (id, discipline_id, type, content)
                    VALUES (?, ?, ?, ?)
                    """,
                    (material["id"], material["discipline_id"], material["type"], material["content"])
                )

            # Load schedule
            for entry in data["schedule"]:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO schedule (id, group_name, day, time, discipline_id, room)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (entry["id"], entry["group"], entry["day"], entry["time"], entry["discipline_id"], entry["room"])
                )

            self.conn.commit()

    def get_student(self, student_id: str) -> Student | None:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM students WHERE id = ?", (student_id,))
        row = cursor.fetchone()
        if row:
            return Student(
                id=row[0],
                name=row[1],
                group=row[2],
                course=row[3],
                specialty=row[4]
            )
        return None

    def get_id_student(self, name: str) -> Student | None:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM students WHERE name = ?", (name,))
        row = cursor.fetchone()
        if row:
            return Student(
                id=row[0],
                name=row[1],
                group=row[2],
                course=row[3],
                specialty=row[4]
            )
        return None

    def get_schedule(self, group_id: str, week: str| None = None) -> List[ScheduleEntry]:
        cursor = self.conn.cursor()
        if week:
            cursor.execute("SELECT * FROM schedule WHERE group_name = ? AND day = ?", (group_id, week))
        else:
            cursor.execute("SELECT * FROM schedule WHERE group_name = ?", (group_id,))
        rows = cursor.fetchall()
        return [
            ScheduleEntry(
                id=row[0],
                group=row[1],
                day=row[2],
                time=row[3],
                discipline_id=row[4],
                room=row[5]
            )
            for row in rows
        ]

    def get_disciplines(self, student_id: str) -> List[Discipline]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT DISTINCT d.* FROM disciplines d
            JOIN schedule s ON d.id = s.discipline_id
            JOIN students st ON s.group_name = st.group_name
            WHERE st.id = ?
            """,
            (student_id,)
        )
        rows = cursor.fetchall()
        return [
            Discipline(
                id=row[0],
                name=row[1],
                description=row[2]
            )
            for row in rows
        ]

    def get_materials(self, discipline_id: str, material_type: str | None = None) -> List[Material]:
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
                id=row[0],
                discipline_id=row[1],
                type=row[2],
                content=row[3]
            )
            for row in rows
        ]

    def search_materials(self, query: str, discipline_id: str | None = None) -> List[Material]:
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
                id=row[0],
                discipline_id=row[1],
                type=row[2],
                content=row[3]
            )
            for row in rows
        ]

    def close(self):
        self.conn.close()
