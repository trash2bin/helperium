"""End-to-end coverage for the standard Streamable HTTP MCP transport.

This exercises the official MCP Python v2 client against the gateway's sole
modern `/mcp` endpoint.
"""

from __future__ import annotations

import os

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


@pytest.mark.asyncio
async def test_streamable_http_lists_tools_and_calls_read_only_tool(tenant, mcp_gateway_url):
    """A tenant-scoped v2 client can discover and invoke the standard MCP route."""
    test_tenant = tenant("sqlite-testseed", tenant_id="e2e-streamable-http")

    headers = {"X-Tenant-ID": test_tenant.id}
    if api_key := os.environ.get("MCP_API_KEY"):
        headers["Authorization"] = f"Bearer {api_key}"

    async with httpx2.AsyncClient(headers=headers, follow_redirects=True) as http_client:
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
