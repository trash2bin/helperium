"""Product-level E2E regressions for the default Helperium experience.

These tests deliberately exercise the externally visible chain rather than
individual service internals:

* default tenant runtime manifest is read-only and exposes canonical db_* tools;
* the demo proxy can display a generated ``strategy`` collection route;
* a Streamable HTTP single-tenant session discovers unprefixed tool names; and
* the authenticated Admin Dashboard emits an actionable aggregate status.

Requires the normal native/compose E2E stack. No live LLM is used.
"""

from __future__ import annotations

import os

import httpx2
import pytest
import requests
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


CANONICAL_DATA_TOOLS = {"db_map", "db_describe", "db_search", "db_get", "db_related"}


def _mcp_headers(tenant_id: str) -> dict[str, str]:
    headers = {"X-Tenant-ID": tenant_id}
    if api_key := os.environ.get("MCP_API_KEY"):
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def test_default_demo_manifest_is_read_only_and_serves_strategy_collection(
    data_service_url: str, demo_web_url: str,
):
    """The demo's default tenant exposes safe runtime tools and usable data tabs."""
    headers = {"X-Tenant-ID": "default"}
    runtime = requests.get(
        f"{data_service_url}/mcp/manifest", headers=headers, timeout=10
    )
    assert runtime.status_code == 200, runtime.text
    runtime_manifest = runtime.json()

    assert runtime_manifest.get("read_only") is True
    runtime_names = {tool["name"] for tool in runtime_manifest.get("mcp_tools", [])}
    assert CANONICAL_DATA_TOOLS <= runtime_names

    collection = next(
        (
            endpoint
            for endpoint in runtime_manifest.get("endpoints", [])
            if endpoint.get("method") == "GET"
            and endpoint.get("op") == "strategy"
            and endpoint.get("strategy") == "grep"
            and endpoint.get("entity") == "categories"
            and "{" not in endpoint.get("path", "")
        ),
        None,
    )
    assert collection is not None, "default manifest must expose the categories grep collection route"

    proxied_manifest = requests.get(
        f"{demo_web_url}/api/manifest", headers=headers, timeout=10
    )
    assert proxied_manifest.status_code == 200, proxied_manifest.text
    assert proxied_manifest.json().get("mcp_tools") == runtime_manifest.get("mcp_tools")

    # A generated grep strategy deliberately requires an explicit user search.
    # The demo proxy must forward a real query and return its preview rows.
    preview = requests.get(
        f"{demo_web_url}/api/data/{collection['path'].lstrip('/')}",
        headers=headers,
        params={"pattern": "Книги", "limit": 100},
        timeout=10,
    )
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    assert preview_body.get("returned", 0) >= 1
    assert any(row.get("name") == "Книги" for row in preview_body.get("preview", []))


@pytest.mark.asyncio
async def test_default_single_tenant_mcp_session_exposes_canonical_tool_names(
    mcp_gateway_url: str,
):
    """A real default single-tenant MCP session never receives tenant prefixes."""
    async with httpx2.AsyncClient(
        headers=_mcp_headers("default"), follow_redirects=True
    ) as http_client:
        transport = streamable_http_client(
            f"{mcp_gateway_url}/mcp", http_client=http_client
        )
        async with Client(transport) as client:
            tools = await client.list_tools()

    names = {tool.name for tool in tools.tools}
    assert CANONICAL_DATA_TOOLS <= names
    assert not any(name.startswith("default__") for name in names)


def test_admin_dashboard_reports_actionable_healthy_status(admin_dashboard_url: str):
    """Dashboard returns `ok` instead of an ambiguous status when data-service works."""
    token = os.environ.get("ADMIN_TOKEN") or os.environ.get("ADMIN_API_TOKEN")
    if not token:
        pytest.skip("ADMIN_TOKEN is required for the authenticated dashboard contract")

    response = requests.get(
        f"{admin_dashboard_url}/api/dashboard",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    assert response.status_code == 200, response.text
    dashboard = response.json()
    assert dashboard.get("status") == "ok"
    assert dashboard.get("tenant_count", 0) >= 1
