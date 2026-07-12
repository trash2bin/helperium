"""Pydantic-модели для Admin API RAG-сервиса.

Используются в эндпоинтах /admin/config и /admin/stats.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AdminConfigResponse(BaseModel):
    """RAG-конфиг для админ-панели (embedding_api_key маскируется)."""

    embedding_provider: str = Field(
        default="local", description="Провайдер эмбеддингов: local | litellm"
    )
    embedding_model: str = Field(
        default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        description="Модель эмбеддингов",
    )
    embedding_batch_size: int = Field(default=64, description="Размер батча эмбеддингов")
    embedding_device: str = Field(default="cpu", description="Устройство: cpu | cuda")
    embedding_local_files_only: bool = Field(
        default=False, description="Только локальные файлы модели"
    )
    embedding_query_prefix: str = Field(default="", description="Префикс для query")
    embedding_passage_prefix: str = Field(default="", description="Префикс для passage")
    embedding_api_key: str | None = Field(
        default=None, description="API-ключ эмбеддингов (маскирован)"
    )
    embedding_api_base: str | None = Field(
        default=None, description="Базовый URL API эмбеддингов"
    )
    embedding_dimensions: int = Field(
        default=1536, description="Размерность вектора для API-провайдеров"
    )

    chunker_type: str = Field(default="semantic", description="Тип чанкера")
    chunk_size: int = Field(default=768, description="Размер чанка (токенов)")
    chunk_overlap: int = Field(default=160, description="Перекрытие чанков")
    page_overlap_tokens: int = Field(
        default=50, description="Перекрытие между страницами"
    )

    chroma_path: str = Field(default="", description="Путь к ChromaDB")
    chroma_collection: str = Field(
        default="university_documents", description="Коллекция ChromaDB"
    )
    rag_db_path: str = Field(default="", description="Путь к SQLite RAG")

    rag_instruction: str = Field(
        default="Ответь на вопрос только по найденным фрагментам документов.",
        description="Инструкция для LLM о том, как использовать RAG-контекст",
    )

    reranker_enabled: bool = Field(default=True, description="BM25 reranker включён")
    reranker_k1: float = Field(default=1.5, description="Параметр K1 BM25")
    reranker_b: float = Field(default=0.75, description="Параметр B BM25")
    reranker_dense_factor: int = Field(
        default=3, description="Множитель dense-кандидатов перед BM25"
    )

    cache_enabled: bool = Field(default=True, description="Кэш поиска включён")
    cache_maxsize: int = Field(default=256, description="Максимальный размер кэша")
    cache_ttl: int = Field(default=300, description="TTL кэша в секундах")

    search_limit_max: int = Field(default=20, description="Максимальный лимит поиска")
    context_max_tokens: int = Field(
        default=8000, description="Максимум токенов в контексте"
    )


class AdminConfigUpdateRequest(BaseModel):
    """Обновление RAG-конфига (все поля опциональны)."""

    embedding_provider: str | None = Field(default=None)
    embedding_model: str | None = Field(default=None)
    embedding_batch_size: int | None = Field(default=None)
    embedding_device: str | None = Field(default=None)
    embedding_local_files_only: bool | None = Field(default=None)
    embedding_query_prefix: str | None = Field(default=None)
    embedding_passage_prefix: str | None = Field(default=None)
    embedding_api_key: str | None = Field(
        default=None,
        description=(
            "API-ключ эмбеддингов. Если прислать \"***\" — оставить текущий. "
            "Если прислать пустую строку — очистить. Если прислать новый ключ — применить."
        ),
    )
    embedding_api_base: str | None = Field(default=None)
    embedding_dimensions: int | None = Field(default=None)

    chunker_type: str | None = Field(default=None)
    chunk_size: int | None = Field(default=None)
    chunk_overlap: int | None = Field(default=None)
    page_overlap_tokens: int | None = Field(default=None)

    chroma_collection: str | None = Field(default=None)

    rag_instruction: str | None = Field(default=None)

    reranker_enabled: bool | None = Field(default=None)
    reranker_k1: float | None = Field(default=None)
    reranker_b: float | None = Field(default=None)
    reranker_dense_factor: int | None = Field(default=None)

    cache_enabled: bool | None = Field(default=None)
    cache_maxsize: int | None = Field(default=None)
    cache_ttl: int | None = Field(default=None)

    search_limit_max: int | None = Field(default=None)
    context_max_tokens: int | None = Field(default=None)


class AdminStatsResponse(BaseModel):
    """Статистика RAG-сервиса."""

    document_count: int = Field(..., description="Количество документов в индексе")
    chunk_count: int = Field(..., description="Количество чанков в индексе")
    chroma_size_mb: float = Field(
        ..., description="Размер хранилища ChromaDB в мегабайтах"
    )
