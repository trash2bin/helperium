"""E2E test: MCP composite mode — single SSE session, multiple tenants.

Tests that:
1. Composite SSE session with multiple tenants opens correctly
2. Tenant-prefixed tools are available (tenant-a__list_student)
3. Each prefixed tool routes to the correct tenant
4. Non-prefixed tools still work for single-tenant sessions
5. Mixed tenant tools are all accessible in one session

Does NOT require LLM. Requires data-service (:8084) + mcp-gateway (:8083) running.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
import requests

from tests.e2e.helpers import (
    admin_headers,
    cleanup_db,
    data_service_url,
    delete_tenant,
    mcp_call,
    project_root,
    register_tenant,
    seed_database,
)


# ── Module-level state ─────────────────────────────────────────────────────

_TENANTS = ["e2e-comp-uni", "e2e-comp-shop"]
_DB_A: Path | None = None


def setup_module(module):
    """Setup: seed DBs, register two tenants with different schemas."""
    global _DB_A
    root = project_root()
    suffix = uuid.uuid4().hex[:8]
    _DB_A = root / f".data/e2e_comp_uni_{suffix}.db"
    _DB_A.parent.mkdir(parents=True, exist_ok=True)

    # Seed university DB
    seed_path = root / "specs" / "fixtures" / "seed.json"
    shop_db = root / "data-service" / "testdata" / "scenarios" / "shop" / "data.db"

    seed_database(_DB_A, seed_path=seed_path, project_root_dir=root)

    # Clean stale tenants
    for tid in _TENANTS:
        delete_tenant(tid)

    uni_config = {
        "data_source": {"driver": "sqlite", "dsn": str(_DB_A), "read_only": True},
        "entities": [
            {
                "name": "student",
                "table": "students",
                "id_column": "id",
                "fields": [
                    {"name": "name", "column": "name", "type": "string"},
                    {"name": "id", "column": "id", "type": "string"},
                ],
            }
        ],
        "endpoints": [
            {"method": "GET", "path": "/students", "op": "list", "entity": "student"},
        ],
    }

    shop_config = {
        "data_source": {"driver": "sqlite", "dsn": str(shop_db), "read_only": True},
        "entities": [
            {
                "name": "product",
                "table": "products",
                "id_column": "id",
                "fields": [
                    {"name": "name", "column": "name", "type": "string"},
                    {"name": "id", "column": "id", "type": "string"},
                ],
            }
        ],
        "endpoints": [
            {"method": "GET", "path": "/products", "op": "list", "entity": "product"},
        ],
    }

    for tid, cfg in [("e2e-comp-uni", uni_config), ("e2e-comp-shop", shop_config)]:
        r = register_tenant(tid, cfg)
        assert r["status"] in (200, 201), f"Register {tid}: {r['status']}"


def teardown_module(module):
    """Cleanup."""
    for tid in _TENANTS:
        delete_tenant(tid)
    if _DB_A:
        cleanup_db(_DB_A)


# ── Tests ──────────────────────────────────────────────────────────────────


def test_composite_uni_legacy_tool():
    """Single-tenant session: list_student works (legacy mode, no prefix)."""
    result = mcp_call("list_student", tenant_ids="e2e-comp-uni")
    assert result, f"Composite uni list_student failed: {result.error}"
    assert result.result is not None, "Result should have content"


def test_composite_shop_legacy_tool():
    """Single-tenant session: list_product works for shop."""
    result = mcp_call("list_product", tenant_ids="e2e-comp-shop")
    assert result, f"Composite shop list_product failed: {result.error}"


def test_composite_both_tenants_in_one_session():
    """One SSE session with both tenant IDs — prefixed tools work.

    When X-Tenant-ID has comma-separated values, mcp-gateway
    creates a composite server with tenant-prefixed tools.
    """
    # Call e2e-comp-uni__list_student (prefixed)
    result = mcp_call(
        "e2e-comp-uni__list_student",
        tenant_ids="e2e-comp-uni,e2e-comp-shop",
    )
    assert result, (
        f"Composite prefixed tool 'e2e-comp-uni__list_student' failed: {result.error}"
    )
    assert result.result is not None, "Result should have content"


def test_composite_prefixed_shop_tool():
    """Composite session: e2e-comp-shop__list_product works."""
    result = mcp_call(
        "e2e-comp-shop__list_product",
        tenant_ids="e2e-comp-uni,e2e-comp-shop",
    )
    assert result, (
        f"Composite prefixed tool 'e2e-comp-shop__list_product' failed: {result.error}"
    )


def test_composite_cross_tenant_blocked():
    """Composite: shop tenant cannot access uni's tool via composite.

    Even in composite mode, e2e-comp-shop__list_student should NOT exist
    because shop tenant doesn't have students.
    """
    result = mcp_call(
        "e2e-comp-shop__list_student",
        tenant_ids="e2e-comp-uni,e2e-comp-shop",
    )
    assert not result, (
        "ISOLATION BREACH: shop tenant's prefixed tool list_student should NOT exist"
    )
