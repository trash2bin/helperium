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
    mcp_call,
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

    scenario_config = (
        root
        / "data-service"
        / "testdata"
        / "scenarios"
        / "sqlite-testseed"
        / "config.json"
    )
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


def _students_grep(tenant_id: str | None = None) -> requests.Response:
    """GET /students?pattern=a — grep по students (REST strategy-эндпоинт)."""
    headers = {}
    if tenant_id:
        headers["X-Tenant-ID"] = tenant_id
    return requests.get(
        f"{data_service_url()}/students",
        params={"pattern": "a"},
        headers=headers,
        timeout=10,
    )


def test_tenant_a_has_data():
    """Tenant A returns data via its own X-Tenant-ID."""
    r = _students_grep("e2e-iso-a")
    assert r.status_code == 200, f"e2e-iso-a: got {r.status_code}: {r.text[:200]}"
    data = r.json()
    # grep возвращает объект {total, returned, preview} (не list — list убран в v4).
    preview = data.get("preview", []) if isinstance(data, dict) else data
    assert isinstance(preview, list), f"expected preview list, got {type(preview)}"
    assert len(preview) > 0, "empty result returned"


def test_tenant_b_has_data():
    """Tenant B returns data via its own X-Tenant-ID."""
    r = _students_grep("e2e-iso-b")
    assert r.status_code == 200, f"e2e-iso-b: got {r.status_code}: {r.text[:200]}"
    data = r.json()
    preview = data.get("preview", []) if isinstance(data, dict) else data
    assert isinstance(preview, list), f"expected preview list, got {type(preview)}"
    assert len(preview) > 0, "empty result returned"


def test_isolation_a_does_not_see_b():
    """Tenant A's data does NOT contain Tenant B's isolation marker."""
    r = _students_grep("e2e-iso-a")
    marker_b = _MARKERS.get("e2e-iso-b", "")
    assert marker_b not in r.text, (
        f"ISOLATION BREACH: Tenant B's marker '{marker_b}' found in Tenant A's data!"
    )


def test_isolation_b_does_not_see_a():
    """Tenant B's data does NOT contain Tenant A's isolation marker."""
    r = _students_grep("e2e-iso-b")
    marker_a = _MARKERS.get("e2e-iso-a", "")
    assert marker_a not in r.text, (
        f"ISOLATION BREACH: Tenant A's marker '{marker_a}' found in Tenant B's data!"
    )


def test_default_tenant_no_leaked_data():
    """Default tenant (no X-Tenant-ID) has no isolation markers."""
    r = _students_grep()
    for tid, marker in _MARKERS.items():
        assert marker not in r.text, (
            f"ISOLATION BREACH: {tid}'s marker '{marker}' leaked into default tenant!"
        )


def test_ghost_tenant_returns_404():
    """Non-existent tenant returns 404."""
    r = _students_grep(f"ghost-{uuid.uuid4().hex[:8]}")
    assert r.status_code == 404, f"Ghost tenant should 404, got {r.status_code}"


# ═══════════════════════════════════════════════════════════════════════════
# Фаза 2: db_get / db_related изоляция через MCP (ревизия)
# ═══════════════════════════════════════════════════════════════════════════


def _db_get_id(tenant_id: str) -> int | None:
    """Через db_search найти первый id записи тенанта (для db_get)."""
    marker = _MARKERS.get(tenant_id, "")
    r = mcp_call(
        "db_search",
        {"entity": "student", "pattern": marker or "a"},
        tenant_ids=tenant_id,
        timeout=30,
    )
    if not r.result.get("isError", False):
        text = "".join(c.get("text", "") for c in r.result.get("content", []) if "text" in c)
        import re

        # id может быть строкой (UUID) — берём первое значение поля id.
        m = re.search(r'"id"\s*:\s*"([^"]+)"', text)
        if not m:
            m = re.search(r'"id"\s*:\s*(\d+)', text)
        if m:
            return m.group(1)
    return None


def test_db_get_other_tenant_id_denied():
    """db_get чужого id (из другого тенанта) → isError/нет данных."""
    # Найти id записи тенанта B через его db_search.
    id_b = _db_get_id("e2e-iso-b")
    assert id_b is not None, "tenant B should have a student id"

    # Тенант A пытается db_get id тенанта B.
    r = mcp_call(
        "db_get",
        {"entity": "student", "id": id_b},
        tenant_ids="e2e-iso-a",
        timeout=30,
    )
    text = "".join(c.get("text", "") for c in r.result.get("content", []) if "text" in c)
    marker_b = _MARKERS.get("e2e-iso-b", "")
    assert marker_b not in text, (
        f"ISOLATION BREACH: tenant A db_get leaked tenant B record (marker '{marker_b}')"
    )
    # Либо isError, либо not_found — но не данные тенанта B.
    assert "Student" not in text or r.result.get("isError", False), (
        f"tenant A db_get should not return tenant B data: {text[:200]}"
    )


def test_db_get_own_id_ok():
    """db_get своего id → данные своего тенанта."""
    id_a = _db_get_id("e2e-iso-a")
    assert id_a is not None, "tenant A should have a student id"
    r = mcp_call(
        "db_get",
        {"entity": "student", "id": id_a},
        tenant_ids="e2e-iso-a",
        timeout=30,
    )
    text = "".join(c.get("text", "") for c in r.result.get("content", []) if "text" in c)
    marker_a = _MARKERS.get("e2e-iso-a", "")
    assert marker_a in text, (
        f"db_get own id should return own marker '{marker_a}': {text[:200]}"
    )


def test_db_filter_does_not_leak_other_tenant():
    """db_search (есть у iso-tenant): тенант A не видит записи тенанта B
    (изоляция по tenant_id; filter_* в iso-конфиге нет — он минимальный)."""
    marker_b = _MARKERS.get("e2e-iso-b", "")
    r = mcp_call(
        "db_search",
        {"entity": "student", "pattern": marker_b or "ISO-B"},
        tenant_ids="e2e-iso-a",
        timeout=30,
    )
    content = r.result.get("content", []) if r.result else []
    text = "".join(c.get("text", "") for c in content if "text" in c)
    assert marker_b not in text, (
        f"ISOLATION BREACH: tenant A db_search leaked tenant B record (marker '{marker_b}')"
    )
