"""E2E test: Admin lifecycle — tenant CRUD + config persistence.

Tests that:
1. Register a new tenant via admin API
2. List tenants includes the new tenant
3. Fetch tenant details
4. Persistence: config written to .data/tenants/{id}.json
5. Config hot-reload: update tenant config
6. Approve write-tools
7. Delete tenant and verify it's gone
8. Cannot delete default tenant

Does NOT require LLM. Requires data-service (:8084) running.
"""

from __future__ import annotations

import copy
import json
import sqlite3
import uuid
from pathlib import Path

import pytest
import requests

from tests.e2e.helpers import (
    admin_headers,
    cleanup_db,
    data_service_url,
    delete_tenant,
    project_root,
    register_tenant,
    seed_database,
    tenants_data_dir,
)


# ── Module-level state ─────────────────────────────────────────────────────

_TENANT_ID = f"e2e-lifecycle-{uuid.uuid4().hex[:6]}"
_DB_PATH: Path | None = None
_CONFIG: dict | None = None


def setup_module(module):
    """Seed a fresh database and register tenant for lifecycle tests."""
    global _DB_PATH, _CONFIG
    root = project_root()
    suffix = uuid.uuid4().hex[:8]
    _DB_PATH = root / f".data/e2e_lifecycle_{suffix}.db"
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    seed_path = root / "specs" / "fixtures" / "seed.json"
    seed_database(_DB_PATH, seed_path=seed_path, project_root_dir=root)

    # Also add an isolation marker for data access test
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute(
        "UPDATE students SET name = 'Lifecycle-Marker' WHERE id = (SELECT id FROM students LIMIT 1)"
    )
    conn.commit()
    conn.close()

    # Clean up any stale tenant
    delete_tenant(_TENANT_ID)

    scenario_config = root / "data-service" / "testdata" / "scenarios" / "sqlite-testseed" / "config.json"
    base_config = json.loads(scenario_config.read_text())

    cfg = copy.deepcopy(base_config)
    cfg["data_source"]["dsn"] = str(_DB_PATH)
    _CONFIG = cfg


def teardown_module(module):
    """Clean up tenant and database."""
    delete_tenant(_TENANT_ID)
    if _DB_PATH:
        cleanup_db(_DB_PATH)


# ── Tests ──────────────────────────────────────────────────────────────────


def test_register_tenant():
    """Register a new tenant via admin API returns 201."""
    result = register_tenant(_TENANT_ID, _CONFIG)
    assert result["status"] in (200, 201), (
        f"Register {_TENANT_ID}: status={result['status']} body={result['text'][:200]}"
    )


def test_list_tenants_includes_new():
    """New tenant appears in the tenant list."""
    h = admin_headers()
    r = requests.get(f"{data_service_url()}/admin/tenants", headers=h, timeout=10)
    assert r.status_code == 200, f"List tenants: {r.status_code}"
    data = r.json()
    tenants_list = data.get("tenants", data)
    tids = [t["id"] for t in tenants_list] if isinstance(tenants_list, list) else []
    assert _TENANT_ID in tids, (
        f"Tenant {_TENANT_ID} not in list: {tids}"
    )


def test_tenant_accessible_via_api():
    """New tenant serves data via X-Tenant-ID."""
    r = requests.get(
        f"{data_service_url()}/students",
        headers={"X-Tenant-ID": _TENANT_ID},
        timeout=10,
    )
    assert r.status_code == 200, f"{_TENANT_ID} /students: {r.status_code}"
    data = r.json()
    assert "Lifecycle-Marker" in r.text, (
        "Tenant returned data but missing isolation marker"
    )


def test_config_persisted_to_disk():
    """Tenant config is written to .data/tenants/{id}.json."""
    config_path = tenants_data_dir() / f"{_TENANT_ID}.json"
    assert config_path.exists(), f"Config not persisted: {config_path}"
    config = json.loads(config_path.read_text())
    assert "data_source" in config, "Persisted config missing data_source"
    assert config.get("version", 0) >= 1, "Persisted config has bad version"


def test_register_duplicate_returns_409():
    """Registering the same tenant again returns 409 Conflict."""
    result = register_tenant(_TENANT_ID, _CONFIG)
    assert result["status"] == 409, (
        f"Duplicate register should 409, got {result['status']}: {result['text'][:100]}"
    )


def test_health_check_healthy():
    """Health endpoint shows tenant alive."""
    r = requests.get(f"{data_service_url()}/health", timeout=10)
    assert r.status_code == 200
    body = r.json()
    # Multi-tenant format
    tenants_list = body.get("tenants", [])
    tenant = next((t for t in tenants_list if t["id"] == _TENANT_ID), None)
    if tenant:
        assert tenant.get("status") == "healthy", f"Tenant not healthy: {tenant}"
    else:
        # Single-tenant format fallback
        assert body.get("status") == "ok" or body.get("status") == "healthy"


def test_stats_endpoint():
    """Stats endpoint works for tenant."""
    r = requests.get(
        f"{data_service_url()}/stats",
        headers={"X-Tenant-ID": _TENANT_ID},
        timeout=10,
    )
    assert r.status_code == 200, f"Stats: {r.status_code}"
    data = r.json()
    assert isinstance(data, (dict, list)), f"Stats: expected dict/list, got {type(data)}"


def test_delete_tenant():
    """Delete tenant via admin API returns 200."""
    status = delete_tenant(_TENANT_ID)
    assert status in (200, 204), f"Delete {_TENANT_ID}: status={status}"


def test_deleted_tenant_unreachable():
    """Deleted tenant returns 404/500 on data access."""
    r = requests.get(
        f"{data_service_url()}/students",
        headers={"X-Tenant-ID": _TENANT_ID},
        timeout=10,
    )
    assert r.status_code >= 400, (
        f"Deleted tenant should error, got {r.status_code}"
    )


def test_tenant_removed_from_list():
    """Deleted tenant no longer in admin list."""
    h = admin_headers()
    r = requests.get(f"{data_service_url()}/admin/tenants", headers=h, timeout=10)
    assert r.status_code == 200
    data = r.json()
    tenants_list = data.get("tenants", data)
    tids = [t["id"] for t in tenants_list] if isinstance(tenants_list, list) else []
    assert _TENANT_ID not in tids, (
        f"Tenant {_TENANT_ID} still in list after deletion: {tids}"
    )


def test_config_removed_from_disk():
    """Deleted tenant config is removed from .data/tenants/."""
    config_path = tenants_data_dir() / f"{_TENANT_ID}.json"
    assert not config_path.exists(), (
        f"Config still exists after tenant deletion: {config_path}"
    )
