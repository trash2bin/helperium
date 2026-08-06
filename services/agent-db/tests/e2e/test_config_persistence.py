"""E2E test: Config persistence — tenant survives service restart.

Tests that:
1. Register tenant → config written to .data/tenants/{id}.json
2. Tenant is registered and serves data
3. Tenant config file contains valid data

Uses module-scope TestTenant (registered once).

Requires data-service (:8084) running.
"""

from __future__ import annotations

import json

import pytest
import requests

from tests.e2e.helpers import (
    TestTenant,
    data_service_url,
    tenants_data_dir,
)


@pytest.fixture(scope="module")
def persist_tenant() -> TestTenant:
    """Module-scope tenant: seeded + registered once."""
    from tests.e2e.helpers import make_tenant

    t = make_tenant("sqlite-testseed", prefix="persist")
    t.register()

    # Persist-Marker for data-access tests
    import sqlite3

    conn = sqlite3.connect(str(t.db_path))
    conn.execute(
        "UPDATE students SET name = 'Persist-Marker' "
        "WHERE id = (SELECT id FROM students LIMIT 1)"
    )
    conn.commit()
    conn.close()

    yield t
    t.cleanup()


# ── Tests ──────────────────────────────────────────────────────────────────


def test_config_file_exists(persist_tenant):
    """Tenant config is persisted to disk after registration."""
    config_path = tenants_data_dir() / f"{persist_tenant.id}.json"
    assert config_path.exists(), f"Config not found: {config_path}"
    assert config_path.stat().st_size > 50, "Config file too small"


def test_config_file_has_valid_content(persist_tenant):
    """Persisted config is valid JSON with required fields."""
    config_path = tenants_data_dir() / f"{persist_tenant.id}.json"
    config = json.loads(config_path.read_text())
    assert config.get("version", 0) >= 1, "Missing version"
    assert "data_source" in config, "Missing data_source"
    assert "driver" in config.get("data_source", {}), "Missing data_source.driver"
    assert "entities" in config, "Missing entities"
    assert len(config.get("entities", [])) > 0, "Empty entities"


def test_tenant_serves_data(persist_tenant):
    """Tenant serves data via its X-Tenant-ID."""
    r = requests.get(
        f"{data_service_url()}/students",
        params={"pattern": "Persist-Marker", "format": "full"},
        headers={"X-Tenant-ID": persist_tenant.id},
        timeout=10,
    )
    assert r.status_code == 200, f"Data: {r.status_code}"
    assert "Persist-Marker" in r.text, "Isolation marker not found in tenant data"


def test_config_write_is_atomic(persist_tenant):
    """Config write is atomic: no .tmp leftovers, file stays valid JSON.

    data-service persists configs via temp+rename (os.CreateTemp →
    os.Rename), not a .bak backup. This verifies that mechanism:
    - no ``*.json.tmp*`` files left behind
    - a rewrite (register same tenant → 409 path) keeps the file valid
    """
    ddir = tenants_data_dir()
    tenant_id = persist_tenant.id
    config_path = ddir / f"{tenant_id}.json"

    # 1. No temp leftovers from the original write
    leftovers = list(ddir.glob(f"{tenant_id}.json.tmp*"))
    assert not leftovers, f"Temp files left after persist: {leftovers}"

    # 2. Force a re-write: duplicate registration triggers persist path
    from tests.e2e.helpers import register_tenant

    result = register_tenant(tenant_id, persist_tenant.config)
    assert result["status"] == 409, (
        f"Expected 409 duplicate, got {result['status']}: {result['text'][:100]}"
    )

    # 3. Still no temp leftovers, and config is valid JSON
    leftovers = list(ddir.glob(f"{tenant_id}.json.tmp*"))
    assert not leftovers, f"Temp files left after duplicate write: {leftovers}"

    config = json.loads(config_path.read_text())
    assert "data_source" in config, "Config corrupted after duplicate write"
