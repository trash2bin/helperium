"""RAG-система."""
from __future__ import annotations

from db.database import Database

from rag.config import RagConfig
from rag.parser import DocumentParser
from rag.chunker import TextChunker
from rag.embeddings import EmbeddingService
from rag.repository import DocumentRepository
from rag.vector_store import VectorStore
from rag.pipeline import RAGPipeline


__all__ = [
    "RagConfig",
    "RAGPipeline",
    "create_rag_pipeline",
]


def create_rag_pipeline(
    db: Database,
    config: RagConfig | None = None,
) -> RAGPipeline:
    """Фабрика для создания RAG-пайплайна с готовыми зависимостями."""
    if config is None:
        config = RagConfig.from_env()

    embedding_service = EmbeddingService(config)
    parser = DocumentParser(config)
    chunker = TextChunker(config)
    repository = DocumentRepository(db, config)
    vector_store = VectorStore(config, embedding_service)

    return RAGPipeline(
        config=config,
        parser=parser,
        chunker=chunker,
        embedding_service=embedding_service,
        repository=repository,
        vector_store=vector_store,
    )
