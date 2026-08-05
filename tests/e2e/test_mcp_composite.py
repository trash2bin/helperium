"""E2E test: MCP composite mode — single SSE session, multiple tenants.

Tests that:
1. Composite SSE session with multiple tenants opens correctly
2. Tenant-prefixed tools are available (tenant-a__db_map)
3. Each prefixed tool routes to the correct tenant
4. Non-prefixed tools still work for single-tenant sessions
5. Mixed tenant tools are all accessible in one session
6. Cross-tenant access via a prefixed tool is blocked (isolation)

Tool surface (v5, see .data/e2e_revision_ground_truth.md):
- 5 consolidated db_* tools (db_map, db_describe, db_search, db_get, db_related)
- In composite mode (X-Tenant-ID: a,b) tools are prefixed "{tenantID}__"
  (mcp-gateway/internal/tools/tools.go:148)

Does NOT require LLM. Requires data-service (:8084) + mcp-gateway (:8083) running.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from tests.e2e.helpers import (
    cleanup_db,
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

    # NOTE: mcp_tools key is IGNORED by data-service — manifest is regenerated
    # from endpoints (v5: 5 db_* consolidated + per-entity filter_{entity}).
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
            {"method": "GET", "path": "/students", "op": "strategy", "strategy": "schema", "entity": "student"},
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
            {"method": "GET", "path": "/products", "op": "strategy", "strategy": "schema", "entity": "product"},
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


# ── Helpers ────────────────────────────────────────────────────────────────


def _result_text(result) -> str:
    """Extract concatenated text from an MCPCallResult's content blocks."""
    content = result.result.get("content", [])
    return "".join(c.get("text", "") for c in content if "text" in c)


def _is_error(result) -> bool:
    """True if the tool returned a business-level isError (e.g. unknown entity)."""
    return bool(result.result.get("isError", False))


# ── Tests ──────────────────────────────────────────────────────────────────


def test_composite_uni_db_map():
    """Single-tenant session: db_map works (no prefix)."""
    result = mcp_call("db_map", {}, tenant_ids="e2e-comp-uni")
    assert result, f"Composite uni db_map failed: {result.error}"
    assert result.result is not None, "Result should have content"
    assert not _is_error(result), f"db_map returned isError: {_result_text(result)[:200]}"
    text = _result_text(result)
    assert "student" in text.lower(), f"db_map should mention student: {text[:200]}"


def test_composite_shop_db_search():
    """Single-tenant session: db_search works for shop."""
    result = mcp_call(
        "db_search",
        {"entity": "product", "pattern": "MacBook"},
        tenant_ids="e2e-comp-shop",
    )
    assert result, f"Composite shop db_search failed: {result.error}"
    assert not _is_error(result), f"db_search returned isError: {_result_text(result)[:200]}"
    text = _result_text(result)
    assert "MacBook" in text, f"db_search should find MacBook: {text[:300]}"


def test_composite_both_tenants_in_one_session():
    """One SSE session with both tenant IDs — prefixed tools work.

    When X-Tenant-ID has comma-separated values, mcp-gateway
    creates a composite server with tenant-prefixed tools.
    """
    # Call e2e-comp-uni__db_map (prefixed)
    result = mcp_call(
        "e2e-comp-uni__db_map",
        {},
        tenant_ids="e2e-comp-uni,e2e-comp-shop",
    )
    assert result, (
        f"Composite prefixed tool 'e2e-comp-uni__db_map' failed: {result.error}"
    )
    assert result.result is not None, "Result should have content"
    assert not _is_error(result), f"prefixed db_map returned isError: {_result_text(result)[:200]}"
    text = _result_text(result)
    assert "student" in text.lower(), f"prefixed db_map should mention student: {text[:200]}"


def test_composite_prefixed_shop_tool():
    """Composite session: e2e-comp-shop__db_search works."""
    result = mcp_call(
        "e2e-comp-shop__db_search",
        {"entity": "product", "pattern": "iPhone"},
        tenant_ids="e2e-comp-uni,e2e-comp-shop",
    )
    assert result, (
        f"Composite prefixed tool 'e2e-comp-shop__db_search' failed: {result.error}"
    )
    assert not _is_error(result), f"prefixed db_search returned isError: {_result_text(result)[:200]}"
    text = _result_text(result)
    assert "iPhone" in text, f"prefixed db_search should find iPhone: {text[:300]}"


def test_composite_cross_tenant_blocked():
    """Composite: shop tenant cannot access uni's student entity.

    Even in composite mode, e2e-comp-shop__db_search(entity=student) should
    return a business-level isError (unknown_entity) because the shop tenant
    has no student entity.
    """
    result = mcp_call(
        "e2e-comp-shop__db_search",
        {"entity": "student", "pattern": "x"},
        tenant_ids="e2e-comp-uni,e2e-comp-shop",
    )
    assert result.success, "prefixed tool should be reachable at JSON-RPC level"
    assert _is_error(result), (
        f"ISOLATION BREACH: shop tenant's prefixed db_search found students! {_result_text(result)[:200]}"
    )
    text = _result_text(result)
    assert "unknown_entity" in text or "404" in text, (
        f"Expected unknown_entity error, got: {text[:200]}"
    )
