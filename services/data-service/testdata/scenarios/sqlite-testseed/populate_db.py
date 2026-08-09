#!/usr/bin/env python3
"""
Populate sqlite-testseed database from seed.json
"""

import os
import json
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent / "data.db"
SEED = Path(__file__).resolve().parent / "seed.json"

# Clear existing data
db = sqlite3.connect(str(DB))
cursor = db.cursor()

# Delete existing data (in correct order due to FKs)
cursor.execute("DELETE FROM grades")
cursor.execute("DELETE FROM schedule")
cursor.execute("DELETE FROM students")
cursor.execute("DELETE FROM disciplines")
cursor.execute("DELETE FROM teachers")
cursor.execute("DELETE FROM groups")
db.commit()

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
print(f"✅ Populated sqlite-testseed database: {DB} ({DB.stat().st_size} bytes)")
print(f"   Groups: {cursor.execute('SELECT COUNT(*) FROM groups').fetchone()[0]}")
print(f"   Students: {cursor.execute('SELECT COUNT(*) FROM students').fetchone()[0]}")
print(f"   Teachers: {cursor.execute('SELECT COUNT(*) FROM teachers').fetchone()[0]}")
print(f"   Disciplines: {cursor.execute('SELECT COUNT(*) FROM disciplines').fetchone()[0]}")
print(f"   Schedule: {cursor.execute('SELECT COUNT(*) FROM schedule').fetchone()[0]}")
print(f"   Grades: {cursor.execute('SELECT COUNT(*) FROM grades').fetchone()[0]}")
