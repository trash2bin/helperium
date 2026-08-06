"""E2E test: Data isolation between tenants (расширяемая архитектура).

Tests that:
1. Tenant A's data is not visible to Tenant B
2. No-X-Tenant-ID routes to default tenant
3. Each tenant sees only its own records (isolation markers)
4. Ghost tenant returns 404

Uses the ``tenants`` factory fixture (TestTenant) — no module-level setup,
no manual cleanup, fully isolated per-test.
"""

from __future__ import annotations

import uuid

import requests

from tests.e2e.helpers import data_service_url


def _students_grep(tenant_id: str | None = None) -> requests.Response:
    """GET /students?pattern=Иван — grep по students (REST strategy-эндпоинт)."""
    headers = {}
    if tenant_id:
        headers["X-Tenant-ID"] = tenant_id
    return requests.get(
        f"{data_service_url()}/students",
        params={"pattern": "\u0418\u0432\u0430\u043d"},  # "Иван" — guaranteed in seed data
        headers=headers,
        timeout=10,
    )


def _stamp_marker(t, marker: str) -> None:
    """Set a unique marker on the first student row (in-place, via sqlite)."""
    import sqlite3

    conn = sqlite3.connect(str(t.db_path))
    conn.execute(
        "UPDATE students SET name = ? WHERE id = (SELECT id FROM students LIMIT 1)",
        (marker,),
    )
    conn.commit()
    conn.close()


def _text(result) -> str:
    content = (result.get("content") or []) if isinstance(result, dict) else []
    return "".join(c.get("text", "") for c in content if "text" in c)


# ═══════════════════════════════════════════════════════════════════════════
# Изоляция на уровне данных (REST)
# ═══════════════════════════════════════════════════════════════════════════


def test_tenant_a_has_data(tenants):
    """Tenant A returns data via its own X-Tenant-ID."""
    a = tenants("sqlite-testseed", prefix="iso_a")
    _stamp_marker(a, "ISO-A-TEST")

    r = _students_grep(a.id)
    assert r.status_code == 200, f"{a.id}: got {r.status_code}: {r.text[:200]}"
    data = r.json()
    preview = data.get("preview", []) if isinstance(data, dict) else data
    assert isinstance(preview, list), f"expected preview list, got {type(preview)}"
    assert len(preview) > 0, "empty result returned"


def test_tenant_b_has_data(tenants):
    """Tenant B returns data via its own X-Tenant-ID."""
    b = tenants("sqlite-testseed", prefix="iso_b")
    _stamp_marker(b, "ISO-B-TEST")

    r = _students_grep(b.id)
    assert r.status_code == 200, f"{b.id}: got {r.status_code}: {r.text[:200]}"
    data = r.json()
    preview = data.get("preview", []) if isinstance(data, dict) else data
    assert isinstance(preview, list), f"expected preview list, got {type(preview)}"
    assert len(preview) > 0, "empty result returned"


def test_isolation_a_does_not_see_b(tenants):
    """Tenant A's data does NOT contain Tenant B's isolation marker."""
    a = tenants("sqlite-testseed", prefix="iso_a")
    b = tenants("sqlite-testseed", prefix="iso_b")
    _stamp_marker(a, "MARK-A-ONLY")
    _stamp_marker(b, "MARK-B-ONLY")

    r = _students_grep(a.id)
    assert "MARK-B-ONLY" not in r.text, (
        "ISOLATION BREACH: Tenant B's marker found in Tenant A's data!"
    )


def test_isolation_b_does_not_see_a(tenants):
    """Tenant B's data does NOT contain Tenant A's isolation marker."""
    a = tenants("sqlite-testseed", prefix="iso_a")
    b = tenants("sqlite-testseed", prefix="iso_b")
    _stamp_marker(a, "MARK-A-ONLY")
    _stamp_marker(b, "MARK-B-ONLY")

    r = _students_grep(b.id)
    assert "MARK-A-ONLY" not in r.text, (
        "ISOLATION BREACH: Tenant A's marker found in Tenant B's data!"
    )


def test_default_tenant_no_leaked_data(tenants):
    """Default tenant (no X-Tenant-ID) has no isolation markers."""
    a = tenants("sqlite-testseed", prefix="iso_a")
    b = tenants("sqlite-testseed", prefix="iso_b")
    _stamp_marker(a, "MARK-A-ONLY")
    _stamp_marker(b, "MARK-B-ONLY")

    r = _students_grep()
    assert "MARK-A-ONLY" not in r.text and "MARK-B-ONLY" not in r.text, (
        "ISOLATION BREACH: markers leaked into default tenant!"
    )


def test_ghost_tenant_returns_404(tenants):
    """Non-existent tenant returns 404."""
    tenants("sqlite-testseed", prefix="iso_a")  # ensure data-service is up
    r = _students_grep(f"ghost-{uuid.uuid4().hex[:8]}")
    assert r.status_code == 404, f"Ghost tenant should 404, got {r.status_code}"


# ═══════════════════════════════════════════════════════════════════════════
# Изоляция на уровне MCP (db_get / db_search)
# ═══════════════════════════════════════════════════════════════════════════


def _db_get_id(t, pattern: str) -> str | None:
    """Find first id of tenant's record via db_search (for db_get)."""
    r = t.mcp_call("db_search", {"entity": "student", "pattern": pattern}, timeout=30)
    if not r.result or r.result.get("isError", False):
        return None
    import re

    text = _text(r.result)
    m = re.search(r'"id"\s*:\s*"([^"]+)"', text) or re.search(r'"id"\s*:\s*(\d+)', text)
    return m.group(1) if m else None


def test_db_get_other_tenant_id_denied(tenants):
    """db_get чужого id (из другого тенанта) → isError/нет данных."""
    a = tenants("sqlite-testseed", prefix="iso_a")
    b = tenants("sqlite-testseed", prefix="iso_b")
    _stamp_marker(a, "M-OWN-A")
    _stamp_marker(b, "M-OWN-B")

    id_b = _db_get_id(b, "M-OWN-B")
    assert id_b is not None, "tenant B should have a student id"

    r = a.mcp_call("db_get", {"entity": "student", "id": id_b}, timeout=30)
    text = _text(r.result) if r.result else ""
    assert "M-OWN-B" not in text, (
        "ISOLATION BREACH: tenant A db_get leaked tenant B record"
    )


def test_db_get_own_id_ok(tenants):
    """db_get своего id → данные своего тенанта."""
    a = tenants("sqlite-testseed", prefix="iso_a")
    _stamp_marker(a, "M-OWN-A")

    id_a = _db_get_id(a, "M-OWN-A")
    assert id_a is not None, "tenant A should have a student id"

    r = a.mcp_call("db_get", {"entity": "student", "id": id_a}, timeout=30)
    text = _text(r.result) if r.result else ""
    assert "M-OWN-A" in text, f"db_get own id should return own marker: {text[:200]}"


def test_db_filter_does_not_leak_other_tenant(tenants):
    """db_search: тенант A не видит записи тенанта B (изоляция по tenant_id)."""
    a = tenants("sqlite-testseed", prefix="iso_a")
    b = tenants("sqlite-testseed", prefix="iso_b")
    _stamp_marker(b, "M-OWN-B")

    r = a.mcp_call("db_search", {"entity": "student", "pattern": "M-OWN-B"}, timeout=30)
    text = _text(r.result) if r.result else ""
    assert "M-OWN-B" not in text, (
        "ISOLATION BREACH: tenant A db_search leaked tenant B record"
    )
