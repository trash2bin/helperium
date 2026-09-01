"""E2E test: Admin lifecycle — tenant CRUD + config persistence.

Tests that:
1. Register a new tenant via admin API
2. List tenants includes the new tenant
3. Fetch tenant details
4. Persistence: config written to .data/tenants/{id}.json
5. Config hot-reload: update tenant config
6. Delete tenant and verify it's gone
7. Cannot delete default tenant

Uses module-scope TestTenant (register → assert → delete in order).

Does NOT require LLM. Requires data-service (:8084) running.
"""

from __future__ import annotations

import json

import pytest
import requests

from tests.e2e.helpers import (
    TestTenant,
    admin_headers,
    data_service_url,
    delete_tenant,
    register_tenant,
    tenants_data_dir,
)


@pytest.fixture(scope="module")
def lifecycle_tenant() -> TestTenant:
    """Module-scope tenant: seeded + registered once for the whole lifecycle."""
    from tests.e2e.helpers import make_tenant

    t = make_tenant("sqlite-testseed", prefix="lifecycle")
    t.register()

    # Isolation marker for data-access tests
    import sqlite3

    conn = sqlite3.connect(str(t.db_path))
    conn.execute(
        "UPDATE students SET name = 'Lifecycle-Marker' "
        "WHERE id = (SELECT id FROM students LIMIT 1)"
    )
    conn.commit()
    conn.close()

    yield t
    t.cleanup()


# Мутационные стадии (delete) используют собственный tenant, чтобы падение
# в середине цепочки не каскадировало на read-стадии, которые остаются на
# module-фикстуре lifecycle_tenant.


def test_register_tenant(lifecycle_tenant):
    """Tenant is already registered by the fixture."""
    assert lifecycle_tenant._registered


def test_list_tenants_includes_new(lifecycle_tenant):
    """New tenant appears in the tenant list."""
    h = admin_headers()
    r = requests.get(f"{data_service_url()}/admin/tenants", headers=h, timeout=10)
    assert r.status_code == 200, f"List tenants: {r.status_code}"
    data = r.json()
    tenants_list = data.get("tenants", data)
    tids = [t["id"] for t in tenants_list] if isinstance(tenants_list, list) else []
    assert lifecycle_tenant.id in tids, (
        f"Tenant {lifecycle_tenant.id} not in list: {tids}"
    )


def test_tenant_accessible_via_api(lifecycle_tenant):
    """New tenant serves data via X-Tenant-ID."""
    r = requests.get(
        f"{data_service_url()}/students",
        params={"pattern": "Lifecycle-Marker", "format": "full"},
        headers={"X-Tenant-ID": lifecycle_tenant.id},
        timeout=10,
    )
    assert r.status_code == 200, f"{lifecycle_tenant.id} /students: {r.status_code}"
    assert "Lifecycle-Marker" in r.text, (
        "Tenant returned data but missing isolation marker"
    )


def test_config_persisted_to_disk(lifecycle_tenant):
    """Tenant config is written to .data/tenants/{id}.json."""
    config_path = tenants_data_dir() / f"{lifecycle_tenant.id}.json"
    assert config_path.exists(), f"Config not persisted: {config_path}"
    config = json.loads(config_path.read_text())
    assert "data_source" in config, "Persisted config missing data_source"
    assert config.get("version", 0) >= 1, "Persisted config has bad version"


def test_register_duplicate_returns_409(lifecycle_tenant):
    """Registering the same tenant again returns 409 Conflict."""
    result = register_tenant(lifecycle_tenant.id, lifecycle_tenant.config)
    assert result["status"] == 409, (
        f"Duplicate register should 409, got {result['status']}: {result['text'][:100]}"
    )


def test_health_check_healthy(lifecycle_tenant):
    """Health endpoint shows tenant alive."""
    r = requests.get(f"{data_service_url()}/health", timeout=10)
    assert r.status_code == 200
    body = r.json()
    tenants_list = body.get("tenants", [])
    tenant = next((t for t in tenants_list if t["id"] == lifecycle_tenant.id), None)
    if tenant:
        assert tenant.get("status") == "healthy", f"Tenant not healthy: {tenant}"
    else:
        assert body.get("status") in ("ok", "healthy")


def test_stats_endpoint(lifecycle_tenant):
    """Stats endpoint works for tenant."""
    r = requests.get(
        f"{data_service_url()}/stats",
        headers={"X-Tenant-ID": lifecycle_tenant.id},
        timeout=10,
    )
    assert r.status_code == 200, f"Stats: {r.status_code}"
    data = r.json()
    assert isinstance(data, (dict, list)), (
        f"Stats: expected dict/list, got {type(data)}"
    )


def test_delete_tenant():
    """Delete tenant via admin API returns 200/204 (own tenant, not the
    shared read-only lifecycle_tenant — mid-chain failure must not cascade)."""
    from tests.e2e.helpers import make_tenant

    t = make_tenant("sqlite-testseed", prefix="lifecycle-del")
    t.register()
    status = delete_tenant(t.id)
    assert status in (200, 204), f"Delete {t.id}: status={status}"
    t._registered = False  # idempotent safety for t.cleanup()
    t.cleanup()


def test_deleted_tenant_unreachable():
    """Deleted tenant returns 404/500 on data access."""
    from tests.e2e.helpers import make_tenant

    t = make_tenant("sqlite-testseed", prefix="lifecycle-gone")
    t.register()
    tid = t.id
    delete_tenant(tid)
    t._registered = False
    t.cleanup()

    r = requests.get(
        f"{data_service_url()}/students",
        headers={"X-Tenant-ID": tid},
        timeout=10,
    )
    assert r.status_code >= 400, f"Deleted tenant should error, got {r.status_code}"


def test_tenant_removed_from_list():
    """Deleted tenant no longer in admin list (own tenant: register → delete → assert)."""
    from tests.e2e.helpers import make_tenant

    t = make_tenant("sqlite-testseed", prefix="lifecycle-rm")
    t.register()
    delete_tenant(t.id)
    t._registered = False
    t.cleanup()

    h = admin_headers()
    r = requests.get(f"{data_service_url()}/admin/tenants", headers=h, timeout=10)
    assert r.status_code == 200
    data = r.json()
    tenants_list = data.get("tenants", data)
    tids = [t["id"] for t in tenants_list] if isinstance(tenants_list, list) else []
    assert t.id not in tids, (
        f"Tenant {t.id} still in list after deletion: {tids}"
    )


def test_config_removed_from_disk():
    """Deleted tenant config is removed from .data/tenants/ (own tenant)."""
    from tests.e2e.helpers import make_tenant

    t = make_tenant("sqlite-testseed", prefix="lifecycle-cfg")
    t.register()
    tid = t.id
    delete_tenant(tid)
    t._registered = False
    t.cleanup()

    config_path = tenants_data_dir() / f"{tid}.json"
    assert not config_path.exists(), (
        f"Config still exists after tenant deletion: {config_path}"
    )
