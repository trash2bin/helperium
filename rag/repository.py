"""Репозиторий для работы с SQLite (документы и чанки)."""
from __future__ import annotations

import json
import logging
import mimetypes
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from rag.config import RagConfig
from rag.models import ChunkDict, Document, DocumentImportResult, Material

logger = logging.getLogger(__name__)


class ConnectionProvider(Protocol):
    @property
    def connection(self) -> sqlite3.Connection:
        ...


class DocumentRepository:
    """CRUD для документов и чанков в SQLite.

    Принимает маленький connection provider или sqlite3.Connection, чтобы RAG
    оставался отделен от application-level Database.
    """

    def __init__(self, connection: sqlite3.Connection | ConnectionProvider, config: RagConfig) -> None:
        self._connection = connection
        self.config = config

    @property
    def conn(self) -> sqlite3.Connection:
        if isinstance(self._connection, sqlite3.Connection):
            return self._connection
        return self._connection.connection

    def list_documents(self, discipline_id: str | None = None, limit: int | None = None) -> list[Document]:
        """Список загруженных документов (опционально по дисциплине).

        Args:
            discipline_id: Опциональный ID дисциплины для фильтрации
            limit: Максимальное количество возвращаемых документов (None = без ограничения)
        """
        cursor = self.conn.cursor()
        params = []

        if discipline_id:
            sql = """
                SELECT id, title, source_path, mime_type, discipline_id, created_at
                FROM documents WHERE discipline_id = ? ORDER BY created_at DESC
                """
            params.append(discipline_id)
        else:
            sql = """
                SELECT id, title, source_path, mime_type, discipline_id, created_at
                FROM documents ORDER BY created_at DESC
                """

        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        rows = cursor.execute(sql, params).fetchall()
        return [self._document_from_row(row) for row in rows]

    def find_existing_by_path(self, source_path: str) -> str | None:
        """Найти ID документа по пути."""
        cursor = self.conn.cursor()
        row = cursor.execute(
            "SELECT id FROM documents WHERE source_path = ?",
            (source_path,),
        ).fetchone()
        return row["id"] if row else None

    def find_document_for_delete(
        self,
        *,
        source_path: str | None = None,
        document_id: str | None = None,
    ) -> sqlite3.Row | None:
        """Find minimal document info used by delete flows."""
        if source_path:
            return self.conn.execute(
                "SELECT id, title, source_path FROM documents WHERE source_path = ?",
                (source_path,),
            ).fetchone()
        if document_id:
            return self.conn.execute(
                "SELECT id, title, source_path FROM documents WHERE id = ?",
                (document_id,),
            ).fetchone()
        return None

    def get_document_by_id(self, document_id: str) -> Document | None:
        """Получить документ по ID."""
        cursor = self.conn.cursor()
        row = cursor.execute(
            "SELECT id, title, source_path, mime_type, discipline_id, created_at "
            "FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()
        return self._document_from_row(row) if row else None

    def get_materials(self, discipline_id: str, material_type: str | None = None) -> list[Material]:
        """Получить документы как учебные материалы."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, title, source_path, mime_type, discipline_id, created_at
            FROM documents WHERE discipline_id = ?
            ORDER BY title ASC
            """,
            (discipline_id,),
        )
        materials = [self._material_from_row(row) for row in cursor.fetchall()]
        if material_type:
            normalized_type = material_type.lower()
            return [
                m for m in materials
                if normalized_type in m.type.lower()
            ]
        return materials

    def search_materials(self, query: str, discipline_id: str | None = None) -> list[Material]:
        """Полнотекстовый поиск по документам (title + chunk content)."""
        cursor = self.conn.cursor()
        params: list = [f"%{query}%"]
        sql = """
            SELECT DISTINCT
                documents.id,
                documents.title,
                documents.source_path,
                documents.mime_type,
                documents.discipline_id,
                documents.created_at
            FROM documents
            LEFT JOIN document_chunks ON document_chunks.document_id = documents.id
            WHERE (documents.title LIKE ? OR document_chunks.content LIKE ?)
        """
        params.append(f"%{query}%")
        if discipline_id:
            sql += " AND documents.discipline_id = ?"
            params.append(discipline_id)

        sql += " ORDER BY documents.title ASC"
        cursor.execute(sql, params)
        return [self._material_from_row(row) for row in cursor.fetchall()]

    def list_generated_document_rows(
        self,
        *,
        path_marker: str,
        discipline_id: str | None = None,
    ) -> list[sqlite3.Row]:
        """Return generated document rows matched by source path marker."""
        params: list[str] = [f"%{path_marker}%"]
        sql = "SELECT id, source_path FROM documents WHERE source_path LIKE ?"
        if discipline_id:
            sql += " AND discipline_id = ?"
            params.append(discipline_id)
        return self.conn.execute(sql, params).fetchall()

    # === Транзакционный save ===

    def save_document_with_chunks(
        self,
        source_path: str,
        chunks: list[ChunkDict],
        discipline_id: str | None,
        title: str | None,
        vector_store=None,
    ) -> DocumentImportResult:
        """Сохранить документ с чанками в одной транзакции (SQLite + ChromaDB).

        Если передан vector_store, сперва пишет в ChromaDB, потом коммитит SQLite.
        При ошибке ChromaDB откатывает SQLite и чистит векторы.
        """
        document_id = str(uuid.uuid4())
        mime_type = mimetypes.guess_type(source_path)[0] or "application/octet-stream"
        document_title = title or Path(source_path).stem

        cursor = self.conn.cursor()

        # Удаляем старую версию, если есть
        existing_id = self.find_existing_by_path(source_path)
        if existing_id:
            if vector_store:
                try:
                    vector_store.delete_by_document_id(existing_id)
                except Exception as exc:
                    logger.warning("Failed to delete vectors for %s: %s", existing_id, exc)
            self.delete_document(cursor, existing_id)

        # Вставляем документ
        created_at = datetime.now(timezone.utc).isoformat()
        cursor.execute(
            """
            INSERT INTO documents (
                id, title, source_path, mime_type, discipline_id, created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                document_title,
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

        chunk_ids, chunk_texts, chunk_metadatas = self._insert_chunks(
            cursor, document_id, chunks,
        )

        if vector_store:
            try:
                # Сначала ChromaDB
                vector_store.add_chunks(
                    chunk_ids=chunk_ids,
                    chunk_texts=chunk_texts,
                    chunk_metadatas=chunk_metadatas,
                    document_id=document_id,
                    document_title=document_title,
                    source_path=source_path,
                    discipline_id=discipline_id,
                )
                # Потом SQLite
                self.conn.commit()
            except Exception as exc:
                self.conn.rollback()
                try:
                    vector_store.delete_by_ids(chunk_ids)
                except Exception as cleanup_exc:
                    logger.error("Failed to cleanup ChromaDB: %s", cleanup_exc)
                raise exc
        else:
            self.conn.commit()

        return DocumentImportResult(
            document=Document(
                id=document_id,
                title=document_title,
                source_path=source_path,
                mime_type=mime_type,
                discipline_id=discipline_id,
                created_at=created_at,
            ),
            chunks_count=len(chunks),
        )

    # === Нижний уровень ===

    def delete_document(self, cursor: sqlite3.Cursor, document_id: str) -> None:
        """Удалить документ и его чанки из SQLite."""
        cursor.execute(
            "DELETE FROM document_chunks WHERE document_id = ?", (document_id,)
        )
        cursor.execute("DELETE FROM documents WHERE id = ?", (document_id,))

    def delete_document_record(self, document_id: str, *, commit: bool = True) -> None:
        cursor = self.conn.cursor()
        self.delete_document(cursor, document_id)
        if commit:
            self.conn.commit()

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    # === Внутренние helpers ===

    def _insert_chunks(
        self,
        cursor: sqlite3.Cursor,
        document_id: str,
        chunks: list[ChunkDict],
    ) -> tuple[list[str], list[str], list[dict]]:
        """Вставить чанки, вернуть (ids, texts, metadatas) для ChromaDB."""
        from rag.utils import tokenize

        chunk_ids = []
        chunk_texts = []
        chunk_metadatas = []

        for index, chunk in enumerate(chunks):
            content = chunk["content"]
            chunk_id = str(uuid.uuid4())
            cursor.execute(
                """
                INSERT INTO document_chunks (
                    id, document_id, chunk_index, page, content,
                    embedding_json, token_count
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
            chunk_metadatas.append({
                "document_id": document_id,
                "chunk_index": index,
                "page": int(chunk["page"]) if chunk["page"] is not None else -1,
            })

        return chunk_ids, chunk_texts, chunk_metadatas

    # === Специфичные для document_generator ===

    def save_generated_document_fallback(
        self,
        path: str,
        discipline_id: str,
        title: str,
        text: str,
    ) -> None:
        """Записать сгенерированный документ в SQLite без векторного индекса."""
        source_path = str(Path(path).resolve())
        document_id = str(uuid.uuid4())
        mime_type = mimetypes.guess_type(source_path)[0] or "application/octet-stream"
        created_at = datetime.now(timezone.utc).isoformat()
        cursor = self.conn.cursor()

        existing = cursor.execute(
            "SELECT id FROM documents WHERE source_path = ?",
            (source_path,),
        ).fetchone()
        if existing:
            self.delete_document(cursor, existing["id"])

        cursor.execute(
            """
            INSERT INTO documents (
                id, title, source_path, mime_type, discipline_id,
                created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                title,
                source_path,
                mime_type,
                discipline_id,
                created_at,
                json.dumps({"generated": True, "indexed": False}, ensure_ascii=False),
            ),
        )
        cursor.execute(
            """
            INSERT INTO document_chunks (
                id, document_id, chunk_index, page, content,
                embedding_json, token_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                document_id,
                0,
                None,
                text,
                "[]",
                len(text.split()),
            ),
        )
        self.conn.commit()

    # === Статические helpers ===

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

    @staticmethod
    def _material_type_from_title(title: str, source_path: str = "") -> str:
        lowered = f"{title} {Path(source_path).name}".lower()
        if "лекц" in lowered:
            return "Лекция"
        if "метод" in lowered:
            return "Методичка"
        if "лаб" in lowered:
            return "Лабораторная работа"
        return "Документ"

    def _material_from_row(self, row) -> Material:
        source_path = row["source_path"]
        return Material(
            id=row["id"],
            discipline_id=row["discipline_id"],
            type=self._material_type_from_title(row["title"], source_path),
            title=row["title"],
            file_name=Path(source_path).name,
            source_path=source_path,
            mime_type=row["mime_type"],
            content=Path(source_path).name,
        )
