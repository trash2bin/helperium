"""Domain facade for persistent chat history and accepted user-turn state."""

from __future__ import annotations

import logging
import threading
from copy import deepcopy
from typing import Any

from helperium_sdk.settings import project_root, settings

from .session_repository import (
    SessionAbuseState,
    SessionRepository,
    SQLiteSessionRepository,
    create_sqlite_connection,
)

PROJECT_ROOT = project_root()
logger = logging.getLogger("api_service.sessions")


class SessionStore:
    """Keep compact transcript history and delegate persistence to a repository.

    ``SessionRepository`` owns database-specific schema/migration and SQL. This
    facade owns message compaction, session-id normalization, and the domain
    invariant that one *accepted ingress user request* is one user turn. That
    marker is shared by current anti-abuse quota/interval checks and any future
    user-turn-based session metric; persisted assistant/tool evidence never
    increments it.
    """

    def __init__(
        self,
        repository: SessionRepository,
        *,
        max_content_chars: int,
    ) -> None:
        self._repository = repository
        self.max_content_chars = max(1, max_content_chars)
        self._lock = threading.RLock()

    def history_messages(self, session_id: str) -> list[dict[str, Any]]:
        turns = self.get_turns(session_id)
        return [self._compact_message(message) for turn in turns for message in turn]

    def get_turns(self, session_id: str) -> list[list[dict[str, Any]]]:
        session_id = self.normalize_session_id(session_id)
        with self._lock:
            turns = self._repository.read_turns(session_id)

        return [
            [self._compact_message(message) for message in turn]
            for turn in turns
            if self._is_turn(turn)
        ]

    def append_turn(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        """Persist transcript evidence without altering accepted user-turn state."""
        session_id = self.normalize_session_id(session_id)
        turn = self._prepare_turn(messages)
        if not turn:
            return
        with self._lock:
            self._repository.append_turn(session_id, turn)

    def accept_user_turn(
        self, session_id: str, accepted_at: float
    ) -> SessionAbuseState:
        """Record one admitted user request before provider/MCP work begins."""
        session_id = self.normalize_session_id(session_id)
        with self._lock:
            return self._repository.accepted_user_turn(session_id, accepted_at)

    def abuse_state(self, session_id: str) -> SessionAbuseState:
        """Return durable user-turn state for request anti-abuse.

        Legacy session rows are lazily backfilled by the persistence adapter from
        retained transcript evidence. New requests use the accepted-at marker and
        are intentionally independent of transcript trimming or provider failure.
        """
        session_id = self.normalize_session_id(session_id)
        with self._lock:
            return self._repository.abuse_state(session_id)

    @staticmethod
    def normalize_session_id(session_id: str) -> str:
        normalized = str(session_id or "").strip()
        return normalized[:128] if normalized else "default"

    def _prepare_turn(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        filtered: list[dict[str, Any]] = []
        for message in messages:
            clean = self._compact_message(message)
            if clean.get("role") == "assistant":
                has_content = bool((clean.get("content") or "").strip())
                has_tool_calls = bool(clean.get("tool_calls"))
                if not has_content and not has_tool_calls:
                    continue
            filtered.append(clean)
        return filtered

    def _compact_message(self, message: dict[str, Any]) -> dict[str, Any]:
        compact = {
            key: deepcopy(value)
            for key, value in message.items()
            if key != "reasoning_content"
        }
        content = compact.get("content")
        if isinstance(content, str) and len(content) > self.max_content_chars:
            compact["content"] = (
                content[: self.max_content_chars]
                + "\n\n...[обрезано в истории диалога]"
            )
        return compact

    @staticmethod
    def _is_turn(value: Any) -> bool:
        return isinstance(value, list) and all(isinstance(item, dict) for item in value)


session_store = SessionStore(
    repository=SQLiteSessionRepository(
        connection_factory=lambda: create_sqlite_connection(settings.session_db_path),
        max_turns=settings.history_turns,
    ),
    max_content_chars=settings.history_content_chars,
)
