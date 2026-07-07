"""Manages conversation history and session state."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

from .types import SessionId, TurnMessages
from api_service.sessions import session_store

logger = logging.getLogger("api_service.agent.conversation")


class ConversationManager:
    """Manages conversation history and session state.

    Sync-методы (get_history_messages, remember_turn) — прямые вызовы session_store
    (sync SQLite), работают в тестах и в CLI. Async-методы (aget_history_messages,
    aremember_turn) — обёртки через asyncio.to_thread, чтобы не блокировать event loop
    в long-running сервисах (api/mcp).
    """

    def __init__(self) -> None:
        self._session_locks: dict[str, asyncio.Lock] = {}

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

    def get_session_lock(self, session_id: SessionId) -> asyncio.Lock:
        """Get or create a lock for a session."""
        return self._session_locks.setdefault(session_id, asyncio.Lock())
