"""RAG-система."""
from __future__ import annotations

import sqlite3

from rag.config import RagConfig
from rag.parser import DocumentParser
from rag.chunker import TextChunker
from rag.embeddings import SentenceTransformerEmbedding
from rag.repository import DocumentRepository
from rag.vector_store import ChromaDBVectorStore
from rag.pipeline import RAGPipeline


__all__ = [
    "RagConfig",
    "RAGPipeline",
    "create_rag_pipeline",
]


def create_rag_pipeline(
    conn: sqlite3.Connection,
    config: RagConfig | None = None,
) -> RAGPipeline:
    """Создать RAG-пайплайн.

    Принимает sqlite3.Connection (не Database), чтобы не создавать
    циклической зависимости между rag и db пакетами.
    """
    if config is None:
        config = RagConfig.from_env()

    embedding_service = SentenceTransformerEmbedding(config)
    parser = DocumentParser(config)
    chunker = TextChunker(config)
    repository = DocumentRepository(conn, config)
    vector_store = ChromaDBVectorStore(config, embedding_service)

    return RAGPipeline(
        config=config,
        parser=parser,
        chunker=chunker,
        embedding_service=embedding_service,
        repository=repository,
        vector_store=vector_store,
    )