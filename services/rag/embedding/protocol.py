"""Протокол сервиса эмбеддингов."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProtocol(Protocol):
    """Протокол сервиса эмбеддингов.

    Позволяет подменять реализацию (локально → remote → микросервис)
    без изменения кода пайплайна.
    """

    def encode_batched(
        self,
        texts: list[str],
        mode: str = "passage",
    ) -> list[list[float]]:
        """Векторизовать список строк с батчингом.

        Args:
            texts: список строк для векторизации
            mode: "query" (для поискового запроса) или "passage" (для документов).
                  Некоторые модели (e5, bge) используют разные префиксы.
        """
        ...
