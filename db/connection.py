from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "university.db"


class SqliteConnector:
    """Owns SQLite connection setup and transaction boundaries."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        check_same_thread: bool = False,
        pragmas: Sequence[str] = ("PRAGMA foreign_keys = ON",),
    ) -> None:
        self.db_path = Path(db_path or os.environ.get("DB_PATH", DEFAULT_DB_PATH))
        self.check_same_thread = check_same_thread
        self.pragmas = tuple(pragmas)
        self._connection: sqlite3.Connection | None = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                self.db_path,
                check_same_thread=self.check_same_thread,
            )
            connection.row_factory = sqlite3.Row
            for pragma in self.pragmas:
                connection.execute(pragma)
            self._connection = connection
        return self._connection

    def connect(self) -> sqlite3.Connection:
        """Return a new short-lived connection with the same settings."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.db_path,
            check_same_thread=self.check_same_thread,
        )
        connection.row_factory = sqlite3.Row
        for pragma in self.pragmas:
            connection.execute(pragma)
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connection
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
