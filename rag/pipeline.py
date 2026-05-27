"""Основной пайплайн RAG."""
from __future__ import annotations

import logging
import mimetypes
import uuid
from pathlib import Path
from typing import Callable

from db.models import Document, DocumentImportResult, RagContext, RagSearchResult

from rag.config import RagConfig
from rag.parser import DocumentParser
from rag.chunker import TextChunker
from rag.embeddings import EmbeddingService
from rag.repository import DocumentRepository
from rag.vector_store import VectorStore

logger = logging.getLogger(__name__)

ProgressCallback = Callable[..., None]


class RAGPipeline:
    """Оркестратор RAG-пайплайна."""

    def __init__(
        self,
        config: RagConfig,
        parser: DocumentParser,
        chunker: TextChunker,
        embedding_service: EmbeddingService,
        repository: DocumentRepository,
        vector_store: VectorStore,
    ) -> None:
        self.config = config
        self.parser = parser
        self.chunker = chunker
        self.embedding_service = embedding_service
        self.repository = repository
        self.vector_store = vector_store

    def import_document(
        self,
        path: str,
        discipline_id: str | None = None,
        title: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> DocumentImportResult:
        """Импортировать документ."""
        source_path = self._validate_path(path)

        # Парсинг
        pages = self.parser.extract_pages(source_path)
        if on_progress:
            on_progress("chunk")

        # Чанкинг
        chunks = self.chunker.chunk_pages(pages)
        if on_progress:
            on_progress("embed")

        if not chunks:
            raise ValueError(f"Document has no readable text: {source_path}")

        # Сохранение
        document = self._save_document(
            source_path=source_path,
            chunks=chunks,
            discipline_id=discipline_id,
            title=title,
        )

        if on_progress:
            on_progress("done", n=len(chunks))

        return DocumentImportResult(document=document, chunks_count=len(chunks))

    def _save_document(
        self,
        source_path: Path,
        chunks: list,
        discipline_id: str | None,
        title: str | None,
    ) -> Document:
        """Сохранить документ (транзакция SQLite + ChromaDB)."""
        document_id = str(uuid.uuid4())
        mime_type = mimetypes.guess_type(source_path.name)[0] or "application/octet-stream"
        document_title = title or source_path.stem

        cursor = self.repository.db.conn.cursor()

        # Удаляем старую версию
        existing_id = self.repository.find_existing_by_path(str(source_path))
        if existing_id:
            try:
                self.vector_store.delete_by_document_id(existing_id)
            except Exception as exc:
                logger.warning("Failed to delete vectors for %s: %s", existing_id, exc)
            self.repository.delete_document(cursor, existing_id)

        # Вставляем в SQLite
        created_at = self.repository.insert_document(
            cursor=cursor,
            document_id=document_id,
            title=document_title,
            source_path=str(source_path),
            mime_type=mime_type,
            discipline_id=discipline_id,
        )

        chunk_ids, chunk_texts, chunk_metadatas = self.repository.insert_chunks(
            cursor=cursor,
            document_id=document_id,
            chunks=chunks,
        )

        try:
            # Сначала ChromaDB
            self.vector_store.add_chunks(
                chunk_ids=chunk_ids,
                chunk_texts=chunk_texts,
                chunk_metadatas=chunk_metadatas,
                document_id=document_id,
                document_title=document_title,
                source_path=str(source_path),
                discipline_id=discipline_id,
            )
            # Потом SQLite
            self.repository.commit()
        except Exception as e:
            self.repository.rollback()
            # Откатываем ChromaDB
            try:
                self.vector_store.delete_by_ids(chunk_ids)
            except Exception as cleanup_exc:
                logger.error("Failed to cleanup ChromaDB: %s", cleanup_exc)
            raise e

        return Document(
            id=document_id,
            title=document_title,
            source_path=str(source_path),
            mime_type=mime_type,
            discipline_id=discipline_id,
            created_at=created_at,
        )

    def list_documents(self, discipline_id: str | None = None) -> list[Document]:
        """Список документов."""
        return self.repository.list_documents(discipline_id)

    def delete_document_vectors(self, document_id: str) -> None:
        """Удалить векторы документа из векторного хранилища."""
        self.vector_store.delete_by_document_id(document_id)

    def search_documents(
        self,
        query: str,
        discipline_id: str | None = None,
        limit: int = 5,
    ) -> list[RagSearchResult]:
        """Семантический поиск."""
        normalized_query = query.strip()
        if not normalized_query:
            return []

        limit = max(1, min(limit, self.config.search_limit_max))
        return self.vector_store.search(
            query=normalized_query,
            discipline_id=discipline_id,
            limit=limit,
        )

    def build_rag_context(
        self,
        query: str,
        discipline_id: str | None = None,
        limit: int = 5,
    ) -> RagContext:
        """Собрать RAG-контекст для LLM."""
        chunks = self.search_documents(
            query=query,
            discipline_id=discipline_id,
            limit=limit,
        )

        # TODO: Добавить контроль max_context_tokens через токенизатор

        return RagContext(
            query=query,
            answer_instruction=self.config.rag_instruction,
            chunks=chunks,
        )

    @staticmethod
    def _validate_path(path: str) -> Path:
        source_path = Path(path).expanduser()
        if not source_path.is_absolute():
            source_path = Path.cwd() / source_path
        source_path = source_path.resolve()

        if not source_path.exists():
            raise FileNotFoundError(f"Document not found: {source_path}")
        if not source_path.is_file():
            raise ValueError(f"Document path is not a file: {source_path}")
        return source_path
