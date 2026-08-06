"""SentenceTransformer-реализация EmbeddingProtocol.

Модель грузится лениво при первом вызове encode_batched.
В будущем заменяется на RemoteEmbedding (HTTP к микросервису).
"""

from __future__ import annotations

import logging
from typing import cast

from chromadb.api.types import Embeddings

from rag.config import RagConfig
from rag.embedding.protocol import EmbeddingProtocol

logger = logging.getLogger(__name__)


class SentenceTransformerEmbedding(EmbeddingProtocol):
    """SentenceTransformer-реализация EmbeddingProtocol."""

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

    def encode_batched(
        self,
        texts: list[str],
        mode: str = "passage",
    ) -> Embeddings:
        """Векторизовать список строк с батчингом.

        Args:
            texts: список строк для векторизации
            mode: "query" — добавляет query_prefix, "passage" — passage_prefix.
                  Для e5-моделей префиксы критичны: без них качество падает.
        """
        if not texts:
            return []

        # Префиксы для модели
        if mode == "query" and self.config.embedding_query_prefix:
            prefixed = [self.config.embedding_query_prefix + t for t in texts]
        elif mode == "passage" and self.config.embedding_passage_prefix:
            prefixed = [self.config.embedding_passage_prefix + t for t in texts]
        else:
            prefixed = texts

        all_embeddings = []
        batch_size = self.config.embedding_batch_size

        for i in range(0, len(prefixed), batch_size):
            batch = prefixed[i : i + batch_size]
            embeddings = self.model.encode(batch, normalize_embeddings=True)
            all_embeddings.extend(embeddings)

        return cast(Embeddings, all_embeddings)
