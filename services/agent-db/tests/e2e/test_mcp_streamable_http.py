"""End-to-end coverage for the standard Streamable HTTP MCP transport.

These checks use the official MCP Python v2 client against the gateway's sole
modern `/mcp` endpoint. They guard the public transport contract: tool discovery
and invocation, composite tenant scopes, and header-only tenant routing.
"""

from __future__ import annotations

import os

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


def _headers(tenant_ids: str | None = None) -> dict[str, str]:
    """Build gateway headers, adding service auth when the stack requires it."""
    headers: dict[str, str] = {}
    if tenant_ids:
        headers["X-Tenant-ID"] = tenant_ids
    if api_key := os.environ.get("MCP_API_KEY"):
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


@pytest.mark.asyncio
async def test_streamable_http_lists_tools_and_calls_read_only_tool(
    tenant, mcp_gateway_url
):
    """A tenant-scoped v2 client can discover and invoke a read-only tool."""
    test_tenant = tenant("sqlite-testseed", tenant_id="e2e-streamable-http")

    async with httpx2.AsyncClient(
        headers=_headers(test_tenant.id), follow_redirects=True
    ) as http_client:
        transport = streamable_http_client(
            f"{mcp_gateway_url}/mcp",
            http_client=http_client,
        )
        async with Client(transport) as client:
            tools = await client.list_tools()
            assert any(tool.name == "db_map" for tool in tools.tools)
            result = await client.call_tool("db_map", {})

    assert result.is_error is False
    text = "\n".join(
        block.text for block in result.content if getattr(block, "type", None) == "text"
    )
    assert "student" in text.lower()


@pytest.mark.asyncio
async def test_streamable_http_composite_scope_uses_prefixed_tools(
    tenant, mcp_gateway_url
):
    """One native v2 client can call a tool bound to its composite tenant scope."""
    first = tenant("sqlite-testseed", tenant_id="e2e-streamable-first")
    second = tenant("sqlite-testseed", tenant_id="e2e-streamable-second")
    tenant_ids = f"{first.id},{second.id}"

    async with httpx2.AsyncClient(
        headers=_headers(tenant_ids), follow_redirects=True
    ) as http_client:
        transport = streamable_http_client(
            f"{mcp_gateway_url}/mcp",
            http_client=http_client,
        )
        async with Client(transport) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools.tools}
            assert f"{first.id}__db_map" in names
            assert f"{second.id}__db_map" in names

            result = await client.call_tool(f"{first.id}__db_map", {})

    assert result.is_error is False
    text = "\n".join(
        block.text for block in result.content if getattr(block, "type", None) == "text"
    )
    assert "student" in text.lower()


@pytest.mark.asyncio
async def test_streamable_http_rejects_tenant_query_parameter_without_header(
    tenant, mcp_gateway_url
):
    """Tenant scope cannot be selected through a query string bypass."""
    target = tenant("sqlite-testseed", tenant_id="e2e-streamable-query-target")
    headers = {
        **_headers(),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    async with httpx2.AsyncClient(headers=headers, follow_redirects=True) as client:
        response = await client.post(
            f"{mcp_gateway_url}/mcp?tenant={target.id}",
            content=b"{}",
        )

    assert response.status_code == 400
    assert "X-Tenant-ID header is required" in response.text
