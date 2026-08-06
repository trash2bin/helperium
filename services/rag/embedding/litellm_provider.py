"""LiteLLM-реализация EmbeddingProtocol.

Использует litellm.embedding() для вызова OpenAI-совместимых API
(OpenAI, Together AI, DeepSeek, Polza.ai и др.) без локальной модели.
"""

from __future__ import annotations

import logging
import os
from typing import cast

from rag.config import RagConfig
from rag.embedding.protocol import EmbeddingProtocol

logger = logging.getLogger(__name__)


class LiteLLMEmbedding(EmbeddingProtocol):
    """Embedding через litellm.embedding() — OpenAI / Together / DeepSeek / etc.

    Модель указывается в config.embedding_model как обычно:
      "text-embedding-3-small", "text-embedding-3-large",
      "deepseek/deepseek-chat", "together_ai/m2-bert-80M-8k-retrieval"

    API-ключ: config.embedding_api_key (или OPENAI_API_KEY env fallback).
    Кастомный endpoint: config.embedding_api_base.
    """

    def __init__(self, config: RagConfig) -> None:
        self.config = config

    def encode_batched(
        self,
        texts: list[str],
        mode: str = "passage",
    ) -> list[list[float]]:
        """Векторизовать список строк через litellm.embedding()."""
        if not texts:
            return []

        from litellm import embedding

        # Префиксы для e5/bge моделей
        if mode == "query" and self.config.embedding_query_prefix:
            prefixed = [self.config.embedding_query_prefix + t for t in texts]
        elif mode == "passage" and self.config.embedding_passage_prefix:
            prefixed = [self.config.embedding_passage_prefix + t for t in texts]
        else:
            prefixed = texts

        # API-ключ: сначала из конфига, потом из env
        api_key = self.config.embedding_api_key or os.environ.get("OPENAI_API_KEY")

        # Определяем размерность — для litellm передаём только если указана
        kwargs: dict = {
            "model": self.config.embedding_model,
            "input": prefixed,
        }
        if api_key:
            kwargs["api_key"] = api_key
        if self.config.embedding_api_base:
            kwargs["api_base"] = self.config.embedding_api_base
        if self.config.embedding_dimensions:
            kwargs["dimensions"] = self.config.embedding_dimensions

        # Логируем модель для отладки (без ключа)
        logger.debug(
            "LiteLLM embedding: model=%s, texts=%d, dims=%s",
            self.config.embedding_model,
            len(prefixed),
            self.config.embedding_dimensions,
        )

        response = embedding(**kwargs)
        return cast(list[list[float]], [item["embedding"] for item in response.data])
