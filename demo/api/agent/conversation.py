"""Manages conversation history and session state."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast
from weakref import WeakValueDictionary

from .types import SessionId, TurnMessages
from demo.api.sessions import session_store

logger = logging.getLogger("demo.api.agent.conversation")


class ConversationManager:
    """Manages conversation history and session state."""

    def __init__(self) -> None:
        self._session_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    def get_history_messages(self, session_id: SessionId) -> list[dict[str, Any]]:
        """Get history messages for a session."""
        return session_store.history_messages(session_id)

    def remember_turn(
        self, session_id: SessionId, messages: TurnMessages
    ) -> None:
        """Save turn messages to session history."""
        session_store.append_turn(session_id, cast(list[dict[str, Any]], messages))
        logger.debug("[CONVERSATION] Stored turn for session %s", session_id)

    @staticmethod
    def normalize_session_id(session_id: str) -> SessionId:
        """Normalize session ID."""
        return session_store.normalize_session_id(session_id)

    def get_session_lock(self, session_id: SessionId) -> asyncio.Lock:
        """Get or create a lock for a session."""
        return self._session_locks.setdefault(session_id, asyncio.Lock())
