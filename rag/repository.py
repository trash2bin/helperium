"""Репозиторий для работы с документами и чанками в SQL/реляционной БД.

Принимает DBAPI2-совместимое соединение (sqlite3 / psycopg2).
Вся SQL-адаптация под параметрический стиль — через переданный adapter.

Внутренний слой: оперирует DocumentRow/MaterialRow (TypedDict из rag._types).
Публичный слой (save_document_with_chunks) возвращает SaveResult с document_id + chunks_count.
Конвертация в публичный Pydantic Document — в pipeline/service.
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

from rag.config import RagConfig
from rag._types import (
    ChunkDict,
    DocumentRow,
    MaterialRow,
)
from agent_tutor_sdk.rag.models import Document

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SaveResult:
    """Результат save_document_with_chunks — внутренний.

    Attributes:
        document: Pydantic Document (публичный, для обратной совместимости с тестами/вызывающими)
        chunks_count: количество созданных чанков
    """

    document: Document
    chunks_count: int


logger = logging.getLogger(__name__)


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
        """Args:
        connection: DBAPI2-соединение (sqlite3.Connection или psycopg2.connection)
                   или объект с полем .connection
        config: конфигурация RAG
        adapter: функция adapt_sql(sql) — подстановка параметрического стиля
        """
        self._connection = connection
        self.config = config
        self._adapter = adapter or (lambda s: s)

    @property
    def conn(self) -> Any:
        if hasattr(self._connection, "connection"):
            return self._connection.connection
        return self._connection

    def _sql(self, sql: str) -> str:
        return self._adapter(sql)

    def _exec(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> Any:
        """Создать курсор, выполнить SQL, вернуть курсор.

        Нужно для совместимости: psycopg2.cursor.execute() возвращает None,
        а sqlite3 возвращает курсор. Не цепляем .fetchone() после .execute().
        """
        cursor = self.conn.cursor()
        cursor.execute(self._sql(sql), params)
        return cursor

    # ── READ ────────────────────────────────────────────────────────

    def list_documents(
        self, discipline_id: str | None = None, limit: int | None = None
    ) -> list[DocumentRow]:
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
        """Обёртка для обратной совместимости: конвертирует DocumentRow в публичный Document."""
        return [
            self._to_document_model(row)
            for row in self.list_documents(discipline_id, limit)
        ]

    def find_existing_by_path(self, source_path: str) -> str | None:
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
    ) -> Any | None:  # sqlite3.Row-like dict
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
        cursor = self._exec(
            "SELECT id, title, source_path, mime_type, discipline_id, discipline_name, created_at "
            "FROM documents WHERE id = ?",
            (document_id,),
        )
        row = cursor.fetchone()
        cursor.close()
        return self._document_from_row(row) if row else None

    def get_document_by_id_as_model(self, document_id: str) -> Document | None:
        """Алиас для обратной совместимости: возвращает публичную Pydantic Document."""
        row = self.get_document_by_id(document_id)
        return self._to_document_model(row) if row else None

    def get_materials(
        self, discipline_id: str, material_type: str | None = None
    ) -> list[MaterialRow]:
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
        params: list[Any] = [f"%{path_marker}%"]
        sql = "SELECT id, source_path FROM documents WHERE source_path LIKE ?"
        if discipline_id:
            sql += " AND discipline_id = ?"
            params.append(discipline_id)
        cursor = self._exec(sql, params)
        rows = cursor.fetchall()
        cursor.close()
        return rows

    # ── WRITE (save_document_with_chunks) ──────────────────────────

    def save_document_with_chunks(
        self,
        source_path: str,
        chunks: list[ChunkDict],
        discipline_id: str | None,
        discipline_name: str | None = None,
        title: str | None = None,
        vector_store=None,
    ) -> SaveResult:
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
                    logger.warning(
                        "Failed to delete vectors for %s: %s", existing_id, exc
                    )
            # Используем fresh cursor для удаления
            self._delete_document(self.conn, existing_id)

        # Вставляем документ
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
            document_id,
            chunks,
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

        # Внутренний слой репозитория возвращает публичную модель для удобства
        # вызывающего кода (pipeline делает второй запрос, чтобы получить DocumentRow).
        # Здесь конвертируем напрямую, чтобы сохранить обратную совместимость сигнатуры.
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
        self._delete_document(self.conn, document_id)
        if commit:
            self.conn.commit()

    # ── chunks ──────────────────────────────────────────────────────

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

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    # ── специфичные для document_generator ──────────────────────────

    def save_generated_document_fallback(
        self,
        path: str,
        discipline_id: str,
        title: str,
        text: str,
    ) -> None:
        source_path = str(Path(path).resolve())
        document_id = str(uuid.uuid4())
        mime_type = mimetypes.guess_type(source_path)[0] or "application/octet-stream"
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
                None,  # discipline_name не сохраняется для fallback-документов
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

    # ── static helpers ──────────────────────────────────────────────

    @staticmethod
    def _document_from_row(row: Any) -> DocumentRow:
        """Конвертация сырого ряда БД в внутренний DocumentRow (TypedDict)."""
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
        """Конвертация внутреннего DocumentRow в публичный Pydantic Document."""
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
        """Конвертация сырого ряда в внутренний MaterialRow."""
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
        # Material модель не входит в публичный контракт SDK — возврат dict,
        # чтобы избежать жёсткой зависимости внутреннего слоя от публичного API.
        return dict(row)
