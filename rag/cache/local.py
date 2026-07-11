"""Локальный TTL-кэш на cachetools."""

from __future__ import annotations

import threading

from cachetools import TTLCache

from rag.cache.protocol import CacheProtocol


class LocalTTLCache(CacheProtocol):
    """TTL-кэш для результатов RAG поиска.

    Хранит результаты в памяти потокобезопасно.
    TTL по умолчанию 5 минут.

    Ключ: query + discipline_id + limit.
    """

    def __init__(self, maxsize: int = 256, ttl: int = 300) -> None:
        """Args:
            maxsize: максимальное количество записей
            ttl: время жизни записи в секундах
        """
        self._cache: TTLCache[str, list] = TTLCache(maxsize=maxsize, ttl=ttl)
        self._lock = threading.RLock()

    @staticmethod
    def _key(query: str, discipline_id: str | None, limit: int = 5) -> str:
        return f"{query}::{discipline_id or ''}::{limit}"

    def get_cached_search(
        self, query: str, discipline_id: str | None, limit: int
    ) -> list | None:
        key = self._key(query, discipline_id, limit)
        with self._lock:
            return self._cache.get(key)

    def set_cached_search(
        self, query: str, discipline_id: str | None, results: list
    ) -> None:
        key = self._key(query, discipline_id)
        with self._lock:
            self._cache[key] = results

    def invalidate(self, query: str | None = None) -> None:
        with self._lock:
            if query is None:
                self._cache.clear()
            else:
                keys_to_remove = [k for k in self._cache if k.startswith(query)]
                for k in keys_to_remove:
                    del self._cache[k]

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
