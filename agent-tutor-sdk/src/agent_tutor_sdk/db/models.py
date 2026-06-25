"""Модели БД — устаревший модуль.

Доменные модели (Student, Teacher, ...) перемещены в agent_tutor_sdk.contracts.
RAG-модели — в agent_tutor_sdk.rag.models.

Этот файл оставлен для обратной совместимости и реэкспортирует RAG-модели.
"""

from agent_tutor_sdk.rag.models import (  # noqa: F401
    Document,
    DocumentChunk,
    DocumentImportResult,
    Material,
    RagContext,
    RagSearchResult,
)
