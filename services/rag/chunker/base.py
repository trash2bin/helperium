"""Базовый чанкер — оркестратор стратегий."""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from rag.config import RagConfig
from rag._types import PageDict, ChunkDict
from rag.utils import normalize_text

logger = logging.getLogger(__name__)


@runtime_checkable
class ChunkerStrategy(Protocol):
    """Интерфейс для стратегий чанкинга."""

    def chunk(self, text: str) -> list[str]:
        """Разбить текст на чанки."""
        ...


class TextChunker:
    """Чанкер с постраничной обработкой и overlap между страницами."""

    def __init__(
        self, config: RagConfig, strategy: ChunkerStrategy | None = None
    ) -> None:
        self.config = config
        self.strategy = strategy or self._create_strategy(config)

    @staticmethod
    def _create_strategy(config: RagConfig) -> ChunkerStrategy:
        """Фабрика стратегий."""
        if config.chunker_type == "semantic":
            from rag.chunker.semantic import SemanticChunkerStrategy

            return SemanticChunkerStrategy(config)
        elif config.chunker_type == "recursive":
            from rag.chunker.recursive import RecursiveChunkerStrategy

            return RecursiveChunkerStrategy(config)
        elif config.chunker_type == "sentence":
            from rag.chunker.sentence import SentenceChunkerStrategy

            return SentenceChunkerStrategy(config)
        else:
            logger.warning(
                "Unknown chunker type '%s', falling back to semantic",
                config.chunker_type,
            )
            from rag.chunker.semantic import SemanticChunkerStrategy

            return SemanticChunkerStrategy(config)

    def chunk_pages(self, pages: list[PageDict]) -> list[ChunkDict]:
        """Разбить страницы на чанки с overlap между страницами."""
        all_chunks: list[ChunkDict] = []
        previous_page_tail: str | None = None

        for page in pages:
            text = normalize_text(str(page.get("text") or ""))
            if not text:
                continue

            # Добавляем хвост предыдущей страницы
            if previous_page_tail:
                text = previous_page_tail + " " + text

            # Чанкаем
            chunk_texts = self.strategy.chunk(text)

            for chunk_text in chunk_texts:
                if not chunk_text:
                    continue

                # Убираем overlap-часть из результата
                if previous_page_tail and chunk_text.startswith(
                    previous_page_tail[:20]
                ):
                    chunk_text = chunk_text[len(previous_page_tail) :].strip()
                    if not chunk_text:
                        continue

                all_chunks.append(
                    {
                        "page": page.get("page"),
                        "content": chunk_text,
                    }
                )

            # Сохраняем хвост для overlap
            words = text.split()
            if len(words) > self.config.page_overlap_tokens:
                previous_page_tail = " ".join(words[-self.config.page_overlap_tokens :])
            else:
                previous_page_tail = text

        return all_chunks
