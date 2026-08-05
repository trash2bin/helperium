"""E2E test: MCP dynamic tool resolution — tool isolation between tenants.

Tests that:
1. MCP Session opens successfully and returns endpoint URL
2. Tools are listed for each tenant
3. Each tenant can call its own tool (v5 consolidated db_* surface)
4. Cross-tenant tool call is blocked (isolation)
5. Non-existent tool returns error

Tool surface (v5, see .data/e2e_revision_ground_truth.md):
- 5 consolidated db_* tools: db_map, db_describe, db_search, db_get, db_related
- per-entity filter_{entity} (only when a filter endpoint exists)
- db_filter does NOT exist; get_*/count_*/distinct_* NOT emitted by default

Does NOT require LLM. Requires data-service (:8084) + mcp-gateway (:8083) running.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.e2e.helpers import (
    cleanup_db,
    delete_tenant,
    mcp_call,
    project_root,
    register_tenant,
    seed_database,
    temp_db_path,
    ensure_scenario_db,
)


# ── Module-level state ─────────────────────────────────────────────────────

_TENANT_A = "e2e-mcp-uni"
_TENANT_B = "e2e-mcp-shop"
_DB_A: Path | None = None
_DB_B: Path | None = None


def setup_module(module):
    """Setup: seed two databases, register two tenants with different schemas."""
    global _DB_A, _DB_B
    root = project_root()
    _DB_A = temp_db_path("mcp_uni")
    _DB_B = temp_db_path("mcp_shop")

    # Seed university DB
    seed_database(_DB_A, scenario="sqlite-testseed", project_root_dir=root)
    shop_db = ensure_scenario_db("shop")

    # Register tenants — different schemas
    for tid, db_path in [(_TENANT_A, _DB_A), (_TENANT_B, shop_db)]:
        delete_tenant(tid)

    # NOTE: the "mcp_tools" key in these configs is IGNORED by data-service —
    # the MCP manifest is regenerated from endpoints via GenerateMCPTools
    # (runtime/handlers/mcp_manifest.go). v5 emits only the 5 consolidated
    # db_* tools + per-entity filter_{entity} when a filter endpoint exists.
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
            {
                "method": "GET",
                "path": "/students/{id}",
                "op": "get_by_id",
                "entity": "student",
            },
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

    r1 = register_tenant(_TENANT_A, uni_config)
    assert r1["status"] in (200, 201), f"Register {_TENANT_A}: {r1['status']}"

    r2 = register_tenant(_TENANT_B, shop_config)
    assert r2["status"] in (200, 201), f"Register {_TENANT_B}: {r2['status']}"


def teardown_module(module):
    """Cleanup."""
    delete_tenant(_TENANT_A)
    delete_tenant(_TENANT_B)
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


def test_mcp_uni_tool_db_map():
    """University tenant can call db_map (v5 consolidated tool)."""
    result = mcp_call("db_map", {}, tenant_ids=_TENANT_A)
    assert result, f"MCP db_map failed: {result.error}"
    assert not _is_error(result), f"db_map returned isError: {_result_text(result)[:200]}"
    text = _result_text(result)
    assert "student" in text.lower(), f"db_map should mention student entity: {text[:200]}"


def test_mcp_uni_tool_search_then_get():
    """University tenant: db_search to find a student, then db_get by id.

    Anti-enumeration: id comes from a prior db_search result, never hardcoded.
    """
    # 1. Search first to obtain a real id
    search = mcp_call(
        "db_search",
        {"entity": "student", "pattern": "Иван"},  # guaranteed in sqlite-testseed
        tenant_ids=_TENANT_A,
    )
    assert search, f"MCP db_search failed: {search.error}"
    assert not _is_error(search), f"db_search returned isError: {_result_text(search)[:200]}"
    text = _result_text(search)
    assert '"id"' in text, f"db_search should return an id: {text[:300]}"

    # Extract the first id from db_search result
    m = re.search(r'"id"\s*:\s*"([^"]+)"', text)
    assert m, f"db_search should return a quoted id: {text[:300]}"
    sid = m.group(1)

    # 2. db_get by the id we just found
    result = mcp_call(
        "db_get", {"entity": "student", "id": sid}, tenant_ids=_TENANT_A
    )
    assert result, f"MCP db_get failed: {result.error}"
    assert not _is_error(result), f"db_get returned isError: {_result_text(result)[:200]}"
    get_text = _result_text(result)
    assert sid in get_text, f"db_get should return the record with id {sid}: {get_text[:300]}"


def test_mcp_shop_tool_db_search():
    """Shop tenant can search its products via db_search."""
    result = mcp_call(
        "db_search", {"entity": "product", "pattern": "iPhone"}, tenant_ids=_TENANT_B
    )
    assert result, f"MCP db_search failed: {result.error}"
    assert not _is_error(result), f"db_search returned isError: {_result_text(result)[:200]}"
    text = _result_text(result)
    assert "iPhone" in text, f"db_search should find iPhone: {text[:300]}"


def test_mcp_shop_cannot_call_uni_tool():
    """Shop tenant CANNOT search the university's student entity (isolation)."""
    result = mcp_call(
        "db_search", {"entity": "student", "pattern": "x"}, tenant_ids=_TENANT_B
    )
    # JSON-RPC transport succeeds, but the tool returns a business-level isError
    # (404 unknown_entity via EntityResolver whitelist).
    assert result.success, "db_search should be reachable at JSON-RPC level"
    assert _is_error(result), (
        f"ISOLATION BREACH: shop tenant searched student successfully! {_result_text(result)[:200]}"
    )
    text = _result_text(result)
    assert "unknown_entity" in text or "404" in text, (
        f"Expected unknown_entity error, got: {text[:200]}"
    )


def test_mcp_unknown_tool_returns_error():
    """Calling a non-existent tool returns error."""
    result = mcp_call("nonexistent_tool_xyz", tenant_ids=_TENANT_A)
    assert not result, "Non-existent tool should fail"
    assert result.error, "Non-existent tool should return error message"
