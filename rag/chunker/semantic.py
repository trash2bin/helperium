"""Семантический чанкинг через chonkie."""

from __future__ import annotations

from rag.config import RagConfig


class SemanticChunkerStrategy:
    """Семантический чанкинг через chonkie."""

    def __init__(self, config: RagConfig) -> None:
        self.config = config
        self._chunker = None

    @property
    def chunker(self):
        if self._chunker is None:
            from chonkie import SemanticChunker

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
