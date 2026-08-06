"""Рекурсивный чанкинг по разделителям. Чистая Python-реализация без langchain."""

from __future__ import annotations

from rag.config import RagConfig


class RecursiveChunkerStrategy:
    """Рекурсивный чанкинг по разделителям."""

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
            candidate = (
                (current_chunk + separator + piece).strip()
                if current_chunk
                else piece.strip()
            )

            if len(candidate) <= self.config.chunk_size:
                current_chunk = candidate
            else:
                if current_chunk:
                    final_chunks.append(current_chunk)

                # Если кусок сам по себе больше лимита — бьём рекурсивно
                if len(piece.strip()) > self.config.chunk_size:
                    remaining_seps = separators[separators.index(separator) + 1 :]
                    if remaining_seps:
                        final_chunks.extend(
                            self._split_recursive(piece.strip(), remaining_seps)
                        )
                    else:
                        # Жёсткая нарезка по символам (fallback)
                        for i in range(0, len(piece), self.config.chunk_size):
                            final_chunks.append(
                                piece[i : i + self.config.chunk_size].strip()
                            )
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
