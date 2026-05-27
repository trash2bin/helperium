"""Внутренние модели данных RAG-пайплайна."""
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
