#!/usr/bin/env python3
"""
Create sqlite-testseed database schema and populate from seed.json
"""

import os
import json
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent / "data.db"
SEED = Path(__file__).resolve().parent / "seed.json"

DB.unlink(missing_ok=True)
DB.parent.mkdir(parents=True, exist_ok=True)

db = sqlite3.connect(str(DB))
cursor = db.cursor()

# Create tables
cursor.executescript("""
    CREATE TABLE groups (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        speciality TEXT NOT NULL
    );

    CREATE TABLE students (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        group_id TEXT,
        course INTEGER,
        FOREIGN KEY (group_id) REFERENCES groups(id)
    );

    CREATE TABLE teachers (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        disciplines_json TEXT
    );

    CREATE TABLE disciplines (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL
    );

    CREATE TABLE schedule (
        id TEXT PRIMARY KEY,
        group_id TEXT NOT NULL,
        day TEXT NOT NULL,
        lessons_json TEXT,
        FOREIGN KEY (group_id) REFERENCES groups(id)
    );

    CREATE TABLE grades (
        id TEXT PRIMARY KEY,
        student_id TEXT NOT NULL,
        discipline_id TEXT NOT NULL,
        grade TEXT NOT NULL,
        date TEXT NOT NULL,
        FOREIGN KEY (student_id) REFERENCES students(id),
        FOREIGN KEY (discipline_id) REFERENCES disciplines(id)
    );
""")

# Load seed data
with open(SEED, 'r', encoding='utf-8') as f:
    seed = json.load(f)

# Insert groups
for g in seed["groups"]:
    cursor.execute("INSERT INTO groups (id, name, speciality) VALUES (?, ?, ?)",
                   (g["id"], g["name"], g["speciality"]))

# Insert students
for s in seed["students"]:
    cursor.execute("INSERT INTO students (id, name, group_id, course) VALUES (?, ?, ?, ?)",
                   (s["id"], s["name"], s.get("group_id"), s.get("course")))

# Insert teachers
for t in seed["teachers"]:
    disciplines_json = json.dumps(t.get("disciplines", []), ensure_ascii=False)
    cursor.execute("INSERT INTO teachers (id, name, disciplines_json) VALUES (?, ?, ?)",
                   (t["id"], t["name"], disciplines_json))

# Insert disciplines
for d in seed["disciplines"]:
    cursor.execute("INSERT INTO disciplines (id, name, description) VALUES (?, ?, ?)",
                   (d["id"], d["name"], d["description"]))

# Insert schedule
for s in seed["schedule"]:
    lessons_json = json.dumps(s.get("lessons", []), ensure_ascii=False)
    cursor.execute("INSERT INTO schedule (id, group_id, day, lessons_json) VALUES (?, ?, ?, ?)",
                   (s["id"], s["group_id"], s["day"], lessons_json))

# Insert grades
for g in seed["grades"]:
    cursor.execute("INSERT INTO grades (id, student_id, discipline_id, grade, date) VALUES (?, ?, ?, ?, ?)",
                   (g["id"], g["student_id"], g["discipline_id"], g["grade"], g["date"]))

db.commit()

# Verify
print(f"✅ Created sqlite-testseed database: {DB} ({DB.stat().st_size} bytes)")
print(f"   Groups: {cursor.execute('SELECT COUNT(*) FROM groups').fetchone()[0]}")
print(f"   Students: {cursor.execute('SELECT COUNT(*) FROM students').fetchone()[0]}")
print(f"   Teachers: {cursor.execute('SELECT COUNT(*) FROM teachers').fetchone()[0]}")
print(f"   Disciplines: {cursor.execute('SELECT COUNT(*) FROM disciplines').fetchone()[0]}")
print(f"   Schedule: {cursor.execute('SELECT COUNT(*) FROM schedule').fetchone()[0]}")
print(f"   Grades: {cursor.execute('SELECT COUNT(*) FROM grades').fetchone()[0]}")
