"""Репозиторий для работы с документами и чанками в SQL/реляционной БД.

Принимает DBAPI2-совместимое соединение (sqlite3 / psycopg2).
"""

from __future__ import annotations

import json
import logging
import mimetypes
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

import threading

from rag.config import RagConfig
from rag._types import (
    ChunkDict,
    DocumentRow,
    MaterialRow,
)
from helperium_sdk.rag.models import Document

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SaveResult:
    """Результат save_document_with_chunks — внутренний."""

    document: Document
    chunks_count: int


class ConnectionProvider(Protocol):
    """Маленький provider с полем connection (например Database)."""

    @property
    def connection(self) -> Any: ...


class DocumentRepository:
    """CRUD для документов и чанков через DBAPI2-совместимое соединение."""

    def __init__(
        self,
        connection: Any | ConnectionProvider,
        config: RagConfig,
        *,
        adapter: Callable[[str], str] | None = None,
    ) -> None:
        self._connection = connection
        self.config = config
        self._adapter = adapter or (lambda s: s)
        conn_module = getattr(connection, "__module__", "")
        is_sqlite = "sqlite3" in conn_module or "apsw" in conn_module
        if is_sqlite:
            self._lock = threading.RLock()
        else:
            class _DummyLock:
                def __enter__(self) -> _DummyLock:
                    return self
                def __exit__(self, *_: object) -> None:
                    return None
            self._lock = _DummyLock()

    @property
    def conn(self) -> Any:
        if hasattr(self._connection, "connection"):
            return self._connection.connection
        return self._connection

    @property
    def db_lock(self) -> threading.RLock:
        return self._lock

    def _sql(self, sql: str) -> str:
        return self._adapter(sql)

    def _exec(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> Any:
        cursor = self.conn.cursor()
        cursor.execute(self._sql(sql), params)
        return cursor

    # ── READ ──

    def list_documents(
        self, discipline_id: str | None = None, limit: int | None = None
    ) -> list[DocumentRow]:
        with self._lock:
            params: list[Any] = []
            if discipline_id:
                sql = """
                    SELECT id, title, source_path, mime_type, discipline_id, discipline_name, created_at
                    FROM documents WHERE discipline_id = ? ORDER BY created_at DESC
                    """
                params.append(discipline_id)
            else:
                sql = """
                    SELECT id, title, source_path, mime_type, discipline_id, discipline_name, created_at
                    FROM documents ORDER BY created_at DESC
                    """
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
            cursor = self._exec(sql, params)
            rows = cursor.fetchall()
            cursor.close()
            return [self._document_from_row(row) for row in rows]

    def list_documents_as_models(
        self, discipline_id: str | None = None, limit: int | None = None
    ) -> list[Document]:
        return [
            self._to_document_model(row)
            for row in self.list_documents(discipline_id, limit)
        ]

    def find_existing_by_path(self, source_path: str) -> str | None:
        with self._lock:
            cursor = self._exec(
                "SELECT id FROM documents WHERE source_path = ?", (source_path,)
            )
            row = cursor.fetchone()
            cursor.close()
            return row["id"] if row else None

    def find_document_for_delete(
        self,
        *,
        source_path: str | None = None,
        document_id: str | None = None,
    ) -> Any | None:
        with self._lock:
            if source_path:
                cursor = self._exec(
                    "SELECT id, title, source_path FROM documents WHERE source_path = ?",
                    (source_path,),
                )
                row = cursor.fetchone()
                cursor.close()
                return row
            if document_id:
                cursor = self._exec(
                    "SELECT id, title, source_path FROM documents WHERE id = ?",
                    (document_id,),
                )
                row = cursor.fetchone()
                cursor.close()
                return row
            return None

    def get_document_by_id(self, document_id: str) -> DocumentRow | None:
        with self._lock:
            cursor = self._exec(
                "SELECT id, title, source_path, mime_type, discipline_id, discipline_name, created_at "
                "FROM documents WHERE id = ?",
                (document_id,),
            )
            row = cursor.fetchone()
            cursor.close()
            return self._document_from_row(row) if row else None

    def get_document_by_id_as_model(self, document_id: str) -> Document | None:
        row = self.get_document_by_id(document_id)
        return self._to_document_model(row) if row else None

    def get_materials(
        self, discipline_id: str, material_type: str | None = None
    ) -> list[MaterialRow]:
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                self._sql(
                    """
                    SELECT id, title, source_path, mime_type, discipline_id, created_at
                    FROM documents WHERE discipline_id = ?
                    ORDER BY title ASC
                    """
                ),
                (discipline_id,),
            )
            materials = [self._material_from_row(row) for row in cursor.fetchall()]
            cursor.close()
            if material_type:
                normalized_type = material_type.lower()
                return [m for m in materials if normalized_type in m.type.lower()]
            return materials

    def search_materials(
        self, query: str, discipline_id: str | None = None
    ) -> list[MaterialRow]:
        with self._lock:
            cursor = self.conn.cursor()
            params: list[Any] = [f"%{query}%"]
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
            cursor.execute(self._sql(sql), params)
            results = [self._material_from_row(row) for row in cursor.fetchall()]
            cursor.close()
            return results

    def list_generated_document_rows(
        self,
        *,
        path_marker: str,
        discipline_id: str | None = None,
    ) -> list[Any]:
        with self._lock:
            params: list[Any] = [f"%{path_marker}%"]
            sql = "SELECT id, source_path FROM documents WHERE source_path LIKE ?"
            if discipline_id:
                sql += " AND discipline_id = ?"
                params.append(discipline_id)
            cursor = self._exec(sql, params)
            rows = cursor.fetchall()
            cursor.close()
            return rows

    # ── WRITE ──

    def save_document_with_chunks(
        self,
        source_path: str,
        chunks: list[ChunkDict],
        discipline_id: str | None,
        discipline_name: str | None = None,
        title: str | None = None,
        vector_store=None,
    ) -> SaveResult:
        with self._lock:
            document_id = str(uuid.uuid4())
            mime_type = (
                mimetypes.guess_type(source_path)[0] or "application/octet-stream"
            )
            document_title = title or Path(source_path).stem
            cursor = self.conn.cursor()

            existing_id = self.find_existing_by_path(source_path)
            if existing_id:
                if vector_store:
                    try:
                        vector_store.delete_by_document_id(existing_id)
                    except Exception as exc:
                        logger.warning(
                            "Failed to delete vectors for %s: %s", existing_id, exc
                        )
                self._delete_document(self.conn, existing_id)

            created_at = datetime.now(timezone.utc).isoformat()
            cursor.execute(
                self._sql(
                    """
                    INSERT INTO documents (
                        id, title, source_path, mime_type,
                        discipline_id, discipline_name, created_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """
                ),
                (
                    document_id,
                    document_title,
                    source_path,
                    mime_type,
                    discipline_id,
                    discipline_name,
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
            cursor.close()

            chunk_ids, chunk_texts, chunk_metadatas = self._insert_chunks(
                document_id, chunks,
            )

            if vector_store:
                try:
                    vector_store.add_chunks(
                        chunk_ids=chunk_ids,
                        chunk_texts=chunk_texts,
                        chunk_metadatas=chunk_metadatas,
                        document_id=document_id,
                        document_title=document_title,
                        source_path=source_path,
                        discipline_id=discipline_id,
                    )
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

            return SaveResult(
                document=Document(
                    id=document_id,
                    title=document_title,
                    source_path=source_path,
                    mime_type=mime_type,
                    discipline_id=discipline_id,
                    discipline_name=discipline_name,
                    created_at=created_at,
                ),
                chunks_count=len(chunks),
            )

    def _delete_document(self, connection: Any, document_id: str) -> None:
        cursor = connection.cursor()
        cursor.execute(
            self._sql("DELETE FROM document_chunks WHERE document_id = ?"),
            (document_id,),
        )
        cursor.execute(
            self._sql("DELETE FROM documents WHERE id = ?"),
            (document_id,),
        )
        cursor.close()

    def delete_document_record(self, document_id: str, *, commit: bool = True) -> None:
        with self._lock:
            self._delete_document(self.conn, document_id)
            if commit:
                self.conn.commit()

    # ── chunks ──

    def _insert_chunks(
        self,
        document_id: str,
        chunks: list[ChunkDict],
    ) -> tuple[list[str], list[str], list[dict]]:
        from rag.utils import tokenize

        chunk_ids = []
        chunk_texts = []
        chunk_metadatas = []
        cursor = self.conn.cursor()

        for index, chunk in enumerate(chunks):
            content = chunk["content"]
            chunk_id = str(uuid.uuid4())
            cursor.execute(
                self._sql(
                    """
                    INSERT INTO document_chunks (
                        id, document_id, chunk_index, page, content,
                        embedding_json, token_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """
                ),
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

        cursor.close()
        return chunk_ids, chunk_texts, chunk_metadatas

    def get_all_chunks_for_reembed(self) -> list[dict]:
        """Get all chunks with document metadata for re-embedding."""
        with self._lock:
            cursor = self.conn.execute(
                self._sql(
                    """
                    SELECT c.id, c.content, c.chunk_index, c.page,
                           d.id AS document_id, d.title, d.source_path,
                           d.discipline_id
                    FROM document_chunks c
                    JOIN documents d ON d.id = c.document_id
                    ORDER BY d.id, c.chunk_index
                    """
                )
            )
            rows = cursor.fetchall()
            cursor.close()
            result = []
            for row in rows:
                result.append(
                    {
                        "id": row["id"],
                        "content": row["content"],
                        "chunk_index": row["chunk_index"],
                        "page": row["page"],
                        "document_id": row["document_id"],
                        "title": row["title"],
                        "source_path": row["source_path"],
                        "discipline_id": row["discipline_id"],
                    }
                )
            return result

    def commit(self) -> None:
        with self._lock:
            self.conn.commit()

    def rollback(self) -> None:
        with self._lock:
            self.conn.rollback()

    def save_generated_document_fallback(
        self,
        path: str,
        discipline_id: str,
        title: str,
        text: str,
    ) -> None:
        with self._lock:
            source_path = str(Path(path).resolve())
            document_id = str(uuid.uuid4())
            mime_type = (
                mimetypes.guess_type(source_path)[0] or "application/octet-stream"
            )
            created_at = datetime.now(timezone.utc).isoformat()
            cursor = self._exec(
                "SELECT id FROM documents WHERE source_path = ?",
                (source_path,),
            )
            existing = cursor.fetchone()
            if existing:
                self._delete_document(self.conn, existing["id"])

            cursor.execute(
                self._sql(
                    """
                    INSERT INTO documents (
                        id, title, source_path, mime_type,
                        discipline_id, discipline_name, created_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """
                ),
                (
                    document_id,
                    title,
                    source_path,
                    mime_type,
                    discipline_id,
                    None,
                    created_at,
                    json.dumps({"generated": True, "indexed": False}, ensure_ascii=False),
                ),
            )
            cursor.execute(
                self._sql(
                    """
                    INSERT INTO document_chunks (
                        id, document_id, chunk_index, page, content,
                        embedding_json, token_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """
                ),
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
            cursor.close()
            self.conn.commit()

    # ── static helpers ──

    @staticmethod
    def _document_from_row(row: Any) -> DocumentRow:
        return DocumentRow(
            id=row["id"],
            title=row["title"],
            source_path=row["source_path"],
            mime_type=row["mime_type"],
            discipline_id=row["discipline_id"],
            discipline_name=row["discipline_name"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _to_document_model(row: DocumentRow) -> Document:
        return Document(
            id=row["id"],
            title=row["title"],
            source_path=row["source_path"],
            mime_type=row["mime_type"],
            discipline_id=row.get("discipline_id"),
            discipline_name=row.get("discipline_name"),
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

    def _material_from_row(self, row: Any) -> MaterialRow:
        source_path = row["source_path"]
        return MaterialRow(
            id=row["id"],
            discipline_id=row.get("discipline_id"),
            type=self._material_type_from_title(row["title"], source_path),
            title=row["title"],
            file_name=Path(source_path).name,
            source_path=source_path,
            mime_type=row["mime_type"],
            content=Path(source_path).name,
        )

    @staticmethod
    def _to_material_model(row: MaterialRow) -> dict:
        return dict(row)
