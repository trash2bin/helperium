"""Persistence for widget problem reports (public ``POST /api/reports``).

Reports are the operator's morning-review surface: a visitor flags a bad
answer, api-service stores it with as much reproduction context as the widget
can provide (transcript, tools, last error, correlation id). Schema, migration
and SQL live in the SQLite adapter, mirroring :mod:`api_service.session_repository`.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Protocol

from helperium_sdk.settings import settings

logger = logging.getLogger("api_service.reports")

REPORT_STATUSES = ("new", "reviewed")


class ReportRepository(Protocol):
    """Storage boundary for widget problem reports."""

    def insert(self, record: dict[str, Any]) -> None: ...

    def list_reports(
        self, *, limit: int, status: str | None = None
    ) -> tuple[list[dict[str, Any]], int]: ...

    def set_status(self, report_id: str, status: str) -> bool: ...

    def cleanup_old(self) -> int: ...


def create_sqlite_connection(db_path: str | Path) -> sqlite3.Connection:
    """Create one SQLite connection with the report-store invariants."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class SQLiteReportRepository:
    """SQLite implementation of :class:`ReportRepository`."""

    _JSON_COLUMNS = ("message_tools", "display_names", "transcript")

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._init_schema()

    def insert(self, record: dict[str, Any]) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO reports(
                    id, created_at, status, agent, session_id, session_key, lang,
                    message_kind, message_text, message_tools, display_names,
                    transcript, comment, last_error_text, last_error_correlation_id,
                    page_url, correlation_id, client_ip, user_agent
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    record.get("created_at", now),
                    record.get("status", "new"),
                    record["agent"],
                    record["session_id"],
                    record["session_key"],
                    record.get("lang"),
                    record.get("message_kind"),
                    record.get("message_text"),
                    json.dumps(record.get("message_tools", []), ensure_ascii=False),
                    json.dumps(record.get("display_names", []), ensure_ascii=False),
                    json.dumps(record.get("transcript", []), ensure_ascii=False),
                    record.get("comment"),
                    record.get("last_error_text"),
                    record.get("last_error_correlation_id"),
                    record.get("page_url"),
                    record.get("correlation_id"),
                    record.get("client_ip"),
                    record.get("user_agent"),
                ),
            )

    def list_reports(
        self, *, limit: int, status: str | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        query = "SELECT * FROM reports"
        params: list[Any] = []
        if status is not None:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(max(1, limit))

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            count_query = "SELECT COUNT(*) AS total FROM reports"
            count_params: list[Any] = []
            if status is not None:
                count_query += " WHERE status = ?"
                count_params.append(status)
            total = conn.execute(count_query, count_params).fetchone()["total"]
        return [self._report_from_row(row) for row in rows], int(total)

    def set_status(self, report_id: str, status: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE reports SET status = ? WHERE id = ?",
                (status, report_id),
            )
            return cursor.rowcount > 0

    def cleanup_old(self) -> int:
        """Delete reports older than the retention window (0 disables it)."""
        days = settings.reports_retention_days
        if days <= 0:
            return 0
        cutoff = time.time() - days * 86400
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM reports WHERE created_at < ?", (cutoff,))
            removed = cursor.rowcount
        if removed:
            logger.info("Report retention removed %s records", removed)
        return removed

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS reports (
                    id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new',
                    agent TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    lang TEXT,
                    message_kind TEXT,
                    message_text TEXT,
                    message_tools TEXT NOT NULL DEFAULT '[]',
                    display_names TEXT NOT NULL DEFAULT '[]',
                    transcript TEXT NOT NULL DEFAULT '[]',
                    comment TEXT,
                    last_error_text TEXT,
                    last_error_correlation_id TEXT,
                    page_url TEXT,
                    correlation_id TEXT,
                    client_ip TEXT,
                    user_agent TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_reports_created_at
                    ON reports(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_reports_status
                    ON reports(status, created_at DESC);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return self._connection_factory()

    def _report_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        record: dict[str, Any] = {
            "id": row["id"],
            "created_at": row["created_at"],
            "status": row["status"],
            "agent": row["agent"],
            "session_id": row["session_id"],
            "session_key": row["session_key"],
            "lang": row["lang"],
            "message_kind": row["message_kind"],
            "message_text": row["message_text"],
            "comment": row["comment"],
            "last_error_text": row["last_error_text"],
            "last_error_correlation_id": row["last_error_correlation_id"],
            "page_url": row["page_url"],
            "correlation_id": row["correlation_id"],
            "client_ip": row["client_ip"],
            "user_agent": row["user_agent"],
        }
        for column in self._JSON_COLUMNS:
            try:
                record[column] = json.loads(row[column])
            except (json.JSONDecodeError, TypeError):
                record[column] = []
        return record


_report_store: SQLiteReportRepository | None = None


def get_report_store() -> SQLiteReportRepository:
    """Lazily construct the report store from ``settings.reports_db_path``."""
    global _report_store
    if _report_store is None:
        _report_store = SQLiteReportRepository(
            connection_factory=lambda: create_sqlite_connection(
                settings.reports_db_path
            )
        )
    return _report_store


def reset_report_store() -> None:
    """Drop the singleton so tests can re-resolve a throwaway path."""
    global _report_store
    _report_store = None
