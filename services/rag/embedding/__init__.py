"""Embedding providers."""

from rag.embedding.local import SentenceTransformerEmbedding  # noqa: F401
from rag.embedding.protocol import EmbeddingProtocol  # noqa: F401

__all__ = ["SentenceTransformerEmbedding", "EmbeddingProtocol"]
