"""Репозиторий для работы с SQLite."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from db.database import Database
from db.models import Document, DocumentImportResult

from rag.config import RagConfig
from rag.models import ChunkDict

if TYPE_CHECKING:
    import sqlite3

logger = logging.getLogger(__name__)


class DocumentRepository:
    """CRUD для документов и чанков в SQLite."""

    def __init__(self, db: Database, config: RagConfig) -> None:
        self.db = db
        self.config = config

    def list_documents(self, discipline_id: str | None = None) -> list[Document]:
        """Список документов."""
        cursor = self.db.conn.cursor()
        if discipline_id:
            rows = cursor.execute(
                """
                SELECT id, title, source_path, mime_type, discipline_id, created_at
                FROM documents WHERE discipline_id = ? ORDER BY created_at DESC
                """,
                (discipline_id,),
            ).fetchall()
        else:
            rows = cursor.execute(
                """
                SELECT id, title, source_path, mime_type, discipline_id, created_at
                FROM documents ORDER BY created_at DESC
                """
            ).fetchall()

        return [self._document_from_row(row) for row in rows]

    def find_existing_by_path(self, source_path: str) -> str | None:
        """Найти ID документа по пути."""
        cursor = self.db.conn.cursor()
        row = cursor.execute(
            "SELECT id FROM documents WHERE source_path = ?",
            (source_path,),
        ).fetchone()
        return row["id"] if row else None

    def delete_document(self, cursor: sqlite3.Cursor, document_id: str) -> None:
        """Удалить документ и его чанки из SQLite."""
        cursor.execute("DELETE FROM document_chunks WHERE document_id = ?", (document_id,))
        cursor.execute("DELETE FROM documents WHERE id = ?", (document_id,))

    def insert_document(
        self,
        cursor: sqlite3.Cursor,
        document_id: str,
        title: str,
        source_path: str,
        mime_type: str,
        discipline_id: str | None,
    ) -> str:
        """Вставить документ, вернуть created_at."""
        created_at = datetime.now(timezone.utc).isoformat()
        cursor.execute(
            """
            INSERT INTO documents (
                id, title, source_path, mime_type, discipline_id, created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                title,
                source_path,
                mime_type,
                discipline_id,
                created_at,
                json.dumps(
                    {
                        "vector_store": "chromadb",
                        "embedding_model": self.config.embedding_model,
                        "chroma_collection": self.config.chroma_collection,
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        return created_at

    def insert_chunks(
        self,
        cursor: sqlite3.Cursor,
        document_id: str,
        chunks: list[ChunkDict],
    ) -> tuple[list[str], list[str], list[dict]]:
        """Вставить чанки, вернуть данные для ChromaDB."""
        from rag.utils import tokenize
        import uuid

        chunk_ids = []
        chunk_texts = []
        chunk_metadatas = []

        for index, chunk in enumerate(chunks):
            content = chunk["content"]
            chunk_id = str(uuid.uuid4())
            cursor.execute(
                """
                INSERT INTO document_chunks (
                    id, document_id, chunk_index, page, content, embedding_json, token_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk_id,
                    document_id,
                    index,
                    chunk["page"],
                    content,
                    "[]",
                    len(tokenize(content)),
                ),
            )
            chunk_ids.append(chunk_id)
            chunk_texts.append(content)
            chunk_metadatas.append(
                {
                    "document_id": document_id,
                    "chunk_index": index,
                    "page": int(chunk["page"]) if chunk["page"] is not None else -1,
                }
            )

        return chunk_ids, chunk_texts, chunk_metadatas

    def commit(self) -> None:
        self.db.conn.commit()

    def rollback(self) -> None:
        self.db.conn.rollback()

    @staticmethod
    def _document_from_row(row) -> Document:
        return Document(
            id=row["id"],
            title=row["title"],
            source_path=row["source_path"],
            mime_type=row["mime_type"],
            discipline_id=row["discipline_id"],
            created_at=row["created_at"],
        )
