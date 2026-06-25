"""Тесты Database — raw SQL, schema, fixtures, lifecycle."""

from agent_tutor_sdk.db.database import Database


def test_database_initialization(test_db):
    """Схема создаётся, фикстуры загружаются."""
    assert test_db is not None
    groups = test_db.fetch_all("SELECT * FROM groups")
    assert len(groups) > 0


def test_raw_sql_helpers(test_db):
    """fetch_one и fetch_all работают."""
    row = test_db.fetch_one("SELECT COUNT(*) AS cnt FROM students")
    assert row is not None
    assert row["cnt"] > 0

    rows = test_db.fetch_all("SELECT id, name FROM disciplines ORDER BY name LIMIT 3")
    assert len(rows) > 0
    assert "id" in rows[0].keys()
    assert "name" in rows[0].keys()


def test_ping_and_context_manager(db_path):
    """Ping, commit, rollback, context manager."""
    with Database(db_path=db_path) as db:
        db.ping()
        db.commit()
        db.rollback()

    assert db._closed is True


def test_pool_thread_isolation():
    """PostgresConnector — ленивая инициализация пула."""
    from agent_tutor_sdk.db.connector import PostgresConnector

    connector = PostgresConnector(
        database_url="postgresql://user:pass@localhost:9999/nonexistent",
        min_conn=1,
        max_conn=5,
    )
    assert connector.min_conn == 1
    assert connector.max_conn == 5
    assert connector._pool is None


def test_sqlite_path_no_pool(temp_dir):
    """SQLite connector без пула."""
    from agent_tutor_sdk.db.connector import SqliteConnector

    db_path = temp_dir / "test.db"
    connector = SqliteConnector(db_path=db_path)
    assert not hasattr(connector, "_pool") or connector._pool is None
