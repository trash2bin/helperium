"""Чанкинг текста с поддержкой разных стратегий."""
from __future__ import annotations

import re

import logging
from typing import Protocol, runtime_checkable

from rag.config import RagConfig
from rag.models import PageDict, ChunkDict
from rag.utils import normalize_text

logger = logging.getLogger(__name__)


@runtime_checkable
class ChunkerStrategy(Protocol):
    """Интерфейс для стратегий чанкинга."""

    def chunk(self, text: str) -> list[str]:
        """Разбить текст на чанки."""
        ...


class SemanticChunkerStrategy:
    """Семантический чанкинг через chonkie."""

    def __init__(self, config: RagConfig) -> None:
        self.config = config
        self._chunker = None

    @property
    def chunker(self):
        if self._chunker is None:
            from chonkie import SemanticChunker

            # В новых версиях chonkie chunk_overlap это dict
            # Если overlap == 0, отключаем его явно
            if self.config.chunk_overlap > 0:
                overlap_config = {
                    "strategy": "token",
                    "value": self.config.chunk_overlap,
                }
            else:
                overlap_config = None

            self._chunker = SemanticChunker(
                embedding_model=self.config.embedding_model,
                chunk_size=self.config.chunk_size,
                chunk_overlap=overlap_config,  # type: ignore[arg-type]
            )
        return self._chunker

    def chunk(self, text: str) -> list[str]:
        chunks = self.chunker.chunk(text)
        return [ch.text.strip() for ch in chunks if ch.text.strip()]


class RecursiveChunkerStrategy:
    """
    Рекурсивный чанкинг по разделителям.
    Чистая Python-реализация без langchain.
    """

    SEPARATORS = ["\n\n", "\n", ". ", "! ", "? ", "; ", ", ", " ", ""]

    def __init__(self, config: RagConfig) -> None:
        self.config = config

    def chunk(self, text: str) -> list[str]:
        return self._split_recursive(text, self.SEPARATORS)

    def _split_recursive(self, text: str, separators: list[str]) -> list[str]:
        """Рекурсивно бьёт текст, начиная с самого крупного разделителя."""
        if len(text) <= self.config.chunk_size:
            return [text.strip()] if text.strip() else []

        final_chunks: list[str] = []

        # Находим подходящий разделитель
        separator = separators[-1]  # fallback — пустая строка
        for sep in separators:
            if sep == "":
                separator = sep
                break
            if sep in text:
                separator = sep
                break

        # Бьём по разделителю
        splits = text.split(separator) if separator else list(text)

        # Склеиваем куски в чанки нужного размера
        current_chunk = ""
        for piece in splits:
            candidate = (current_chunk + separator + piece).strip() if current_chunk else piece.strip()

            if len(candidate) <= self.config.chunk_size:
                current_chunk = candidate
            else:
                if current_chunk:
                    final_chunks.append(current_chunk)

                # Если кусок сам по себе больше лимита — бьём рекурсивно
                if len(piece.strip()) > self.config.chunk_size:
                    remaining_seps = separators[separators.index(separator) + 1 :]
                    if remaining_seps:
                        final_chunks.extend(self._split_recursive(piece.strip(), remaining_seps))
                    else:
                        # Жёсткая нарезка по символам (fallback)
                        for i in range(0, len(piece), self.config.chunk_size):
                            final_chunks.append(piece[i : i + self.config.chunk_size].strip())
                else:
                    current_chunk = piece.strip()
                    continue

                current_chunk = ""

        if current_chunk:
            final_chunks.append(current_chunk)

        # Применяем overlap
        if self.config.chunk_overlap > 0 and len(final_chunks) > 1:
            return self._apply_overlap(final_chunks)

        return [c for c in final_chunks if c]

    def _apply_overlap(self, chunks: list[str]) -> list[str]:
        """Добавить перекрытие между чанками."""
        result = [chunks[0]]
        for i in range(1, len(chunks)):
            # Берём последние N символов предыдущего чанка
            overlap = chunks[i - 1][-self.config.chunk_overlap :]
            overlapped = (overlap + " " + chunks[i]).strip()
            # Если не влезает — обрезаем overlap
            if len(overlapped) > self.config.chunk_size:
                overlapped = chunks[i]
            result.append(overlapped)
        return result


