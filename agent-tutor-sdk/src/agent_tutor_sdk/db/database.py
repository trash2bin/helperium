"""Фасад БД — абстракция над SQLite / PostgreSQL.

Предоставляет соединение, raw SQL-хелперы и загрузку схемы/фикстур.
Доменные методы (get_student, get_group, ...) удалены — теперь через data-service (Go).
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from agent_tutor_sdk.db.connector import (
    PROJECT_ROOT,
    Connector,
    create_connector,
    is_operational_error,
)
from agent_tutor_sdk.db.fixtures import load_fixtures
from agent_tutor_sdk.db.schema import create_schema

logger = logging.getLogger(__name__)

FIXTURES_PATH = PROJECT_ROOT / "fixtures.json"

_db_instance: Database | None = None
_db_lock = threading.RLock()


def get_db(load_seed_data: bool | None = None) -> Database:
    global _db_instance
    if _db_instance is None:
        with _db_lock:
            if _db_instance is None:
                _db_instance = Database(load_seed_data=load_seed_data)
    return _db_instance


def reset_db() -> None:
    global _db_instance
    with _db_lock:
        if _db_instance is not None:
            _db_instance.close()
            _db_instance = None


class Database:
    """Управление соединением с БД, схемой и raw SQL."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        connector: Connector | None = None,
        load_seed_data: bool | None = None,
        database_url: str | None = None,
    ) -> None:
        self.connector = connector or create_connector(database_url, db_path)
        self.conn = self.connector.connection
        self._closed = False
        self._adapt = self.connector.adapt_sql

        create_schema(self.conn, adapter=self._adapt)

        if load_seed_data is None:
            if self._has_data():
                logger.info("Database already has data — skipping seed fixtures")
            else:
                logger.info("Empty database — loading seed fixtures")
                load_fixtures(self.conn, FIXTURES_PATH, adapter=self._adapt)
        elif load_seed_data:
            load_fixtures(self.conn, FIXTURES_PATH, adapter=self._adapt)

    @property
    def db_path(self) -> str:
        if hasattr(self.connector, "db_path"):
            return str(self.connector.db_path)
        return self.connector.database_url

    # ── Raw SQL ──────────────────────────────────────────────────────

    def execute(self, sql: str, parameters: tuple[Any, ...] | list[Any] = ()) -> Any:
        adapted = self._adapt(sql)
        try:
            cursor = self.conn.cursor()
            cursor.execute(adapted, parameters)
            return cursor
        except Exception as exc:
            if is_operational_error(exc) and hasattr(self.connector, "reset_thread_connection"):
                logger.warning("DB connection lost in Database.execute, resetting: %s", exc)
                self.connector.reset_thread_connection()
            raise

    def fetch_one(self, sql: str, parameters: tuple[Any, ...] | list[Any] = ()) -> Any:
        return self.execute(sql, parameters).fetchone()

    def fetch_all(self, sql: str, parameters: tuple[Any, ...] | list[Any] = ()) -> list[Any]:
        return self.execute(sql, parameters).fetchall()

    # ── Helpers ──────────────────────────────────────────────────────

    def _has_data(self) -> bool:
        for table in ("groups", "students", "teachers", "disciplines"):
            try:
                cursor = self.execute(f"SELECT COUNT(*) AS cnt FROM {table}")
                row = cursor.fetchone()
                if row is not None:
                    cnt = row["cnt"] if hasattr(row, "keys") else row[0]
                    if cnt and int(cnt) > 0:
                        return True
            except Exception:
                pass
        return False

    # ── Lifecycle ────────────────────────────────────────────────────

    def ping(self) -> None:
        try:
            self.execute("SELECT 1")
        except Exception as exc:
            if is_operational_error(exc) and hasattr(self.connector, "reset_thread_connection"):
                self.connector.reset_thread_connection()
            raise

    def commit(self) -> None:
        try:
            self.conn.commit()
        except Exception as exc:
            if is_operational_error(exc) and hasattr(self.connector, "reset_thread_connection"):
                self.connector.reset_thread_connection()
            raise

    def rollback(self) -> None:
        try:
            self.conn.rollback()
        except Exception as exc:
            if is_operational_error(exc) and hasattr(self.connector, "reset_thread_connection"):
                self.connector.reset_thread_connection()
            raise

    def close(self) -> None:
        if not self._closed:
            self.connector.close()
            self._closed = True

    def __enter__(self) -> Database:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.close()
        return False
