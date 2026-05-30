"""Фасад RagTools — упразднён.

RagTools был тонкой обёрткой с ленивой инициализацией, которая дублировала
весь интерфейс RAGPipeline. Теперь server.py и ingest.py используют
create_rag_pipeline() напрямую.

Оставлен как заглушка для обратной совместимости старых скриптов.
"""
from __future__ import annotations

import logging

from db.database import Database
from rag import create_rag_pipeline
from rag.models import Document, DocumentImportResult, RagContext, RagSearchResult

logger = logging.getLogger(__name__)


class RagTools:
    """Устаревший фасад. Создаёт RAGPipeline через Database."""

    def __init__(
        self,
        db: Database,
        **kwargs,
    ) -> None:
        self.db = db
        self._pipeline = create_rag_pipeline(db.conn)

    @property
    def pipeline(self):
        if self._pipeline is None:
            self._pipeline = create_rag_pipeline(self.db.conn)
        return self._pipeline

    def import_document(self, path, discipline_id=None, title=None, on_progress=None):
        return self.pipeline.import_document(
            path=path, discipline_id=discipline_id, title=title, on_progress=on_progress,
        )

    def list_documents(self, discipline_id=None):
        return self.pipeline.list_documents(discipline_id)

    def _delete_document_vectors(self, document_id):
        self.pipeline.delete_document_vectors(document_id)

    def search_documents(self, query, discipline_id=None, limit=5):
        return self.pipeline.search_documents(query, discipline_id, limit)

    def build_rag_context(self, query, discipline_id=None, limit=5):
        return self.pipeline.build_rag_context(query, discipline_id, limit)