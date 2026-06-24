"""Внутренние типы RAG-пайплайна.

TypedDict'ы, используемые внутри пайплайна (парсер → чанкер → репозиторий).
Не являются публичным API — не экспортируются наружу.
"""

from __future__ import annotations

from typing import TypedDict


class PageDict(TypedDict):
    """Страница документа после парсинга."""

    page: int | None
    text: str


class ChunkDict(TypedDict):
    """Чанк текста с привязкой к странице."""

    page: int | None
    content: str