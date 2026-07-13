"""E2E test: Data isolation between tenants.

Tests that:
1. Tenant A's data is not visible to Tenant B
2. No-X-Tenant-ID routes to default tenant
3. Each tenant sees only its own records (isolation markers)
4. Ghost tenant returns 404

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
    cleanup_db,
    data_service_url,
    delete_tenant,
    register_tenant,
    seed_database,
)


# ── Module-level state (avoid yield-based fixtures, pytest bug) ────────────

_MARKERS: dict[str, str] = {}  # tenant_id → marker name
_TIDS: list[str] = []


def setup_module(module):
    """One-time setup: seed DBs, register tenants."""
    root = Path(__file__).resolve().parents[2]
    suffix = uuid.uuid4().hex[:8]
    db_a = root / f".data/e2e_iso_a_{suffix}.db"
    db_b = root / f".data/e2e_iso_b_{suffix}.db"
    db_a.parent.mkdir(parents=True, exist_ok=True)

    seed_shared = root / "specs" / "fixtures" / "seed.json"
    seed_database(db_a, seed_path=seed_shared, project_root_dir=root)
    seed_database(db_b, seed_path=seed_shared, project_root_dir=root)

    marker_a = f"ISO-A-{uuid.uuid4().hex[:6]}"
    marker_b = f"ISO-B-{uuid.uuid4().hex[:6]}"

    conn = sqlite3.connect(str(db_a))
    conn.execute(
        "UPDATE students SET name = ? WHERE id = (SELECT id FROM students LIMIT 1)",
        (marker_a,),
    )
    conn.commit()
    conn.close()

    conn = sqlite3.connect(str(db_b))
    conn.execute(
        "UPDATE students SET name = ? WHERE id = (SELECT id FROM students LIMIT 1)",
        (marker_b,),
    )
    conn.commit()
    conn.close()

    scenario_config = root / "data-service" / "testdata" / "scenarios" / "sqlite-testseed" / "config.json"
    base_config = json.loads(scenario_config.read_text())

    cfg_a = copy.deepcopy(base_config)
    cfg_a["data_source"]["dsn"] = str(db_a)
    cfg_b = copy.deepcopy(base_config)
    cfg_b["data_source"]["dsn"] = str(db_b)

    configs = [("e2e-iso-a", cfg_a, marker_a), ("e2e-iso-b", cfg_b, marker_b)]

    # Register
    for tid, cfg, _ in configs:
        delete_tenant(tid)  # cleanup stale
        result = register_tenant(tid, cfg)
        assert result["status"] in (200, 201), (
            f"Failed to register {tid}: status={result['status']} body={result['text'][:200]}"
        )
        _TIDS.append(tid)
        _MARKERS[tid] = marker_a if tid == "e2e-iso-a" else marker_b

    module._db_a = db_a
    module._db_b = db_b


def teardown_module(module):
    """One-time teardown: remove tenants, clean up DBs."""
    for tid in _TIDS:
        delete_tenant(tid)
    for attr in ["_db_a", "_db_b"]:
        db = getattr(module, attr, None)
        if db:
            cleanup_db(db)


# ── Tests ──────────────────────────────────────────────────────────────────


def test_tenant_a_has_data():
    """Tenant A returns data via its own X-Tenant-ID."""
    r = requests.get(
        f"{data_service_url()}/students",
        headers={"X-Tenant-ID": "e2e-iso-a"},
        timeout=10,
    )
    assert r.status_code == 200, f"e2e-iso-a: got {r.status_code}: {r.text[:200]}"
    data = r.json()
    assert isinstance(data, list), f"expected list, got {type(data)}"
    assert len(data) > 0, "empty list returned"


def test_tenant_b_has_data():
    """Tenant B returns data via its own X-Tenant-ID."""
    r = requests.get(
        f"{data_service_url()}/students",
        headers={"X-Tenant-ID": "e2e-iso-b"},
        timeout=10,
    )
    assert r.status_code == 200, f"e2e-iso-b: got {r.status_code}: {r.text[:200]}"
    data = r.json()
    assert isinstance(data, list), f"expected list, got {type(data)}"
    assert len(data) > 0, "empty list returned"


def test_isolation_a_does_not_see_b():
    """Tenant A's data does NOT contain Tenant B's isolation marker."""
    r = requests.get(
        f"{data_service_url()}/students",
        headers={"X-Tenant-ID": "e2e-iso-a"},
        timeout=10,
    )
    marker_b = _MARKERS.get("e2e-iso-b", "")
    assert marker_b not in r.text, (
        f"ISOLATION BREACH: Tenant B's marker '{marker_b}' found in Tenant A's data!"
    )


def test_isolation_b_does_not_see_a():
    """Tenant B's data does NOT contain Tenant A's isolation marker."""
    r = requests.get(
        f"{data_service_url()}/students",
        headers={"X-Tenant-ID": "e2e-iso-b"},
        timeout=10,
    )
    marker_a = _MARKERS.get("e2e-iso-a", "")
    assert marker_a not in r.text, (
        f"ISOLATION BREACH: Tenant A's marker '{marker_a}' found in Tenant B's data!"
    )


def test_default_tenant_no_leaked_data():
    """Default tenant (no X-Tenant-ID) has no isolation markers."""
    r = requests.get(f"{data_service_url()}/students", timeout=10)
    for tid, marker in _MARKERS.items():
        assert marker not in r.text, (
            f"ISOLATION BREACH: {tid}'s marker '{marker}' leaked into default tenant!"
        )


def test_ghost_tenant_returns_404():
    """Non-existent tenant returns 404."""
    r = requests.get(
        f"{data_service_url()}/students",
        headers={"X-Tenant-ID": f"ghost-{uuid.uuid4().hex[:8]}"},
        timeout=10,
    )
    assert r.status_code == 404, f"Ghost tenant should 404, got {r.status_code}"
