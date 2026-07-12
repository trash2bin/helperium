"""Векторное хранилище (ChromaDB)."""

from __future__ import annotations

import logging
from typing import cast

import chromadb
from chromadb.api.types import Embeddings, Metadata

from rag.config import RagConfig
from rag.embedding.protocol import EmbeddingProtocol
from rag.vector_store.protocol import VectorStoreProtocol
from helperium_sdk.rag.models import RagSearchResult

logger = logging.getLogger(__name__)


class ChromaDBVectorStore(VectorStoreProtocol):
    """ChromaDB-реализация VectorStoreProtocol.

    Локальное персистентное хранилище. В будущем заменяется на
    RemoteVectorStore (HTTP к Qdrant/Pgvector-микросервису).
    """

    def __init__(self, config: RagConfig, embedding_service: EmbeddingProtocol) -> None:
        self.config = config
        self.embedding_service = embedding_service

        self.client = chromadb.PersistentClient(path=config.chroma_path)
        self.collection = self.client.get_or_create_collection(
            name=config.chroma_collection,
            metadata={"hnsw:space": "cosine"},
        )
        self._embedding_was_rebuilt = False

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

        # Store embedding_model in collection metadata on first add if missing
        meta = dict(self.collection.metadata or {})
        if not meta.get("embedding_model"):
            # Filter out hnsw:* keys — ChromaDB rejects any modify with them
            meta["embedding_model"] = self.config.embedding_model
            safe_meta = {k: v for k, v in meta.items() if not k.startswith("hnsw:")}
            self.collection.modify(metadata=safe_meta)

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

            embeddings = cast(
                Embeddings,
                self.embedding_service.encode_batched(batch_texts, mode="passage"),
            )
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
        query_emb: Embeddings = cast(
            Embeddings,
            self.embedding_service.encode_batched([query], mode="query"),
        )

        query_result = self.collection.query(
            query_embeddings=query_emb,
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
                    discipline_id=self._meta_str(metadata.get("discipline_id", ""))
                    or None,
                    chunk_id=str(chunk_id),
                    chunk_index=self._meta_int(metadata.get("chunk_index", 0)),
                    page=page if page >= 0 else None,
                    score=round(max(0.0, 1.0 - float(distance)), 6),
                    content=str(content),
                )
            )

        return results

    def ensure_embedding_consistency(self, chunks_data: list[dict]) -> bool:
        """Check if ChromaDB vectors match current embedding model.

        Args:
            chunks_data: list of dicts with keys:
                id, content, chunk_index, page,
                document_id, title, source_path, discipline_id

        Returns:
            True if re-embedding happened, False if consistent.
        """
        collection_meta = self.collection.metadata or {}
        stored_model = collection_meta.get("embedding_model")

        if stored_model == self.config.embedding_model:
            # Already consistent
            return False

        if not stored_model:
            # First-time setup: store model name and return
            meta = dict(self.collection.metadata or {})
            meta["embedding_model"] = self.config.embedding_model
            safe_meta = {k: v for k, v in meta.items() if not k.startswith("hnsw:")}
            self.collection.modify(metadata=safe_meta)
            return False

        if not chunks_data:
            logger.warning(
                "Embedding model changed but no chunks to re-embed. Skipping."
            )
            return False

        # Re-embed!
        logger.info(
            "Embedding model changed: %s -> %s. Re-embedding %d chunks...",
            stored_model,
            self.config.embedding_model,
            len(chunks_data),
        )

        texts = [c["content"] for c in chunks_data]
        embeddings = self.embedding_service.encode_batched(texts, mode="passage")

        # Delete old collection
        self.client.delete_collection(self.config.chroma_collection)

        # Create new one with updated metadata
        self.collection = self.client.create_collection(
            name=self.config.chroma_collection,
            metadata={
                "hnsw:space": "cosine",
                "embedding_model": self.config.embedding_model,
            },
        )

        # Add all chunks with new embeddings in batches
        batch_size = 64
        for i in range(0, len(chunks_data), batch_size):
            batch = chunks_data[i : i + batch_size]
            batch_embs = embeddings[i : i + batch_size]

            metadatas = []
            for c in batch:
                page = (
                    int(c["page"])
                    if c.get("page") is not None and c["page"] >= 0
                    else -1
                )
                metadatas.append(
                    {
                        "document_id": c["document_id"],
                        "document_title": c["title"],
                        "source_path": c["source_path"],
                        "discipline_id": c["discipline_id"] or "",
                        "chunk_index": c["chunk_index"],
                        "page": page,
                    }
                )

            self.collection.add(
                ids=[c["id"] for c in batch],
                documents=[c["content"] for c in batch],
                embeddings=batch_embs,
                metadatas=metadatas,
            )

        self._embedding_was_rebuilt = True
        logger.info("Re-embedding complete for %d chunks.", len(chunks_data))
        return True

    @property
    def embedding_was_rebuilt(self) -> bool:
        return self._embedding_was_rebuilt

    @staticmethod
    def _meta_int(val: object) -> int:
        if isinstance(val, (int, float, str)):
            return int(val)
        return 0

    @staticmethod
    def _meta_str(val: object) -> str:
        return str(val) if val is not None else ""
