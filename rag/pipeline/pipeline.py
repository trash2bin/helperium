"""Основной пайплайн RAG — оркестрация без raw SQL."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from rag.config import RagConfig
from rag.embedding.protocol import EmbeddingProtocol
from rag.vector_store.protocol import VectorStoreProtocol
from rag.prometheus_metrics import rag_cache_hits, rag_cache_misses
from agent_tutor_sdk.rag.models import (
    Document,
    DocumentImportResult,
    RagContext,
    RagSearchResult,
)
from rag.parser.parser import DocumentParser
from rag.chunker.base import TextChunker
from rag.pipeline.repository import DocumentRepository
from rag.reranker.bm25 import BM25Reranker

logger = logging.getLogger(__name__)

ProgressCallback = Callable[..., None]


class RAGPipeline:
    """Оркестратор RAG-пайплайна.

    Координирует парсинг -> чанкинг -> сохранение (SQLite + VectorStore).
    Не содержит raw SQL — вся persistence делегирована DocumentRepository.
    """

    def __init__(
        self,
        config: RagConfig,
        parser: DocumentParser,
        chunker: TextChunker,
        embedding_service: EmbeddingProtocol,
        repository: DocumentRepository,
        vector_store: VectorStoreProtocol,
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
        discipline_name: str | None = None,
        title: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> DocumentImportResult:
        """Импортировать документ: парсинг -> чанкинг -> сохранение."""
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

        # Сохранение (транзакция SQLite + VectorStore внутри repository)
        result = self.repository.save_document_with_chunks(
            source_path=str(source_path),
            chunks=chunks,
            discipline_id=discipline_id,
            discipline_name=discipline_name,
            title=title,
            vector_store=self.vector_store,
        )

        if on_progress:
            on_progress("done", n=result.chunks_count)

        return DocumentImportResult(
            document=result.document,
            chunks_count=result.chunks_count,
        )

    def list_documents(
        self, discipline_id: str | None = None, limit: int | None = None
    ) -> list[Document]:
        """Список документов в публичном формате (Document Pydantic)."""
        rows = self.repository.list_documents(discipline_id, limit)
        return [self.repository._to_document_model(r) for r in rows]

    def delete_document_vectors(self, document_id: str) -> None:
        """Удалить векторы документа из векторного хранилища."""
        self.vector_store.delete_by_document_id(document_id)

    def search_documents(
        self,
        query: str,
        discipline_id: str | None = None,
        limit: int = 5,
    ) -> list[RagSearchResult]:
        """Семантический поиск с BM25 reranker'ом поверх dense-поиска."""
        normalized_query = query.strip()
        if not normalized_query:
            return []

        # Проверяем кэш
        if hasattr(self, '_cache') and self._cache is not None:
            cached = self._cache.get_cached_search(normalized_query, discipline_id, limit)
            if cached is not None:
                rag_cache_hits.inc()
                return cached
            rag_cache_misses.inc()

        limit = max(1, min(limit, self.config.search_limit_max))

        if self.config.reranker_enabled:
            dense_limit = limit * self.config.reranker_dense_factor
            results = self.vector_store.search(
                query=normalized_query,
                discipline_id=discipline_id,
                limit=dense_limit,
            )
            if results:
                reranker = BM25Reranker(
                    k1=self.config.reranker_k1,
                    b=self.config.reranker_b,
                )
                reranked = reranker.rerank_with_scores(normalized_query, results)
                result = [r for r, _ in reranked[:limit]]
            else:
                result = []
        else:
            result = self.vector_store.search(
                query=normalized_query,
                discipline_id=discipline_id,
                limit=limit,
            )

        # Сохраняем в кэш
        if hasattr(self, '_cache') and self._cache is not None:
            self._cache.set_cached_search(normalized_query, discipline_id, result)

        return result

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
