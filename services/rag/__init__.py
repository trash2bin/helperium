"""RAG-система.

Все тяжелые импорты (docling, chonkie, sentence-transformers, chromadb)
загружаются лениво — только при вызове create_rag_pipeline().
Это позволяет сервисам, не использующим RAG напрямую (mcp, api, web),
не тащить эти зависимости.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from rag.config import RagConfig

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from rag.chunker.base import TextChunker
    from rag.embedding.local import SentenceTransformerEmbedding
    from rag.parser.parser import DocumentParser
    from rag.pipeline.pipeline import RAGPipeline
    from rag.pipeline.repository import DocumentRepository
    from rag.vector_store.chroma import ChromaDBVectorStore


__all__ = [
    "RagConfig",
    "create_rag_pipeline",
    "TextChunker",
    "SentenceTransformerEmbedding",
    "DocumentParser",
    "RAGPipeline",
    "DocumentRepository",
    "ChromaDBVectorStore",
]


class ConnectionProvider:
    """Протокол: объект с полем connection (например Database.connector)."""

    def connection(self) -> Any: ...


def create_rag_pipeline(
    connection: Any | ConnectionProvider,
    config: RagConfig | None = None,
) -> "RAGPipeline":
    """Создать RAG-пайплайн.

    Принимает DBAPI2-совместимое соединение (или provider с полем connection).
    Если у соединения есть атрибут `_adapter` или передан объект Database,
    попытается получить адаптер из connector.adapt_sql.
    """
    # Ленивые импорты — docling, chonkie, sentence-transformers, chromadb
    from rag.chunker.base import TextChunker
    from rag.embedding.local import SentenceTransformerEmbedding
    from rag.parser.parser import DocumentParser
    from rag.pipeline.pipeline import RAGPipeline
    from rag.pipeline.repository import DocumentRepository
    from rag.vector_store.chroma import ChromaDBVectorStore

    if config is None:
        config = RagConfig.from_env()

    # Извлекаем adapter для SQL-параметризации
    # Если передан Connector (SqliteConnector/PostgresConnector) — у него есть .adapt_sql
    # Если передан Database — у неё есть .connector.adapt_sql
    adapter: Callable[[str], str] | None = None
    if hasattr(connection, "adapt_sql"):
        adapter = connection.adapt_sql  # type: ignore[union-attr]
    elif hasattr(connection, "connector") and hasattr(
        connection.connector, "adapt_sql"
    ):
        adapter = connection.connector.adapt_sql  # type: ignore[union-attr]

    # Выбор провайдера эмбеддингов
    if config.embedding_provider == "litellm":
        from rag.embedding.litellm_provider import LiteLLMEmbedding

        embedding_service = LiteLLMEmbedding(config)
    else:
        embedding_service = SentenceTransformerEmbedding(config)

    parser = DocumentParser(config)
    chunker = TextChunker(config)
    repository = DocumentRepository(connection, config, adapter=adapter)
    vector_store = ChromaDBVectorStore(config, embedding_service)

    # Check embedding consistency and re-embed if model changed
    chunks_for_reembed = repository.get_all_chunks_for_reembed()
    vector_store.ensure_embedding_consistency(chunks_for_reembed)

    pipeline = RAGPipeline(
        config=config,
        parser=parser,
        chunker=chunker,
        embedding_service=embedding_service,
        repository=repository,
        vector_store=vector_store,
    )

    # Подключаем кэш, если включён
    if config.cache_enabled:
        from rag.cache.local import LocalTTLCache

        pipeline._cache = LocalTTLCache(
            maxsize=config.cache_maxsize,
            ttl=config.cache_ttl,
        )
        # Clear cache if re-embedding just happened
        if vector_store.embedding_was_rebuilt:
            logger.info("Embedding model changed, clearing search cache.")
            pipeline._cache.clear()
    else:
        pipeline._cache = None

    return pipeline
