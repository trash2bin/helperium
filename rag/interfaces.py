"""Протоколы для абстракции внешних сервисов RAG.

Позволяют подменять реализацию эмбеддингов и векторного хранилища
без изменения кода пайплайна (локально → remote → микросервис).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from rag.models import RagSearchResult


@runtime_checkable
class EmbeddingProtocol(Protocol):
    """Протокол сервиса эмбеддингов."""

    def encode_batched(self, texts: list[str]) -> list[list[float]]:
        """Векторизовать список строк с батчингом."""
        ...


@runtime_checkable
class VectorStoreProtocol(Protocol):
    """Протокол векторного хранилища."""

    def add_chunks(
        self,
        chunk_ids: list[str],
        chunk_texts: list[str],
        chunk_metadatas: list[dict],
        document_id: str,
        document_title: str,
        source_path: str,
        discipline_id: str | None,
    ) -> None:
        """Добавить чанки в векторное хранилище."""
        ...

    def delete_by_document_id(self, document_id: str) -> None:
        """Удалить все векторы документа."""
        ...

    def delete_by_ids(self, ids: list[str]) -> None:
        """Удалить векторы по ID чанков."""
        ...

    def search(
        self,
        query: str,
        discipline_id: str | None = None,
        limit: int = 5,
    ) -> list[RagSearchResult]:
        """Семантический поиск."""
        ...