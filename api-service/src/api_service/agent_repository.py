"""Agent Repository — абстракция для хранения агентов.

Определяет интерфейс AgentRepository и реализацию SqliteAgentRepository.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)


# ── Encryption helpers ──


def _get_fernet() -> Fernet | None:
    """Build a Fernet cipher from ENCRYPTION_KEY env var (base64, 32 bytes).
    Returns None if the env var is not set (backward compat for dev).
    """
    key = os.environ.get("ENCRYPTION_KEY")
    if not key:
        logger.warning("ENCRYPTION_KEY not set — llm_config stored as plaintext")
        return None
    try:
        return Fernet(key.encode("ascii"))
    except Exception as exc:
        logger.warning(
            "Invalid ENCRYPTION_KEY — llm_config stored as plaintext: %s", exc
        )
        return None


_FERNET = _get_fernet()


def _encrypt_value(value: str | None) -> str | None:
    """Encrypt a JSON string with Fernet, return base64 ciphertext (or None)."""
    if value is None:
        return None
    if _FERNET is None:
        return value  # plaintext fallback
    return _FERNET.encrypt(value.encode("utf-8")).decode("ascii")


def _decrypt_value(encrypted: str | None) -> str | None:
    """Decrypt a base64 ciphertext back to the original JSON string (or None)."""
    if encrypted is None:
        return None
    if _FERNET is None:
        return encrypted  # plaintext fallback
    try:
        return _FERNET.decrypt(encrypted.encode("ascii")).decode("utf-8")
    except Exception as exc:
        logger.warning("Failed to decrypt llm_config: %s", exc)
        return None


def _json_or_none(val: Any) -> str | None:
    """Serialize a dict to a JSON string, or None if null/empty."""
    if val is None:
        return None
    if isinstance(val, str):
        return val  # already serialized
    s = json.dumps(val, ensure_ascii=False)
    return s if s != "null" else None


def _unpack_json(val: Any) -> Any:
    """Deserialize a JSON string if it's a string, otherwise pass through."""
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return None
    return val


def _parse_config(val: Any) -> dict | None:
    """Parse a config field (JSON string or None) into a dict or None."""
    if val is None:
        return None
    if isinstance(val, dict):
        return val
    return _unpack_json(val)


# ── Abstract repository ──


