-- Создание таблиц для тестов (та же схема, что и в production)
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

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source_path TEXT NOT NULL UNIQUE,
    mime_type TEXT NOT NULL,
    discipline_id TEXT,
    created_at TEXT NOT NULL,
    metadata_json TEXT,
    FOREIGN KEY (discipline_id) REFERENCES disciplines (id)
);

-- Группы
INSERT INTO groups (id, name, speciality) VALUES
    ('g1', 'ИВТ-21', 'Информационные системы'),
    ('g2', 'ПМ-22', 'Прикладная математика');

-- Студенты
INSERT INTO students (id, name, group_id, course) VALUES
    ('s1', 'Иван Петров Иванович', 'g1', 2),
    ('s2', 'Мария Сидорова Ивановна', 'g2', 3);

-- Преподаватели
INSERT INTO teachers (id, name, disciplines_json) VALUES
    ('t1', 'Оксана Ниловна Константинова', '["Алгоритмы", "Базы данных"]');

-- Дисциплины
INSERT INTO disciplines (id, name, description) VALUES
    ('d1', 'Алгоритмы и структуры данных', 'Курс по алгоритмам'),
    ('d2', 'Базы данных', 'Курс по SQL и NoSQL'),
    ('d3', 'Математический анализ', 'Высшая математика');

-- Оценки
INSERT INTO grades (id, student_id, discipline_id, grade, date) VALUES
    ('gr1', 's1', 'd1', '5', '2026-01-15'),
    ('gr2', 's1', 'd2', '4', '2026-02-20'),
    ('gr3', 's2', 'd1', '3', '2026-03-10');

-- Расписание
INSERT INTO schedule (id, day, group_id, lessons_json) VALUES
    ('sc1', 'Понедельник', 'g1', '[{"discipline_id":"d1","discipline_name":"Алгоритмы и структуры данных","teacher_name":"Оксана Ниловна Константинова","room":101},{"discipline_id":"d2","discipline_name":"Базы данных","teacher_name":"Оксана Ниловна Константинова","room":202}]'),
    ('sc2', 'Вторник', 'g1', '[{"discipline_id":"d3","discipline_name":"Математический анализ","teacher_name":"Другой Преподаватель","room":303}]');
