"""Лёгкий менеджер SQLite-соединения для RAG.

Не зависит от agent_tutor_sdk. Использует только стандартный sqlite3.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

from rag.documents_schema import create_rag_schema

logger = logging.getLogger(__name__)

DEFAULT_RAG_DB = Path(__file__).parent.parent / "rag_documents.db"


class RagDB:
    """Простое SQLite-соединение с авто-созданием схемы."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        path = Path(db_path or os.environ.get("RAG_DB_PATH", DEFAULT_RAG_DB))
        path.parent.mkdir(parents=True, exist_ok=True)

        logger.info("Opening RAG database: %s", path)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")

        create_rag_schema(self.conn)

    def ping(self) -> bool:
        try:
            self.conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    def close(self) -> None:
        logger.info("Closing RAG database")
        self.conn.close()
