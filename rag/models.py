"""Внутренние и внешние модели данных RAG-системы."""
from __future__ import annotations

from typing import List, TypedDict

from pydantic import BaseModel


# === Внутренние модели пайплайна ===


class PageDict(TypedDict):
    """Страница документа после парсинга."""
    page: int | None
    text: str


class ChunkDict(TypedDict):
    """Чанк текста с привязкой к странице."""
    page: int | None
    content: str


# === Pydantic-модели для внешнего API ===


class Document(BaseModel):
    """Документ, загруженный в RAG-индекс."""
    id: str
    title: str
    source_path: str
    mime_type: str
    discipline_id: str | None = None
    created_at: str


class DocumentChunk(BaseModel):
    """Чанк текста документа."""
    id: str
    document_id: str
    chunk_index: int
    page: int | None = None
    content: str


class DocumentImportResult(BaseModel):
    """Результат импорта документа."""
    document: Document
    chunks_count: int


class RagSearchResult(BaseModel):
    """Результат семантического поиска по документам."""
    document_id: str
    document_title: str
    source_path: str
    discipline_id: str | None = None
    chunk_id: str
    chunk_index: int
    page: int | None = None
    score: float
    content: str


class Material(BaseModel):
    """Учебный материал (документ, представленный как материал дисциплины)."""
    id: str
    discipline_id: str
    type: str
    title: str
    file_name: str
    source_path: str
    mime_type: str
    content: str = ""


class RagContext(BaseModel):
    """Готовый контекст для LLM с инструкцией и найденными чанками."""
    query: str
    answer_instruction: str
    chunks: List[RagSearchResult]