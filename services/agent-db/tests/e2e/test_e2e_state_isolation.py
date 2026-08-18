"""Regression tests for E2E filesystem isolation and cleanup."""

from __future__ import annotations

from pathlib import Path

from tests.e2e.helpers import cleanup_db, tenants_data_dir


def test_cleanup_db_removes_sqlite_database_and_sidecars(tmp_path: Path) -> None:
    """A completed E2E run must not leave WAL/SHM files behind."""

    database = tmp_path / "tenant.db"
    sidecars = [
        database,
        database.with_name("tenant.db-wal"),
        database.with_name("tenant.db-shm"),
    ]
    for path in sidecars:
        path.write_bytes(b"test")

    cleanup_db(database)

    assert all(not path.exists() for path in sidecars)


def test_tenants_data_dir_uses_isolated_environment_path(monkeypatch) -> None:
    """Docker CI can direct persistence checks away from project .data."""

    monkeypatch.setenv("E2E_TENANTS_DIR", "/e2e/tenants")

    assert tenants_data_dir() == Path("/e2e/tenants")
