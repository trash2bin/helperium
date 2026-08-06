"""Чанкинг по предложениям. Чистая regex-реализация без nltk."""

from __future__ import annotations

import re

from rag.config import RagConfig


class SentenceChunkerStrategy:
    """Чанкинг по предложениям через regex."""

    # Разбиваем по . ! ? с учётом пробелов и переносов строк
    SENTENCE_SPLIT_RE = re.compile(
        r"(?<=[.!?])\s+(?=[A-ZА-ЯЁ])|"  # Точка/воскл/вопрос + пробел + Заглавная
        r"(?<=[.!?])\n+"  # Точка/воскл/вопрос + перенос строки
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
        text = re.sub(r"\s+", " ", text).strip()
        sentences = self.SENTENCE_SPLIT_RE.split(text)
        return [s.strip() for s in sentences if s.strip()]

    def _apply_overlap(self, chunks: list[str]) -> list[str]:
        """Добавить перекрытие (последнее предложение предыдущего чанка)."""
        result = [chunks[0]]
        for i in range(1, len(chunks)):
            prev_sentences = self._split_sentences(chunks[i - 1])
            if prev_sentences:
                overlap = prev_sentences[-1]
                overlapped = (overlap + " " + chunks[i]).strip()
                if len(overlapped) <= self.config.chunk_size:
                    result.append(overlapped)
                else:
                    result.append(chunks[i])
            else:
                result.append(chunks[i])
        return result
