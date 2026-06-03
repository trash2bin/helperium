from __future__ import annotations

import sqlite3


SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS groups (
        id TEXT PRIMARY KEY,
        name TEXT,
        speciality TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS students (
        id TEXT PRIMARY KEY,
        name TEXT,
        group_id TEXT,
        course INTEGER,
        FOREIGN KEY (group_id) REFERENCES groups (id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS teachers (
        id TEXT PRIMARY KEY,
        name TEXT,
        disciplines_json TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS disciplines (
        id TEXT PRIMARY KEY,
        name TEXT,
        description TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS materials (
        id TEXT PRIMARY KEY,
        discipline_id TEXT,
        type TEXT,
        content TEXT,
        FOREIGN KEY (discipline_id) REFERENCES disciplines (id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS documents (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        source_path TEXT NOT NULL UNIQUE,
        mime_type TEXT NOT NULL,
        discipline_id TEXT,
        created_at TEXT NOT NULL,
        metadata_json TEXT,
        FOREIGN KEY (discipline_id) REFERENCES disciplines (id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS document_chunks (
        id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL,
        chunk_index INTEGER NOT NULL,
        page INTEGER,
        content TEXT NOT NULL,
        embedding_json TEXT NOT NULL,
        token_count INTEGER NOT NULL,
        FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_document_chunks_document_id
    ON document_chunks (document_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_documents_discipline_id
    ON documents (discipline_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS grades (
        id TEXT PRIMARY KEY,
        student_id TEXT,
        discipline_id TEXT,
        grade TEXT,
        date TEXT,
        FOREIGN KEY (student_id) REFERENCES students (id),
        FOREIGN KEY (discipline_id) REFERENCES disciplines (id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS schedule (
        id TEXT PRIMARY KEY,
        day TEXT,
        group_id TEXT,
        lessons_json TEXT,
        FOREIGN KEY (group_id) REFERENCES groups (id)
    )
    """,
)


def create_schema(connection: sqlite3.Connection) -> None:
    for statement in SCHEMA_STATEMENTS:
        connection.execute(statement)
    connection.commit()
