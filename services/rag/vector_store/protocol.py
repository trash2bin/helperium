"""Протокол векторного хранилища."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from helperium_sdk.rag.models import RagSearchResult


@runtime_checkable
class VectorStoreProtocol(Protocol):
    """Протокол векторного хранилища.

    Позволяет подменять реализацию (ChromaDB → Pgvector → Qdrant → ...)
    без изменения кода пайплайна.
    """

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
