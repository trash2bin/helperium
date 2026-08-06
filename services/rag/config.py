"""Конфигурация RAG-системы."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RagConfig:
    """Централизованная конфигурация RAG."""

    # ── Провайдер эмбеддингов ──
    embedding_provider: str = "local"  # local | litellm
    # Для local:
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_batch_size: int = 64
    embedding_device: str = "cpu"
    embedding_local_files_only: bool = False
    embedding_query_prefix: str = ""  # e5: "query: ", MiniLM: ""
    embedding_passage_prefix: str = ""  # e5: "passage: ", MiniLM: ""
    # Для litellm:
    embedding_api_key: str | None = None
    embedding_api_base: str | None = None
    embedding_dimensions: int = 1536  # размерность вектора для API-провайдеров

    # Чанкинг
    chunker_type: str = "semantic"  # semantic | recursive | sentence
    chunk_size: int = 768
    chunk_overlap: int = 160
    page_overlap_tokens: int = 50  # overlap между страницами

    # ChromaDB + SQL
    chroma_path: str = ""
    chroma_collection: str = "university_documents"
    rag_db_path: str = ""

    # Промпты
    rag_instruction: str = (
        "Ответь на вопрос только по найденным фрагментам документов. "
        "Если в контексте нет ответа, прямо скажи, что данных в документах недостаточно. "
        "Ссылайся на название документа и страницу, когда page заполнен."
    )

    # BM25 reranker
    reranker_enabled: bool = True
    reranker_k1: float = 1.5
    reranker_b: float = 0.75
    reranker_dense_factor: int = 3  # dense-кандидатов = limit * factor перед BM25

    # Кэш
    cache_enabled: bool = True
    cache_maxsize: int = 256
    cache_ttl: int = 300  # секунд

    # Лимиты
    search_limit_max: int = 20
    context_max_tokens: int = 8000

    def __post_init__(self) -> None:
        if not self.chroma_path:
            project_root = Path(__file__).parent.parent
            self.chroma_path = str(project_root / "chroma_db")

    @classmethod
    def from_env(cls) -> RagConfig:
        """Создать конфиг из переменных окружения."""
        return cls(
            # Провайдер эмбеддингов
            embedding_provider=os.environ.get("RAG_EMBEDDING_PROVIDER", "local"),
            embedding_model=os.environ.get(
                "RAG_EMBEDDING_MODEL",
                "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            ),
            embedding_batch_size=int(os.environ.get("RAG_EMBEDDING_BATCH_SIZE", "64")),
            embedding_device=os.environ.get("RAG_DEVICE", "cpu"),
            embedding_local_files_only=os.environ.get("RAG_LOCAL_FILES_ONLY", "0")
            == "1",
            embedding_query_prefix=os.environ.get("RAG_QUERY_PREFIX", ""),
            embedding_passage_prefix=os.environ.get("RAG_PASSAGE_PREFIX", ""),
            embedding_api_key=os.environ.get("RAG_EMBEDDING_API_KEY"),
            embedding_api_base=os.environ.get("RAG_EMBEDDING_API_BASE"),
            embedding_dimensions=int(
                os.environ.get("RAG_EMBEDDING_DIMENSIONS", "1536")
            ),
            # Чанкинг
            chunker_type=os.environ.get("RAG_CHUNKER_TYPE", "semantic"),
            chunk_size=int(os.environ.get("RAG_CHUNK_SIZE", "768")),
            chunk_overlap=int(os.environ.get("RAG_CHUNK_OVERLAP", "160")),
            page_overlap_tokens=int(os.environ.get("RAG_PAGE_OVERLAP_TOKENS", "50")),
            # Reranker
            reranker_enabled=os.environ.get("RAG_RERANKER_ENABLED", "1") == "1",
            reranker_k1=float(os.environ.get("RAG_RERANKER_K1", "1.5")),
            reranker_b=float(os.environ.get("RAG_RERANKER_B", "0.75")),
            reranker_dense_factor=int(os.environ.get("RAG_RERANKER_DENSE_FACTOR", "3")),
            # Кэш
            cache_enabled=os.environ.get("RAG_CACHE_ENABLED", "1") == "1",
            cache_maxsize=int(os.environ.get("RAG_CACHE_MAXSIZE", "256")),
            cache_ttl=int(os.environ.get("RAG_CACHE_TTL", "300")),
            # Хранилища
            chroma_path=os.environ.get("CHROMA_PATH", ""),
            chroma_collection=os.environ.get(
                "CHROMA_COLLECTION", "university_documents"
            ),
            rag_db_path=os.environ.get("RAG_DB_PATH", ""),
            # Лимиты
            context_max_tokens=int(os.environ.get("RAG_CONTEXT_MAX_TOKENS", "8000")),
        )
