"""Конфигурация RAG-системы."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RagConfig:
    """Централизованная конфигурация RAG."""

    # Эмбеддинги
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_batch_size: int = 64
    embedding_device: str = "cpu"
    embedding_local_files_only: bool = False

    # Чанкинг
    chunker_type: str = "semantic"  # semantic | recursive | sentence
    chunk_size: int = 512
    chunk_overlap: int = 80
    page_overlap_tokens: int = 50  # overlap между страницами

    # ChromaDB
    chroma_path: str = ""
    chroma_collection: str = "university_documents"

    # Промпты
    rag_instruction: str = (
        "Ответь на вопрос только по найденным фрагментам документов. "
        "Если в контексте нет ответа, прямо скажи, что данных в документах недостаточно. "
        "Ссылайся на название документа и страницу, когда page заполнен."
    )

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
            embedding_model=os.environ.get(
                "RAG_EMBEDDING_MODEL",
                "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            ),
            embedding_batch_size=int(os.environ.get("RAG_EMBEDDING_BATCH_SIZE", "64")),
            embedding_device=os.environ.get("RAG_DEVICE", "cpu"),
            embedding_local_files_only=os.environ.get("RAG_LOCAL_FILES_ONLY", "0") == "1",
            chunker_type=os.environ.get("RAG_CHUNKER_TYPE", "semantic"),
            chunk_size=int(os.environ.get("RAG_CHUNK_SIZE", "512")),
            chunk_overlap=int(os.environ.get("RAG_CHUNK_OVERLAP", "80")),
            page_overlap_tokens=int(os.environ.get("RAG_PAGE_OVERLAP_TOKENS", "50")),
            chroma_path=os.environ.get("CHROMA_PATH", ""),
            chroma_collection=os.environ.get(
                "CHROMA_COLLECTION", "university_documents"
            ),
            context_max_tokens=int(os.environ.get("RAG_CONTEXT_MAX_TOKENS", "8000")),
        )
