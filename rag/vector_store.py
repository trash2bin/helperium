"""Векторное хранилище (ChromaDB)."""
from __future__ import annotations

import logging
from typing import cast

import chromadb
from chromadb.api.types import Metadata

from db.models import RagSearchResult

from rag.config import RagConfig
from rag.embeddings import EmbeddingService

logger = logging.getLogger(__name__)


class VectorStore:
    """Обёртка над ChromaDB."""

    def __init__(self, config: RagConfig, embedding_service: EmbeddingService) -> None:
        self.config = config
        self.embedding_service = embedding_service

        self.client = chromadb.PersistentClient(path=config.chroma_path)
        self.collection = self.client.get_or_create_collection(
            name=config.chroma_collection,
            metadata={"hnsw:space": "cosine"},
        )

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
        """Добавить чанки в ChromaDB с батчингом."""
        if not chunk_texts:
            return

        batch_size = self.config.embedding_batch_size

        for i in range(0, len(chunk_texts), batch_size):
            batch_ids = chunk_ids[i : i + batch_size]
            batch_texts = chunk_texts[i : i + batch_size]
            batch_metas = chunk_metadatas[i : i + batch_size]

            # Обогащаем метаданные
            enriched_metas = []
            for meta in batch_metas:
                enriched_metas.append(
                    {
                        **meta,
                        "document_id": document_id,
                        "document_title": document_title,
                        "source_path": source_path,
                        "discipline_id": discipline_id or "",
                    }
                )

            embeddings = self.embedding_service.encode_batched(batch_texts)
            self.collection.add(
                ids=batch_ids,
                documents=batch_texts,
                embeddings=embeddings,
                metadatas=cast(list[Metadata], enriched_metas),
            )

    def delete_by_document_id(self, document_id: str) -> None:
        """Удалить все векторы документа."""
        self.collection.delete(where={"document_id": document_id})

    def delete_by_ids(self, ids: list[str]) -> None:
        """Удалить векторы по ID чанков."""
        if ids:
            self.collection.delete(ids=ids)

    def search(
        self,
        query: str,
        discipline_id: str | None = None,
        limit: int = 5,
    ) -> list[RagSearchResult]:
        """Семантический поиск."""
        query_embedding = self.embedding_service.encode_batched([query])

        query_result = self.collection.query(
            query_embeddings=query_embedding,
            n_results=limit,
            where={"discipline_id": discipline_id} if discipline_id else None,
            include=["documents", "metadatas", "distances"],
        )

        flat_ids = (query_result.get("ids") or [[]])[0]
        flat_docs = (query_result.get("documents") or [[]])[0]
        flat_metas = (query_result.get("metadatas") or [[]])[0]
        flat_dists = (query_result.get("distances") or [[]])[0]

        results = []
        for chunk_id, content, metadata, distance in zip(
            flat_ids, flat_docs, flat_metas, flat_dists
        ):
            page = self._meta_int(metadata.get("page", -1))
            results.append(
                RagSearchResult(
                    document_id=self._meta_str(metadata.get("document_id", "")),
                    document_title=self._meta_str(metadata.get("document_title", "")),
                    source_path=self._meta_str(metadata.get("source_path", "")),
                    discipline_id=self._meta_str(metadata.get("discipline_id", "")) or None,
                    chunk_id=str(chunk_id),
                    chunk_index=self._meta_int(metadata.get("chunk_index", 0)),
                    page=page if page >= 0 else None,
                    score=round(max(0.0, 1.0 - float(distance)), 6),
                    content=str(content),
                )
            )

        return results

    @staticmethod
    def _meta_int(val: object) -> int:
        if isinstance(val, (int, float, str)):
            return int(val)
        return 0

    @staticmethod
    def _meta_str(val: object) -> str:
        return str(val) if val is not None else ""
