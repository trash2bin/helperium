"""E2E test: MCP composite mode — one SSE session, multiple tenants.

Uses the ``tenants`` factory fixture with two different scenarios
(uni + shop) — the idiomatic way to build multi-tenant setups in the
extensible e2e architecture.

Does NOT require LLM. Requires data-service (:8084) + mcp-gateway (:8083).
"""

from __future__ import annotations

import pytest

from tests.e2e.helpers import mcp_call


# ── Helpers ────────────────────────────────────────────────────────────────


def _result_text(result) -> str:
    """Extract concatenated text from an MCPCallResult's content blocks."""
    content = result.result.get("content", [])
    return "".join(c.get("text", "") for c in content if "text" in c)


def _is_error(result) -> bool:
    """True if the tool returned a business-level isError (e.g. unknown entity)."""
    return bool(result.result.get("isError", False))


@pytest.fixture(scope="module")
def comp_tenants():
    """Two registered tenants with different schemas (uni + shop)."""
    from tests.e2e.helpers import make_tenant

    uni = make_tenant("sqlite-testseed", tenant_id="e2e-comp-uni", prefix="comp_uni")
    uni.register()

    # shop scenario: БД перегенерируется автоматически если битая/пустая
    from tests.e2e.helpers import ensure_scenario_db

    shop_db = ensure_scenario_db("shop")
    shop = make_tenant(
        config={
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
        },
        tenant_id="e2e-comp-shop",
        prefix="comp_shop",
        db_path=shop_db,
    )
    shop.register()

    yield uni, shop

    uni.cleanup()
    shop.cleanup()


# ── Tests ──────────────────────────────────────────────────────────────────


def test_composite_uni_db_map(comp_tenants):
    """Single-tenant session: db_map works (no prefix)."""
    uni, _ = comp_tenants
    result = uni.mcp_call("db_map")
    assert result, f"Composite uni db_map failed: {result.error}"
    assert not _is_error(result), f"db_map returned isError: {_result_text(result)[:200]}"
    text = _result_text(result)
    assert "student" in text.lower(), f"db_map should mention student: {text[:200]}"


def test_composite_shop_db_search(comp_tenants):
    """Single-tenant session: db_search works for shop."""
    _, shop = comp_tenants
    result = shop.mcp_call("db_search", {"entity": "product", "pattern": "MacBook"})
    assert result, f"Composite shop db_search failed: {result.error}"
    assert not _is_error(result), f"db_search returned isError: {_result_text(result)[:200]}"
    text = _result_text(result)
    assert "MacBook" in text, f"db_search should find MacBook: {text[:300]}"


def test_composite_both_tenants_in_one_session(comp_tenants):
    """One SSE session with both tenant IDs — prefixed tools work."""
    uni, _ = comp_tenants
    result = mcp_call(
        "e2e-comp-uni__db_map", {}, tenant_ids="e2e-comp-uni,e2e-comp-shop"
    )
    assert result, (
        f"Composite prefixed tool 'e2e-comp-uni__db_map' failed: {result.error}"
    )
    assert not _is_error(result), f"prefixed db_map returned isError: {_result_text(result)[:200]}"
    text = _result_text(result)
    assert "student" in text.lower(), f"prefixed db_map should mention student: {text[:200]}"


def test_composite_prefixed_shop_tool(comp_tenants):
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


def test_composite_cross_tenant_blocked(comp_tenants):
    """Composite: shop tenant cannot access uni's student entity."""
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
