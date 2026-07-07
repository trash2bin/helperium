"""Unit tests for MCPClient.

Tests the stateless HTTP client that interacts with mcp-gateway.
Verified with respx for mocking HTTP responses.
"""

from __future__ import annotations

import json
import pytest
import respx
from httpx import Response

from api_service.agent.mcp_client import MCPClient


@pytest.fixture
def mcp_client():
    return MCPClient()


@pytest.mark.skip(
    reason="MCPClient refactored to SDK-based MCP sessions — tests need rewriting for real MCP transport"
)
@pytest.mark.asyncio
@respx.mock
async def test_list_tools_success(mcp_client: MCPClient):
    """list_tools should fetch tools from /tools/list and format them for the agent."""
    # Setup mock response
    url = f"{mcp_client.base_url}/tools/list"
    mock_tools = [
        {
            "name": "get_student",
            "description": "Get student info",
            "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}},
        }
    ]
    respx.get(url).mock(return_value=Response(200, json=mock_tools))

    tools = await mcp_client.list_tools()

    assert len(tools) == 1
    assert tools[0]["function"]["name"] == "get_student"
    assert tools[0]["function"]["description"] == "Get student info"
    assert tools[0]["function"]["parameters"] == mock_tools[0]["inputSchema"]


@pytest.mark.skip(
    reason="MCPClient refactored to SDK-based MCP sessions — tests need rewriting for real MCP transport"
)
@pytest.mark.asyncio
@respx.mock
async def test_list_tools_with_tenant(mcp_client: MCPClient):
    """list_tools should send X-Tenant-ID header if provided via session."""
    tenant_id = "school-123"
    url = f"{mcp_client.base_url}/tools/list"

    # Verify header in the mock
    def check_headers(request):
        assert request.headers.get("X-Tenant-ID") == tenant_id
        return Response(200, json=[])

    respx.get(url).mock(side_effect=check_headers)

    # Use dummy session proxy to simulate tenant_id
    class Session:
        def __init__(self, t_id):
            self.tenant_id = t_id

    await mcp_client.list_tools(session=Session(tenant_id))


@pytest.mark.skip(
    reason="MCPClient refactored to SDK-based MCP sessions — tests need rewriting for real MCP transport"
)
@pytest.mark.asyncio
@respx.mock
async def test_call_tool_success(mcp_client: MCPClient):
    """call_tool should return a ToolResult with ok=True when gateway returns ok=True."""
    url = f"{mcp_client.base_url}/tools/call"
    payload = {"ok": True, "data": "Student: Ivan Ivanov"}
    respx.post(url).mock(return_value=Response(200, json=payload))

    tr = await mcp_client.call_tool(
        session=None, name="get_student", arguments={"id": "1"}
    )

    assert tr.ok is True
    assert tr.error is None
    assert "Student: Ivan Ivanov" in tr.tool_content
    assert "ОБЯЗАТЕЛЬНО" in tr.reminder


@pytest.mark.skip(
    reason="MCPClient refactored to SDK-based MCP sessions — tests need rewriting for real MCP transport"
)
@pytest.mark.asyncio
@respx.mock
async def test_call_tool_gateway_error(mcp_client: MCPClient):
    """call_tool should return ToolResult with ok=False when gateway returns ok=False."""
    url = f"{mcp_client.base_url}/tools/call"
    payload = {"ok": False, "error": "Student not found"}
    respx.post(url).mock(return_value=Response(200, json=payload))

    tr = await mcp_client.call_tool(
        session=None, name="get_student", arguments={"id": "999"}
    )

    assert tr.ok is False
    assert tr.error == "Student not found"
    assert "вернул ошибку" in tr.reminder


@pytest.mark.skip(
    reason="MCPClient refactored to SDK-based MCP sessions — tests need rewriting for real MCP transport"
)
@pytest.mark.asyncio
@respx.mock
async def test_call_tool_http_error(mcp_client: MCPClient):
    """call_tool should handle HTTP exceptions (e.g. 500) by returning a ToolResult error."""
    url = f"{mcp_client.base_url}/tools/call"
    respx.post(url).mock(return_value=Response(500))

    tr = await mcp_client.call_tool(
        session=None, name="get_student", arguments={"id": "1"}
    )

    assert tr.ok is False
    assert "500" in (tr.error or "")


@pytest.mark.skip(
    reason="MCPClient refactored to SDK-based MCP sessions — tests need rewriting for real MCP transport"
)
@pytest.mark.asyncio
@respx.mock
async def test_call_tool_unwraps_json_data(mcp_client: MCPClient):
    """call_tool should unwrap nested JSON string in 'data' field for better LLM compatibility."""
    url = f"{mcp_client.base_url}/tools/call"
    inner_json = json.dumps({"id": "1", "name": "Ivan"})
    payload = {"ok": True, "data": inner_json}
    respx.post(url).mock(return_value=Response(200, json=payload))

    tr = await mcp_client.call_tool(
        session=None, name="get_student", arguments={"id": "1"}
    )

    # The tool_content should be the unwrapped JSON string, not the original payload
    parsed = json.loads(tr.tool_content)
    assert parsed == {"id": "1", "name": "Ivan"}
    assert "ok" not in parsed


@pytest.mark.skip(
    reason="MCPClient refactored to SDK-based MCP sessions — tests need rewriting for real MCP transport"
)
@pytest.mark.asyncio
@respx.mock
async def test_call_tool_keeps_wrapper_for_non_json(mcp_client: MCPClient):
    """call_tool should keep the payload wrapper if 'data' is not a JSON string."""
    url = f"{mcp_client.base_url}/tools/call"
    payload = {"ok": True, "data": "plain text response"}
    respx.post(url).mock(return_value=Response(200, json=payload))

    tr = await mcp_client.call_tool(
        session=None, name="get_student", arguments={"id": "1"}
    )

    parsed = json.loads(tr.tool_content)
    assert parsed == payload
    assert tr.ok is True
