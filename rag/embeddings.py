"""Сервис для вычисления эмбеддингов."""
from __future__ import annotations

import logging
from typing import cast

from chromadb.api.types import Embeddings

from rag.config import RagConfig

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Сервис эмбеддингов с батчингом и кэшированием модели."""

    def __init__(self, config: RagConfig) -> None:
        self.config = config
        self._model = None

    @property
    def model(self):
        """Ленивая инициализация модели."""
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(
                    self.config.embedding_model,
                    local_files_only=self.config.embedding_local_files_only,
                    device=self.config.embedding_device,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to load embedding model '{self.config.embedding_model}'. "
                    "Check internet access or set RAG_EMBEDDING_MODEL to a local path."
                ) from exc
        return self._model

    def encode_batched(self, texts: list[str]) -> Embeddings:
        """Векторизовать список строк с батчингом."""
        if not texts:
            return []

        all_embeddings = []
        batch_size = self.config.embedding_batch_size

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            embeddings = self.model.encode(batch, normalize_embeddings=True)
            all_embeddings.extend(embeddings)

        return cast(Embeddings, all_embeddings)
