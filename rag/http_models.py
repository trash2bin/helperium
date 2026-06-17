"""HTTP DTO для RAG-сервиса.

Разделены от внутренних моделей `rag.models`, чтобы HTTP-контракт
не зависел от внутренних TypedDict'ов пайплайна.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# === Запросы ===


class SearchRequest(BaseModel):
    """Запрос семантического поиска."""

    query: str = Field(..., min_length=1, description="Поисковый запрос")
    discipline_id: Optional[str] = Field(default=None, description="ID дисциплины для фильтрации")
    limit: int = Field(default=5, ge=1, le=20, description="Количество результатов (1–20)")


class ContextRequest(BaseModel):
    """Запрос готового RAG-контекста для LLM."""

    query: str = Field(..., min_length=1, description="Вопрос пользователя")
    discipline_id: Optional[str] = Field(default=None, description="ID дисциплины для фильтрации")
    limit: int = Field(default=5, ge=1, le=20, description="Фрагментов в контексте (1–20)")


class ListDocumentsRequest(BaseModel):
    """Запрос списка документов."""

    discipline_id: Optional[str] = Field(default=None, description="ID дисциплины для фильтрации")
    limit: Optional[int] = Field(default=None, ge=1, le=1000, description="Максимум документов (1–1000)")


class ImportDocumentRequest(BaseModel):
    """Запрос импорта документа."""

    path: str = Field(..., min_length=1, description="Путь к файлу (PDF, DOCX, TXT, MD, HTML)")
    discipline_id: Optional[str] = Field(default=None, description="ID дисциплины для привязки")
    title: Optional[str] = Field(default=None, description="Человекочитаемое название")


class DeleteDocumentRequest(BaseModel):
    """Запрос удаления документа."""

    path: Optional[str] = Field(default=None, description="Путь к файлу документа")
    document_id: Optional[str] = Field(default=None, description="ID документа")


# === Ответы ===


class HealthResponse(BaseModel):
    """Состояние RAG-сервиса и его зависимостей."""

    status: Literal["ok", "degraded"] = "ok"
    database: dict = Field(default_factory=dict, description="Статус SQLite-доступа")
    chroma: dict = Field(default_factory=dict, description="Статус ChromaDB-индекса")
    embedding: dict = Field(default_factory=dict, description="Статус embedding-модели")


class ErrorResponse(BaseModel):
    """Унифицированный ответ об ошибке."""

    error: str
    detail: Optional[str] = None
