"""HTTP DTO для RAG-сервиса.

Контрактные Pydantic-модели — единственный source of truth для HTTP-контракта
между RAG-сервисом и его потребителями.
Внутренние модели (TypedDict'ы пайплайна) живут в rag._types.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agent_tutor_sdk.rag.models import Document, RagSearchResult


# === Запросы ===


class SearchRequest(BaseModel):
    """Запрос семантического поиска."""

    query: str = Field(..., min_length=1, description="Поисковый запрос")
    discipline_id: str | None = Field(
        default=None, description="ID дисциплины для фильтрации"
    )
    limit: int = Field(
        default=5, ge=1, le=20, description="Количество результатов (1–20)"
    )


class ContextRequest(BaseModel):
    """Запрос готового RAG-контекста для LLM."""

    query: str = Field(..., min_length=1, description="Вопрос пользователя")
    discipline_id: str | None = Field(
        default=None, description="ID дисциплины для фильтрации"
    )
    limit: int = Field(
        default=5, ge=1, le=20, description="Фрагментов в контексте (1–20)"
    )


class ListDocumentsRequest(BaseModel):
    """Запрос списка документов."""

    discipline_id: str | None = Field(
        default=None, description="ID дисциплины для фильтрации"
    )
    limit: int | None = Field(
        default=None, ge=1, le=1000, description="Максимум документов (1–1000)"
    )


class ImportDocumentRequest(BaseModel):
    """Запрос импорта документа."""

    path: str = Field(
        ..., min_length=1, description="Путь к файлу (PDF, DOCX, TXT, MD, HTML)"
    )
    discipline_id: str | None = Field(
        default=None, description="ID дисциплины для привязки"
    )
    discipline_name: str | None = Field(
        default=None, description="Название дисциплины (сохраняется при импорте)"
    )
    title: str | None = Field(default=None, description="Человекочитаемое название")


class DeleteDocumentRequest(BaseModel):
    """Запрос удаления документа."""

    path: str | None = Field(default=None, description="Путь к файлу документа")
    document_id: str | None = Field(default=None, description="ID документа")


# === Ответы ===


class HealthResponse(BaseModel):
    """Состояние RAG-сервиса и его зависимостей."""

    status: Literal["ok", "degraded"] = "ok"
    database: dict = Field(default_factory=dict, description="Статус SQLite-доступа")
    chroma: dict = Field(default_factory=dict, description="Статус ChromaDB-индекса")
    embedding: dict = Field(default_factory=dict, description="Статус embedding-модели")


class ListDocumentsResponse(BaseModel):
    """Список документов в индексе."""

    documents: list[Document] = Field(..., description="Список документов в индексе")
    count: int = Field(..., description="Общее количество найденных документов")


class ImportDocumentResponse(BaseModel):
    """Результат импорта документа."""

    document: Document = Field(..., description="Метаданные импортированного документа")
    chunks_count: int = Field(..., description="Количество созданных чанков")


class DeleteDocumentResponse(BaseModel):
    """Результат удаления документа."""

    deleted: str | None = Field(default=None, description="ID удалённого документа")
    title: str | None = Field(default=None, description="Название удалённого документа")
    message: str | None = Field(
        default=None,
        description="Сообщение о результате (например, если документ не найден)",
    )


class SearchResponse(BaseModel):
    """Результаты семантического поиска."""

    results: list[RagSearchResult] = Field(
        ..., description="Список найденных фрагментов"
    )
    count: int = Field(..., description="Общее количество результатов")


class ContextResponse(BaseModel):
    """Сформированный контекст для LLM."""

    context: str = Field(..., description="Объединённый текст релевантных фрагментов")
    sources: list[RagSearchResult] = Field(
        ..., description="Список источников, использованных в контексте"
    )


class ErrorResponse(BaseModel):
    """Унифицированный ответ об ошибке."""

    error: str
    detail: str | None = None
