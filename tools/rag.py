"""Фасад RagTools для обратной совместимости."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from db.database import Database
from db.models import Document, DocumentImportResult, RagContext, RagSearchResult

from rag.config import RagConfig
from rag.pipeline import RAGPipeline, ProgressCallback
from rag import create_rag_pipeline

logger = logging.getLogger(__name__)


class RagTools:
    """
    Инструменты RAG: парсинг документов, чанкинг, эмбеддинги, поиск.

    Тонкая обертка над RAGPipeline для сохранения оригинального интерфейса.
    Все тяжелые зависимости инициализируются лениво через фабрику.
    """

    def __init__(
        self,
        db: Database,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        chroma_path: str | None = None,
        embedding_model_name: str | None = None,
    ) -> None:
        self.db = db

        # Собираем конфиг из аргументов + env + дефолты
        # Аргументы конструктора имеют приоритет над env
        config = RagConfig.from_env()

        if chunk_size is not None:
            config.chunk_size = chunk_size
        if chunk_overlap is not None:
            config.chunk_overlap = chunk_overlap
        if chroma_path is not None:
            config.chroma_path = chroma_path
        if embedding_model_name is not None:
            config.embedding_model = embedding_model_name

        self._config = config
        self._pipeline: RAGPipeline | None = None

    @property
    def pipeline(self) -> RAGPipeline:
        """Ленивая инициализация пайплайна."""
        if self._pipeline is None:
            self._pipeline = create_rag_pipeline(self.db, self._config)
        return self._pipeline

    # === Оригинальный публичный интерфейс ===

    def import_document(
        self,
        path: str,
        discipline_id: str | None = None,
        title: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> DocumentImportResult:
        """Загрузить документ в SQLite + ChromaDB."""
        return self.pipeline.import_document(
            path=path,
            discipline_id=discipline_id,
            title=title,
            on_progress=on_progress,
        )

    def list_documents(self, discipline_id: str | None = None) -> list[Document]:
        """Список загруженных документов (опционально фильтруется по discipline_id)."""
        return self.pipeline.list_documents(discipline_id)

    def _delete_document_vectors(self, document_id: str) -> None:
        """Удалить векторы одного документа из ChromaDB."""
        self.pipeline.delete_document_vectors(document_id)

    def search_documents(
        self,
        query: str,
        discipline_id: str | None = None,
        limit: int = 5,
    ) -> list[RagSearchResult]:
        """Семантический поиск по чанкам."""
        return self.pipeline.search_documents(
            query=query,
            discipline_id=discipline_id,
            limit=limit,
        )

    def build_rag_context(
        self,
        query: str,
        discipline_id: str | None = None,
        limit: int = 5,
    ) -> RagContext:
        """Собрать RagContext для LLM: запрос + найденные чанки + инструкция."""
        return self.pipeline.build_rag_context(
            query=query,
            discipline_id=discipline_id,
            limit=limit,
        )