class AgentRepository(ABC):
    """Repository interface for agent definitions."""

    @abstractmethod
    def list_agents(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def get_agent(self, name: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def create_agent(
        self,
        name: str,
        description: str = "",
        tenant_ids: list[str] | None = None,
        widget_config: dict | None = None,
        llm_config: dict | None = None,
        provider_priority: list[str] | None = None,
        abuse_config: dict | None = None,
        system_prompt: str | None = None,
    ) -> dict[str, Any]: ...

    @abstractmethod
    def update_agent(
        self,
        name: str,
        description: str | None = None,
        tenant_ids: list[str] | None = None,
        widget_config: dict | None = None,
        llm_config: dict | None = None,
        provider_priority: list[str] | None = None,
        abuse_config: dict | None = None,
        system_prompt: str | None = None,
    ) -> dict[str, Any] | None: ...

    @abstractmethod
    def delete_agent(self, name: str) -> bool: ...


# ── SQLite implementation ──


class SqliteAgentRepository(AgentRepository):
    """Thread-safe SQLite store for agent definitions.

    Таблица `agents`:
      name         TEXT PRIMARY KEY — уникальное имя агента
      description  TEXT — человекочитаемое описание
      tenant_ids   TEXT — JSON-массив tenant_id (пример: '["tenant-a","tenant-b"]')
      widget_config TEXT — JSON-объект конфигурации embed-виджета (опционально)
      llm_config   TEXT — JSON-объект per-agent LLM конфигурации (опционально)
      provider_priority TEXT — JSON-массив имён провайдеров по приоритету
      abuse_config TEXT — JSON-объект per-agent abuse override (опционально)
      system_prompt TEXT — кастомный system prompt для агента (опционально, plaintext)
      created_at   TEXT — ISO timestamp
      updated_at   TEXT — ISO timestamp
    """

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
                        name         TEXT PRIMARY KEY,
                        description  TEXT NOT NULL DEFAULT '',
                        tenant_ids   TEXT NOT NULL DEFAULT '[]',
                        widget_config TEXT,
                        llm_config   TEXT,
                        provider_priority TEXT NOT NULL DEFAULT '[]',
                        created_at   TEXT NOT NULL,
                        updated_at   TEXT NOT NULL
                    )
                """)
                conn.commit()

                # Backward-compat migration: add columns if upgrading from old schema
                for col in (
                    "widget_config",
                    "llm_config",
                    "provider_priority",
                    "abuse_config",
                    "system_prompt",
                ):
                    try:
                        conn.execute(f"ALTER TABLE agents ADD COLUMN {col} TEXT")
                    except sqlite3.OperationalError:
                        pass  # column already exists
            finally:
                conn.close()

    # ── CRUD ──

    def list_agents(self) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT name, description, tenant_ids, widget_config, llm_config, provider_priority, abuse_config, system_prompt, created_at, updated_at "
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
                    "SELECT name, description, tenant_ids, widget_config, llm_config, provider_priority, abuse_config, system_prompt, created_at, updated_at "
                    "FROM agents WHERE name = ?",
                    (name,),
                ).fetchone()
                return self._row_to_dict(row) if row else None
            finally:
                conn.close()

    def create_agent(
        self,
        name: str,
        description: str = "",
        tenant_ids: list[str] | None = None,
        widget_config: dict | None = None,
        llm_config: dict | None = None,
        provider_priority: list[str] | None = None,
        abuse_config: dict | None = None,
        system_prompt: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        tenant_ids_json = json.dumps(tenant_ids or [], ensure_ascii=False)
        widget_config_json = _json_or_none(widget_config)
        llm_config_json = _encrypt_value(_json_or_none(llm_config))
        provider_priority_json = json.dumps(provider_priority or [], ensure_ascii=False)
        abuse_config_json = _json_or_none(abuse_config)
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO agents (name, description, tenant_ids, widget_config, llm_config, provider_priority, abuse_config, system_prompt, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        name,
                        description,
                        tenant_ids_json,
                        widget_config_json,
                        llm_config_json,
                        provider_priority_json,
                        abuse_config_json,
                        system_prompt,
                        now,
                        now,
                    ),
                )
                conn.commit()
                return {
                    "name": name,
                    "description": description,
                    "tenant_ids": tenant_ids or [],
                    "widget_config": _parse_config(widget_config),
                    "llm_config": _parse_config(llm_config),
                    "provider_priority": provider_priority or [],
                    "abuse_config": _parse_config(abuse_config),
                    "system_prompt": system_prompt,
                    "created_at": now,
                    "updated_at": now,
                }
            except sqlite3.IntegrityError:
                raise ValueError(f"Agent '{name}' already exists")
            finally:
                conn.close()

    def update_agent(
        self,
        name: str,
        description: str | None = None,
        tenant_ids: list[str] | None = None,
        widget_config: dict | None = None,
        llm_config: dict | None = None,
        provider_priority: list[str] | None = None,
        abuse_config: dict | None = None,
        system_prompt: str | None = None,
    ) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._conn()
            try:
                existing = conn.execute(
                    "SELECT name, description, tenant_ids, widget_config, llm_config, provider_priority, abuse_config, system_prompt, created_at, updated_at "
                    "FROM agents WHERE name = ?",
                    (name,),
                ).fetchone()
                if not existing:
                    return None

                new_description = (
                    description if description is not None else existing["description"]
                )
                new_tenant_ids = json.dumps(
                    tenant_ids
                    if tenant_ids is not None
                    else json.loads(existing["tenant_ids"]),
                    ensure_ascii=False,
                )
                new_widget_config = (
                    widget_config
                    if widget_config is not None
                    else existing["widget_config"]
                )
                existing_llm_decrypted = _decrypt_value(existing["llm_config"])
                new_llm_config = (
                    llm_config if llm_config is not None else existing_llm_decrypted
                )
                new_provider_priority = (
                    provider_priority
                    if provider_priority is not None
                    else (
                        json.loads(existing["provider_priority"])
                        if existing["provider_priority"]
                        else []
                    )
                )
                new_abuse_config = (
                    abuse_config
                    if abuse_config is not None
                    else existing["abuse_config"]
                )
                new_system_prompt = (
                    system_prompt
                    if system_prompt is not None
                    else existing["system_prompt"]
                )

                new_widget_config_json = _json_or_none(_unpack_json(new_widget_config))
                new_llm_config_json = _encrypt_value(
                    _json_or_none(_unpack_json(new_llm_config))
                )
                new_provider_priority_json = json.dumps(
                    new_provider_priority, ensure_ascii=False
                )
                new_abuse_config_json = _json_or_none(_unpack_json(new_abuse_config))

                conn.execute(
                    "UPDATE agents SET description = ?, tenant_ids = ?, widget_config = ?, llm_config = ?, provider_priority = ?, abuse_config = ?, system_prompt = ?, updated_at = ? WHERE name = ?",
                    (
                        new_description,
                        new_tenant_ids,
                        new_widget_config_json,
                        new_llm_config_json,
                        new_provider_priority_json,
                        new_abuse_config_json,
                        new_system_prompt,
                        now,
                        name,
                    ),
                )
                conn.commit()

                new_tenant_ids_list = (
                    tenant_ids
                    if tenant_ids is not None
                    else json.loads(existing["tenant_ids"])
                )
                return {
                    "name": name,
                    "description": new_description,
                    "tenant_ids": new_tenant_ids_list,
                    "widget_config": _parse_config(_unpack_json(new_widget_config)),
                    "llm_config": _parse_config(_unpack_json(new_llm_config)),
                    "provider_priority": new_provider_priority,
                    "abuse_config": _parse_config(_unpack_json(new_abuse_config)),
                    "system_prompt": new_system_prompt,
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
            "widget_config": _parse_config(row["widget_config"]),
            "llm_config": _parse_config(_decrypt_value(row["llm_config"])),
            "provider_priority": json.loads(row["provider_priority"])
            if row["provider_priority"]
            else [],
            "abuse_config": _parse_config(row["abuse_config"]),
            "system_prompt": row["system_prompt"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