class SentenceChunkerStrategy:
    """
    Чанкинг по предложениям.
    Чистая regex-реализация без nltk.
    """

    # Разбиваем по . ! ? с учётом пробелов и переносов строк
    SENTENCE_SPLIT_RE = re.compile(
        r'(?<=[.!?])\s+(?=[A-ZА-ЯЁ])|'  # Точка/воскл/вопрос + пробел + Заглавная
        r'(?<=[.!?])\n+'                 # Точка/воскл/вопрос + перенос строки
    )

    def __init__(self, config: RagConfig) -> None:
        self.config = config

    def chunk(self, text: str) -> list[str]:
        sentences = self._split_sentences(text)

        chunks: list[str] = []
        current_chunk: list[str] = []
        current_len = 0

        for sent in sentences:
            sent_len = len(sent)
            if current_len + sent_len > self.config.chunk_size and current_chunk:
                chunks.append(" ".join(current_chunk))
                current_chunk = [sent]
                current_len = sent_len
            else:
                current_chunk.append(sent)
                current_len += sent_len

        if current_chunk:
            chunks.append(" ".join(current_chunk))

        # Применяем overlap между чанками
        if self.config.chunk_overlap > 0 and len(chunks) > 1:
            return self._apply_overlap(chunks)

        return chunks

    def _split_sentences(self, text: str) -> list[str]:
        """Разбить текст на предложения регуляркой."""
        # Заменяем множественные пробелы
        text = re.sub(r'\s+', ' ', text).strip()

        sentences = self.SENTENCE_SPLIT_RE.split(text)
        return [s.strip() for s in sentences if s.strip()]

    def _apply_overlap(self, chunks: list[str]) -> list[str]:
        """Добавить перекрытие (последнее предложение предыдущего чанка)."""
        result = [chunks[0]]
        for i in range(1, len(chunks)):
            prev_sentences = self._split_sentences(chunks[i - 1])
            if prev_sentences:
                overlap = prev_sentences[-1]  # Последнее предложение
                overlapped = (overlap + " " + chunks[i]).strip()
                if len(overlapped) <= self.config.chunk_size:
                    result.append(overlapped)
                else:
                    result.append(chunks[i])
            else:
                result.append(chunks[i])
        return result

class TextChunker:
    """Чанкер с постраничной обработкой и overlap между страницами."""

    def __init__(self, config: RagConfig, strategy: ChunkerStrategy | None = None) -> None:
        self.config = config
        self.strategy = strategy or self._create_strategy(config)

    @staticmethod
    def _create_strategy(config: RagConfig) -> ChunkerStrategy:
        """Фабрика стратегий."""
        if config.chunker_type == "semantic":
            return SemanticChunkerStrategy(config)
        elif config.chunker_type == "recursive":
            return RecursiveChunkerStrategy(config)
        elif config.chunker_type == "sentence":
            return SentenceChunkerStrategy(config)
        else:
            logger.warning(
                "Unknown chunker type '%s', falling back to semantic",
                config.chunker_type,
            )
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
                if previous_page_tail and chunk_text.startswith(previous_page_tail[:20]):
                    chunk_text = chunk_text[len(previous_page_tail):].strip()
                    if not chunk_text:
                        continue

                all_chunks.append({
                    "page": page.get("page"),
                    "content": chunk_text,
                })

            # Сохраняем хвост для overlap
            words = text.split()
            if len(words) > self.config.page_overlap_tokens:
                previous_page_tail = " ".join(words[-self.config.page_overlap_tokens:])
            else:
                previous_page_tail = text

        return all_chunks
