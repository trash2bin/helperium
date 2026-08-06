"""Manages conversation history and session state."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

from .types import SessionId, TurnMessages
from api_service.sessions import session_store

logger = logging.getLogger("api_service.agent.conversation")


class _LRULockCache:
    """Dict-like lock cache with LRU eviction when max_size is exceeded.

    Thread-safe for asyncio: access guarded by an asyncio.Lock.
    """

    def __init__(self, max_size: int = 1000) -> None:
        self._max_size = max_size
        self._locks: dict[str, asyncio.Lock] = {}
        self._access_order: list[str] = []
        self._lock = asyncio.Lock()

    async def get_or_create(self, key: str) -> asyncio.Lock:
        async with self._lock:
            lock = self._locks.get(key)
            if lock is not None:
                # Move to front (most recently used)
                self._access_order.remove(key)
                self._access_order.append(key)
                return lock
            lock = asyncio.Lock()
            self._locks[key] = lock
            self._access_order.append(key)
            if len(self._access_order) > self._max_size:
                self._evict_one()
            return lock

    async def remove(self, key: str) -> None:
        async with self._lock:
            self._locks.pop(key, None)
            try:
                self._access_order.remove(key)
            except ValueError:
                pass

    async def size(self) -> int:
        async with self._lock:
            return len(self._locks)

    async def has_key(self, key: str) -> bool:
        async with self._lock:
            return key in self._locks

    async def keys(self) -> list[str]:
        async with self._lock:
            return list(self._locks.keys())

    def _evict_one(self) -> None:
        """Evict the least recently used lock (oldest access)."""
        if not self._access_order:
            return
        oldest = self._access_order.pop(0)
        self._locks.pop(oldest, None)


class ConversationManager:
    """Manages conversation history and session state.

    Sync-методы (get_history_messages, remember_turn) — прямые вызовы session_store
    (sync SQLite), работают в тестах и в CLI. Async-методы (aget_history_messages,
    aremember_turn) — обёртки через asyncio.to_thread, чтобы не блокировать event loop
    в long-running сервисах (api/mcp).
    """

    def __init__(self, max_session_locks: int = 1000) -> None:
        self._session_locks = _LRULockCache(max_size=max_session_locks)

    def get_history_messages(self, session_id: SessionId) -> list[dict[str, Any]]:
        """Get history messages for a session (sync, для тестов/CLI)."""
        return session_store.history_messages(session_id)

    async def aget_history_messages(
        self, session_id: SessionId
    ) -> list[dict[str, Any]]:
        """Async-обёртка: не блокирует event loop в long-running сервисах."""
        return await asyncio.to_thread(self.get_history_messages, session_id)

    def remember_turn(self, session_id: SessionId, messages: TurnMessages) -> None:
        """Save turn messages to session history (sync, для тестов/CLI)."""
        session_store.append_turn(session_id, cast(list[dict[str, Any]], messages))
        logger.debug("[CONVERSATION] Stored turn for session %s", session_id)

    async def aremember_turn(
        self, session_id: SessionId, messages: TurnMessages
    ) -> None:
        """Async-обёртка для remember_turn."""
        await asyncio.to_thread(self.remember_turn, session_id, messages)

    @staticmethod
    def normalize_session_id(session_id: str) -> SessionId:
        """Normalize session ID."""
        return session_store.normalize_session_id(session_id)

    async def get_session_lock(self, session_id: SessionId) -> asyncio.Lock:
        """Get or create a lock for a session."""
        return await self._session_locks.get_or_create(session_id)

    async def cleanup_session_lock(self, session_id: SessionId) -> None:
        """Remove a session lock, allowing its memory to be reclaimed.

        Call this when the SSE session ends or when a session is no longer
        needed. After cleanup, a subsequent ``get_session_lock`` will create
        a fresh lock.
        """
        await self._session_locks.remove(session_id)
