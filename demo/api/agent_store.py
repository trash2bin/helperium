"""SQLite-хранилище агентов.

Таблица `agents`:
  name        TEXT PRIMARY KEY — уникальное имя агента
  description TEXT — человекочитаемое описание
  tenant_ids  TEXT — JSON-массив tenant_id (пример: '["tenant-a","tenant-b"]')
  created_at  TEXT — ISO timestamp
  updated_at  TEXT — ISO timestamp
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any


class AgentStore:
    """Thread-safe SQLite store for agent definitions."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS agents (
                        name        TEXT PRIMARY KEY,
                        description TEXT NOT NULL DEFAULT '',
                        tenant_ids  TEXT NOT NULL DEFAULT '[]',
                        created_at  TEXT NOT NULL,
                        updated_at  TEXT NOT NULL
                    )
                """)
                conn.commit()
            finally:
                conn.close()

    # ── CRUD ──

    def list_agents(self) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT name, description, tenant_ids, created_at, updated_at "
                    "FROM agents ORDER BY created_at DESC"
                ).fetchall()
                return [self._row_to_dict(r) for r in rows]
            finally:
                conn.close()

    def get_agent(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT name, description, tenant_ids, created_at, updated_at "
                    "FROM agents WHERE name = ?", (name,)
                ).fetchone()
                return self._row_to_dict(row) if row else None
            finally:
                conn.close()

    def create_agent(self, name: str, description: str = "", tenant_ids: list[str] | None = None) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        tenant_ids_json = json.dumps(tenant_ids or [], ensure_ascii=False)
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO agents (name, description, tenant_ids, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (name, description, tenant_ids_json, now, now),
                )
                conn.commit()
                return {
                    "name": name,
                    "description": description,
                    "tenant_ids": tenant_ids or [],
                    "created_at": now,
                    "updated_at": now,
                }
            except sqlite3.IntegrityError:
                raise ValueError(f"Agent '{name}' already exists")
            finally:
                conn.close()

    def update_agent(self, name: str, description: str | None = None, tenant_ids: list[str] | None = None) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._conn()
            try:
                existing = conn.execute(
                    "SELECT name, description, tenant_ids, created_at, updated_at "
                    "FROM agents WHERE name = ?", (name,)
                ).fetchone()
                if not existing:
                    return None

                new_description = description if description is not None else existing["description"]
                new_tenant_ids = json.dumps(tenant_ids if tenant_ids is not None else json.loads(existing["tenant_ids"]), ensure_ascii=False)

                conn.execute(
                    "UPDATE agents SET description = ?, tenant_ids = ?, updated_at = ? WHERE name = ?",
                    (new_description, new_tenant_ids, now, name),
                )
                conn.commit()

                new_tenant_ids_list = tenant_ids if tenant_ids is not None else json.loads(existing["tenant_ids"])
                return {
                    "name": name,
                    "description": new_description,
                    "tenant_ids": new_tenant_ids_list,
                    "created_at": existing["created_at"],
                    "updated_at": now,
                }
            finally:
                conn.close()

    def delete_agent(self, name: str) -> bool:
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute("DELETE FROM agents WHERE name = ?", (name,))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    # ── helpers ──

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "name": row["name"],
            "description": row["description"],
            "tenant_ids": json.loads(row["tenant_ids"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
