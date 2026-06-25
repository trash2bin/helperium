-- DDL для university DB. Используется:
--   1. data-service --seed (через embed.FS в Go)
--   2. Go-тестами через schema.sql переменную
--
-- Диалект — общий для SQLite (3.24+) и PostgreSQL:
--   CREATE TABLE IF NOT EXISTS
--   TEXT / INTEGER — общие типы
--   FOREIGN KEY (id) REFERENCES — обе БД
--
-- ВНИМАНИЕ: на реальной prod-БД вуза этот DDL НЕ применяется
-- (таблицы там уже есть, data-service работает read-only).

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