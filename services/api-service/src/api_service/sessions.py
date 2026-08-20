from __future__ import annotations

import json
import logging
import threading
import time
import sqlite3
from copy import deepcopy
from typing import Any, Callable
from pathlib import Path


from helperium_sdk.settings import settings, project_root

PROJECT_ROOT = project_root()


logger = logging.getLogger("api_service.sessions")


def _create_sqlite_connection(db_path: str | Path) -> sqlite3.Connection:
    """Factory function to create a SQLite connection with proper setup.

    Can be replaced with PostgreSQL/MySQL/Redis connection factory in the future.

    Example for PostgreSQL:
        import psycopg2
        def _create_postgres_connection(dsn: str) -> psycopg2.connection:
            conn = psycopg2.connect(dsn)
            conn.autocommit = False
            return conn

    Example for MySQL:
        import mysql.connector
        def _create_mysql_connection(config: dict) -> mysql.connector.connection:
            return mysql.connector.connect(**config)
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class SessionStore:
    """Persistent chat session history for the demo agent.

    Uses dependency injection for database connection factory,
    allowing easy switching between SQLite, PostgreSQL, MySQL, etc.
    """

    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
        *,
        max_turns: int,
        max_content_chars: int,
    ) -> None:
        self._connection_factory = connection_factory
        self.max_turns = max(1, max_turns)
        self.max_content_chars = max(1, max_content_chars)
        self._lock = threading.RLock()

        self._init_schema()

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
        user_turn_increment = int(
            any(message.get("role") == "user" for message in turn)
        )
        last_user_turn_at = now if user_turn_increment else None

        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions(
                    session_id, created_at, updated_at, user_turn_count, last_user_turn_at
                ) VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    user_turn_count = sessions.user_turn_count + excluded.user_turn_count,
                    last_user_turn_at = CASE
                        WHEN excluded.last_user_turn_at IS NULL THEN sessions.last_user_turn_at
                        ELSE excluded.last_user_turn_at
                    END
                """,
                (session_id, now, now, user_turn_increment, last_user_turn_at),
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
                    updated_at REAL NOT NULL,
                    user_turn_count INTEGER NOT NULL DEFAULT 0,
                    last_user_turn_at REAL
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
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
            }
            if "user_turn_count" not in columns:
                conn.execute(
                    "ALTER TABLE sessions ADD COLUMN user_turn_count INTEGER NOT NULL DEFAULT 0"
                )
            if "last_user_turn_at" not in columns:
                conn.execute("ALTER TABLE sessions ADD COLUMN last_user_turn_at REAL")

    def abuse_state(self, session_id: str) -> tuple[int, float | None]:
        """Return durable prior user-turn quota state for request anti-abuse.

        Transcript history is trimmed independently for LLM context, so it cannot
        be used as a session quota counter. Assistant and tool messages never
        consume this user-turn budget.
        """
        session_id = self.normalize_session_id(session_id)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT user_turn_count, last_user_turn_at FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return 0, None
            count = row["user_turn_count"]
            last_user_turn_at = row["last_user_turn_at"]
            if isinstance(count, int) and count > 0:
                return count, last_user_turn_at

            # Existing databases gain the columns through ALTER TABLE with
            # zero/null defaults. Backfill on first anti-abuse access so active
            # legacy sessions cannot temporarily bypass quota or interval checks.
            rows = conn.execute(
                "SELECT created_at, messages_json FROM session_turns WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
            user_turn_count = 0
            latest_user_turn_at: float | None = None
            for turn_row in rows:
                try:
                    messages = json.loads(turn_row["messages_json"])
                except json.JSONDecodeError:
                    continue
                if isinstance(messages, list) and any(
                    isinstance(message, dict) and message.get("role") == "user"
                    for message in messages
                ):
                    user_turn_count += 1
                    created_at = turn_row["created_at"]
                    if isinstance(created_at, (int, float)):
                        latest_user_turn_at = float(created_at)
            conn.execute(
                "UPDATE sessions SET user_turn_count = ?, last_user_turn_at = ? WHERE session_id = ?",
                (user_turn_count, latest_user_turn_at, session_id),
            )
        return user_turn_count, latest_user_turn_at

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

    def _connect(self) -> sqlite3.Connection:
        return self._connection_factory()

    @staticmethod
    def _is_turn(value: Any) -> bool:
        return isinstance(value, list) and all(isinstance(item, dict) for item in value)


session_store = SessionStore(
    connection_factory=lambda: _create_sqlite_connection(settings.session_db_path),
    max_turns=settings.history_turns,
    max_content_chars=settings.history_content_chars,
)
