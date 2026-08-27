"""Regression tests for E2E filesystem isolation and cleanup."""

from __future__ import annotations

import os
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


def test_test_profile_exposes_matching_secure_service_credentials() -> None:
    """The E2E caller must exercise, not bypass, the secure service contracts."""

    assert os.environ["MCP_DEV"] == "false"
    assert os.environ["MCP_REQUIRE_AUTH"] == "true"
    assert os.environ["MCP_API_KEY"]
    assert os.environ["MCP_CLIENT_API_KEY"] == os.environ["MCP_API_KEY"]
    assert os.environ["MCP_ALLOWED_ORIGINS"] == "http://localhost:8080"
    assert os.environ["MCP_RATE_LIMIT_RPS"] == "1000"
    assert os.environ["MCP_RATE_LIMIT_BURST"] == "1000"
    assert os.environ["API_BEARER_TOKEN"]
