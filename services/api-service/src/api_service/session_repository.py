"""Persistence adapters for chat session history and accepted-turn anti-abuse state."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol


@dataclass(frozen=True)
class SessionAbuseState:
    """Durable state for the accepted user turns of one session."""

    user_turn_count: int = 0
    last_user_turn_at: float | None = None


class SessionRepository(Protocol):
    """Storage boundary for session history and accepted user-turn state."""

    def read_turns(self, session_id: str) -> list[list[dict[str, Any]]]: ...

    def append_turn(self, session_id: str, messages: list[dict[str, Any]]) -> None: ...

    def accepted_user_turn(
        self, session_id: str, accepted_at: float
    ) -> SessionAbuseState: ...

    def abuse_state(self, session_id: str) -> SessionAbuseState: ...


def create_sqlite_connection(db_path: str | Path) -> sqlite3.Connection:
    """Create one SQLite connection with the session-store invariants."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class SQLiteSessionRepository:
    """SQLite implementation of :class:`SessionRepository`.

    SQLite schema, migration and SQL stay in this adapter. The schema records an
    *accepted* user turn at ingress independently of later transcript persistence:
    response failures never refund anti-abuse quota or min-interval state.
    """

    def __init__(
        self, connection_factory: Callable[[], sqlite3.Connection], *, max_turns: int
    ) -> None:
        self._connection_factory = connection_factory
        self._max_turns = max(1, max_turns)
        self._init_schema()

    def read_turns(self, session_id: str) -> list[list[dict[str, Any]]]:
        with self._connect() as conn:
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
                continue
            if isinstance(messages, list) and all(
                isinstance(message, dict) for message in messages
            ):
                turns.append(messages)
        return turns

    def append_turn(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        now = time.time()
        payload = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as conn:
            self._ensure_session(conn, session_id, now)
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )
            conn.execute(
                """
                INSERT INTO session_turns(session_id, created_at, messages_json)
                VALUES(?, ?, ?)
                """,
                (session_id, now, payload),
            )
            self._trim_session(conn, session_id)

    def accepted_user_turn(
        self, session_id: str, accepted_at: float
    ) -> SessionAbuseState:
        """Atomically record an accepted ingress user turn before model execution."""
        with self._connect() as conn:
            self._backfill_legacy_state(conn, session_id)
            conn.execute(
                """
                INSERT INTO sessions(
                    session_id, created_at, updated_at, user_turn_count, last_user_turn_at
                ) VALUES(?, ?, ?, 1, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    user_turn_count = sessions.user_turn_count + 1,
                    last_user_turn_at = excluded.last_user_turn_at
                """,
                (session_id, accepted_at, accepted_at, accepted_at),
            )
            row = conn.execute(
                "SELECT user_turn_count, last_user_turn_at FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return self._state_from_row(row)

    def abuse_state(self, session_id: str) -> SessionAbuseState:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT user_turn_count, last_user_turn_at FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return SessionAbuseState()
            state = self._state_from_row(row)
            if state.user_turn_count > 0:
                return state
            state = self._backfill_legacy_state(conn, session_id)
        return state

    def _init_schema(self) -> None:
        with self._connect() as conn:
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

    @staticmethod
    def _ensure_session(conn: sqlite3.Connection, session_id: str, now: float) -> None:
        conn.execute(
            """
            INSERT INTO sessions(session_id, created_at, updated_at)
            VALUES(?, ?, ?)
            ON CONFLICT(session_id) DO NOTHING
            """,
            (session_id, now, now),
        )

    def _backfill_legacy_state(
        self, conn: sqlite3.Connection, session_id: str
    ) -> SessionAbuseState:
        """Persist historic state once before any new accepted ingress turn."""
        row = conn.execute(
            "SELECT user_turn_count, last_user_turn_at FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        state = self._state_from_row(row)
        if row is None or state.user_turn_count > 0:
            return state
        rows = conn.execute(
            """
            SELECT created_at, messages_json
            FROM session_turns
            WHERE session_id = ?
            ORDER BY id ASC
            """,
            (session_id,),
        ).fetchall()
        state = self._legacy_abuse_state(rows)
        conn.execute(
            """
            UPDATE sessions
            SET user_turn_count = ?, last_user_turn_at = ?
            WHERE session_id = ?
            """,
            (state.user_turn_count, state.last_user_turn_at, session_id),
        )
        return state

    def _trim_session(self, conn: sqlite3.Connection, session_id: str) -> None:
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
            (session_id, session_id, self._max_turns),
        )

    def _connect(self) -> sqlite3.Connection:
        return self._connection_factory()

    @staticmethod
    def _state_from_row(row: sqlite3.Row | None) -> SessionAbuseState:
        if row is None:
            return SessionAbuseState()
        count = row["user_turn_count"]
        timestamp = row["last_user_turn_at"]
        return SessionAbuseState(
            user_turn_count=count if isinstance(count, int) and count > 0 else 0,
            last_user_turn_at=(
                float(timestamp) if isinstance(timestamp, (int, float)) else None
            ),
        )

    @staticmethod
    def _legacy_abuse_state(rows: list[sqlite3.Row]) -> SessionAbuseState:
        user_turn_count = 0
        latest_user_turn_at: float | None = None
        for row in rows:
            try:
                messages = json.loads(row["messages_json"])
            except json.JSONDecodeError:
                continue
            if isinstance(messages, list) and any(
                isinstance(message, dict) and message.get("role") == "user"
                for message in messages
            ):
                user_turn_count += 1
                created_at = row["created_at"]
                if isinstance(created_at, (int, float)):
                    latest_user_turn_at = float(created_at)
        return SessionAbuseState(user_turn_count, latest_user_turn_at)
