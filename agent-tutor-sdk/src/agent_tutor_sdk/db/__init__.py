"""DB layer — абстракция над SQLite / PostgreSQL.

Доменные модели удалены — используй agent_tutor_sdk.contracts.
Доменные репозитории удалены — используй agent_tutor_sdk.data_client.
"""

from agent_tutor_sdk.db.connector import (
    PROJECT_ROOT,
    DEFAULT_DB_PATH,
    Connector,
    SqliteConnector,
    PostgresConnector,
    create_connector,
    DBAPIConnection,
    DBAPICursor,
)
from agent_tutor_sdk.db.database import Database, get_db, reset_db

__all__ = [
    "PROJECT_ROOT",
    "DEFAULT_DB_PATH",
    "Connector",
    "SqliteConnector",
    "PostgresConnector",
    "create_connector",
    "DBAPIConnection",
    "DBAPICursor",
    "Database",
    "get_db",
    "reset_db",
]
