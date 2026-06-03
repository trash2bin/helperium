from __future__ import annotations

import json
import logging
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from db.connection import SqliteConnector
from demo.settings import PROJECT_ROOT, settings

logger = logging.getLogger("demo.api.sessions")


class SessionStore:
    """Persistent chat session history for the demo agent."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        max_turns: int,
        max_content_chars: int,
        legacy_memory_path: str | Path | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.connector = SqliteConnector(
            self.db_path,
            check_same_thread=True,
            pragmas=("PRAGMA foreign_keys = ON",),
        )
        self.max_turns = max(1, max_turns)
        self.max_content_chars = max(1, max_content_chars)
        self.legacy_memory_path = Path(legacy_memory_path) if legacy_memory_path else None
        self._lock = threading.RLock()

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._migrate_legacy_memory()

    def history_messages(self, session_id: str) -> list[dict[str, Any]]:
        turns = self.get_turns(session_id)
        return [self._compact_message(message) for turn in turns for message in turn]

    def get_turns(self, session_id: str) -> list[list[dict[str, Any]]]:
        session_id = self.normalize_session_id(session_id)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT messages_json
                FROM session_turns
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()

        turns: list[list[dict[str, Any]]] = []
        for row in rows:
            try:
                messages = json.loads(row["messages_json"])
            except json.JSONDecodeError:
                logger.warning("Skipping broken session turn for %s", session_id)
                continue
            if self._is_turn(messages):
                turns.append([self._compact_message(message) for message in messages])
        return turns

    def append_turn(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        session_id = self.normalize_session_id(session_id)
        turn = self._prepare_turn(messages)
        if not turn:
            return

        now = time.time()
        payload = json.dumps(turn, ensure_ascii=False, separators=(",", ":"))

        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions(session_id, created_at, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (session_id, now, now),
            )
            conn.execute(
                """
                INSERT INTO session_turns(session_id, created_at, messages_json)
                VALUES(?, ?, ?)
                """,
                (session_id, now, payload),
            )
            self._trim_session(conn, session_id)

    @staticmethod
    def normalize_session_id(session_id: str) -> str:
        normalized = str(session_id or "").strip()
        return normalized[:128] if normalized else "default"

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS session_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    messages_json TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_session_turns_session_id_id
                    ON session_turns(session_id, id);
                """
            )

    def _migrate_legacy_memory(self) -> None:
        if not self.legacy_memory_path or not self.legacy_memory_path.exists():
            return

        with self._lock, self._connect() as conn:
            has_turns = conn.execute("SELECT 1 FROM session_turns LIMIT 1").fetchone()
            if has_turns:
                return

        try:
            data = json.loads(self.legacy_memory_path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Failed to read legacy agent memory")
            return

        if not isinstance(data, dict):
            return

        imported = 0
        for session_id, turns in data.items():
            if not isinstance(turns, list):
                continue
            for turn in turns[-self.max_turns :]:
                if self._is_turn(turn):
                    self.append_turn(str(session_id), turn)
                    imported += 1

        if imported:
            logger.info("Imported %s turns from legacy agent memory", imported)

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
        compact = {key: deepcopy(value) for key, value in message.items() if key != "reasoning_content"}
        content = compact.get("content")
        if isinstance(content, str) and len(content) > self.max_content_chars:
            compact["content"] = content[: self.max_content_chars] + "\n\n...[обрезано в истории диалога]"
        return compact

    def _trim_session(self, conn, session_id: str) -> None:
        conn.execute(
            """
            DELETE FROM session_turns
            WHERE session_id = ?
              AND id NOT IN (
                  SELECT id
                  FROM session_turns
                  WHERE session_id = ?
                  ORDER BY id DESC
                  LIMIT ?
              )
            """,
            (session_id, session_id, self.max_turns),
        )

    def _connect(self):
        return self.connector.connect()

    @staticmethod
    def _is_turn(value: Any) -> bool:
        return isinstance(value, list) and all(isinstance(item, dict) for item in value)


session_store = SessionStore(
    settings.session_db_path,
    max_turns=settings.history_turns,
    max_content_chars=settings.history_content_chars,
    legacy_memory_path=PROJECT_ROOT / ".agent_memory.json",
)
