"""Протокол кэша для результатов поиска/контекста.

Позволяет подменять реализацию (Local → Redis → Memcached → ...)
без изменения кода пайплайна.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CacheProtocol(Protocol):
    """Протокол кэша поисковых результатов."""

    def get_cached_search(
        self, query: str, discipline_id: str | None, limit: int
    ) -> list | None:
        """Получить закэшированный результат поиска.
        Возвращает None если промах.
        """
        ...

    def set_cached_search(
        self, query: str, discipline_id: str | None, results: list
    ) -> None:
        """Сохранить результат поиска в кэш."""
        ...

    def invalidate(self, query: str | None = None) -> None:
        """Инвалидировать кэш (по ключу или весь)."""
        ...

    def clear(self) -> None:
        """Очистить весь кэш."""
        ...
