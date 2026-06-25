"""DDL для RAG — только таблицы документов, без FK к университетским таблицам."""

from __future__ import annotations

from typing import Any

RAG_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS documents (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        source_path TEXT NOT NULL UNIQUE,
        mime_type TEXT NOT NULL,
        discipline_id TEXT,
        discipline_name TEXT,
        created_at TEXT NOT NULL,
        metadata_json TEXT
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
        token_count INTEGER NOT NULL
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
)


def create_rag_schema(connection: Any) -> None:
    """Создать таблицы RAG через DBAPI2-совместимое соединение."""
    cursor = connection.cursor()
    for statement in RAG_SCHEMA:
        cursor.execute(statement)
    cursor.close()
    connection.commit()
