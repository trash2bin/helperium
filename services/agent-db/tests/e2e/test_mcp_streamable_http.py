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


@pytest.mark.asyncio
async def test_streamable_http_session_is_bound_to_its_tenant_scope(
    tenant, mcp_gateway_url
):
    """A session ID minted for one tenant cannot be replayed under another."""
    first = tenant("sqlite-testseed", tenant_id="e2e-streamable-session-first")
    second = tenant("sqlite-testseed", tenant_id="e2e-streamable-session-second")
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "helperium-e2e", "version": "1"},
        },
    }
    tools_list = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}

    async with httpx2.AsyncClient(follow_redirects=True) as client:
        first_headers = {
            **_headers(first.id),
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        initialized = await client.post(
            f"{mcp_gateway_url}/mcp", headers=first_headers, json=initialize
        )
        assert initialized.status_code == 200, initialized.text
        session_id = initialized.headers.get("Mcp-Session-Id")
        assert session_id, "gateway did not issue a Streamable HTTP session ID"

        replay_headers = {
            **_headers(second.id),
            "Mcp-Session-Id": session_id,
            "MCP-Protocol-Version": "2025-11-25",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        replay = await client.post(
            f"{mcp_gateway_url}/mcp", headers=replay_headers, json=tools_list
        )
        assert replay.status_code == 404, replay.text

        closed = await client.delete(
            f"{mcp_gateway_url}/mcp",
            headers={
                **first_headers,
                "Mcp-Session-Id": session_id,
                "MCP-Protocol-Version": "2025-11-25",
            },
        )
    assert closed.status_code == 200, closed.text


@pytest.mark.asyncio
async def test_streamable_http_rejects_invalid_composite_scope_before_loading_manifest(
    tenant, mcp_gateway_url
):
    """Duplicate and oversized tenant headers cannot amplify registry work."""
    target = tenant("sqlite-testseed", tenant_id="e2e-streamable-scope-target")
    headers = {
        **_headers(),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    invalid_scopes = {
        "duplicate tenant": f"{target.id},{target.id}",
        "too many tenants": ",".join(f"scope-{index}" for index in range(9)),
    }

    async with httpx2.AsyncClient(headers=headers, follow_redirects=True) as client:
        for label, tenant_ids in invalid_scopes.items():
            response = await client.post(
                f"{mcp_gateway_url}/mcp",
                headers={"X-Tenant-ID": tenant_ids},
                content=b"{}",
            )
            assert response.status_code == 400, f"{label}: {response.text}"


@pytest.mark.asyncio
async def test_streamable_http_requires_service_auth_when_enabled(tenant, mcp_gateway_url):
    """A secure deployment rejects a request that omits its MCP bearer token."""
    if not os.environ.get("MCP_API_KEY"):
        pytest.skip("MCP_API_KEY is not set; stack intentionally runs without service auth")

    target = tenant("sqlite-testseed", tenant_id="e2e-streamable-auth-target")
    async with httpx2.AsyncClient(
        headers={
            "X-Tenant-ID": target.id,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
    ) as client:
        response = await client.post(f"{mcp_gateway_url}/mcp", content=b"{}")

    assert response.status_code == 401, response.text


@pytest.mark.asyncio
async def test_streamable_http_rejects_untrusted_browser_origin(tenant, mcp_gateway_url):
    """Configured Origin policy rejects browser requests outside its allow-list."""
    if not os.environ.get("MCP_ALLOWED_ORIGINS"):
        pytest.skip("MCP_ALLOWED_ORIGINS is not configured for this stack")

    target = tenant("sqlite-testseed", tenant_id="e2e-streamable-origin-target")
    async with httpx2.AsyncClient(
        headers={
            **_headers(target.id),
            "Origin": "https://attacker.invalid",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
    ) as client:
        response = await client.post(f"{mcp_gateway_url}/mcp", content=b"{}")

    assert response.status_code == 403, response.text
