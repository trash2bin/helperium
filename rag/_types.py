"""Внутренние типы RAG-пайплайна.

TypedDict'ы, используемые внутри пайплайна (парсер → чанкер → репозиторий → сервис).
Не являются публичным API — не экспортируются наружу.
Публичные Pydantic-модели (Document, Material, RagSearchResult, ...) — в agent_tutor_sdk.rag.models.
"""

from __future__ import annotations

from typing import Optional, TypedDict


class PageDict(TypedDict):
    """Страница документа после парсинга."""

    page: int | None
    text: str


class ChunkDict(TypedDict):
    """Чанк текста с привязкой к странице."""

    page: int | None
    content: str


# ── Внутренние ряды (возвращаются репозиторием до конвертации в публичные модели) ──


class DocumentRow(TypedDict, total=False):
    """Строка из таблицы documents — внутреннее представление.

    repository.list_documents() / get_document_by_id() возвращают эти ряды,
    конвертация в публичный agent_tutor_sdk.rag.models.Document — в pipeline/service.
    """

    id: str
    title: str
    source_path: str
    mime_type: str
    discipline_id: Optional[str]
    discipline_name: Optional[str]
    created_at: str
    metadata_json: Optional[str]


class MaterialRow(TypedDict, total=False):
    """Строка из таблицы materials — внутреннее представление."""

    id: str
    discipline_id: str
    type: str
    title: str
    file_name: str
    source_path: str
    mime_type: str
    content: str


class DocumentChunkRow(TypedDict, total=False):
    """Строка из таблицы document_chunks — внутреннее представление."""

    id: str
    document_id: str
    chunk_index: int
    page: Optional[int]
    content: str


class SearchHitRow(TypedDict, total=False):
    """Один результат семантического поиска — внутреннее представление."""

    chunk_id: str
    chunk_index: int
    page: Optional[int]
    score: float
    content: str
    document_id: str
    document_title: str
    source_path: str
    discipline_id: Optional[str]
