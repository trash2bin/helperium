"""Redis-реализация CacheProtocol.

Для переключения с LocalTTLCache на Redis нужно только заменить
экземпляр кэша — CacheProtocol интерфейс остаётся тем же.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from rag.cache.protocol import CacheProtocol

logger = logging.getLogger(__name__)


class RedisCache(CacheProtocol):
    """Redis-кэш для результатов RAG поиска.

    Использует Redis для хранения сериализованных результатов.
    Подходит для multi-instance deployments.

    TTL на уровне Redis (SETEX).
    """

    def __init__(self, redis_client: Any, ttl: int = 300) -> None:
        """Args:
        redis_client: redis.Redis instance (синхронный)
        ttl: время жизни записи в секундах
        """
        self._redis = redis_client
        self._ttl = ttl
        self._prefix = "RAG:CACHE:"

    def _key(self, query: str, discipline_id: str | None, limit: int = 5) -> str:
        query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
        return f"{self._prefix}{query_hash}::{discipline_id or ''}::{limit}"

    def get_cached_search(
        self, query: str, discipline_id: str | None, limit: int
    ) -> list | None:
        key = self._key(query, discipline_id, limit)
        try:
            raw = self._redis.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:
            logger.warning("Redis get failed: %s", exc)
            return None

    def set_cached_search(
        self, query: str, discipline_id: str | None, results: list
    ) -> None:
        key = self._key(query, discipline_id)
        try:
            raw = json.dumps(results, default=str)
            self._redis.set(key, raw, ex=self._ttl)
        except Exception as exc:
            logger.warning("Redis set failed: %s", exc)

    def invalidate(self, query: str | None = None) -> None:
        try:
            if query is None:
                # Clear entire cache
                pattern = f"{self._prefix}*"
            else:
                query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
                pattern = f"{self._prefix}{query_hash}*"

            cursor = 0
            while True:
                cursor, keys = self._redis.scan(cursor, match=pattern, count=100)
                if keys:
                    self._redis.delete(*keys)
                if cursor == 0:
                    break
        except Exception as exc:
            logger.warning("Redis scan/delete failed: %s", exc)

    def clear(self) -> None:
        self.invalidate(None)
