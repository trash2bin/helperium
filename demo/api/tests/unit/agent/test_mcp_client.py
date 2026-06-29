import pytest
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from mcp import ClientSession

from demo.api.agent.mcp_client import MCPClient


@pytest.mark.asyncio
async def test_mcp_client_list_tools():
    # Setup mock session
    mock_session = AsyncMock(spec=ClientSession)
    mock_tools_result = MagicMock()

    # Mocking individual tool
    tool1 = MagicMock()
    tool1.name = "get_student"
    tool1.description = "Get student info"
    tool1.inputSchema = {"type": "object"}

    mock_tools_result.tools = [tool1]
    mock_session.list_tools.return_value = mock_tools_result

    client = MCPClient()
    tools = await client.list_tools(mock_session)

    assert len(tools) == 1
    assert tools[0]["function"]["name"] == "get_student"
    assert tools[0]["function"]["parameters"] == {"type": "object"}


@pytest.mark.asyncio
async def test_mcp_client_call_tool_success():
    mock_session = AsyncMock(spec=ClientSession)

    # Mock result with text content
    mock_result = MagicMock()
    mock_result.isError = False
    mock_result.content = [MagicMock(text="Student found")]
    mock_result.structuredContent = None

    mock_session.call_tool.return_value = mock_result

    client = MCPClient()
    response = await client.call_tool(mock_session, "get_student", {"id": "123"})

    data = json.loads(response)
    assert data["ok"] is True
    assert data["data"] == "Student found"


@pytest.mark.asyncio
async def test_mcp_client_call_tool_error():
    mock_session = AsyncMock(spec=ClientSession)

    # Mock result error
    mock_result = MagicMock()
    mock_result.isError = True
    mock_result.content = [MagicMock(text="Error message")]

    mock_session.call_tool.return_value = mock_result

    client = MCPClient()
    response = await client.call_tool(mock_session, "get_student", {"id": "123"})

    data = json.loads(response)
    assert data["ok"] is False
    assert data["error"] == "Error message"


@pytest.mark.asyncio
async def test_close_schedules_background_task_when_called_from_different_task():
    """cancel-scope регрессия: close() из чужой task должен планировать
    фоновое закрытие через create_task, а не вызывать __aexit__ напрямую.
    """
    client = MCPClient()

    opened = AsyncMock()
    opened.__aexit__ = AsyncMock(return_value=None)
    streams = AsyncMock()
    streams.__aexit__ = AsyncMock(return_value=None)

    client._session = MagicMock()
    client._session_cm = opened
    client._streams_cm = streams
    # owner_task — «та, что открыла» (симулируем через MagicMock —
    # close() сравнивает через `is`, и текущая task точно не она)
    client._owner_task = MagicMock(spec=asyncio.Task)

    await client.close()

    bg = getattr(client, "_background_close_task", None)
    assert bg is not None, "background close task must be scheduled"
    await bg
    opened.__aexit__.assert_awaited_once()
    streams.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_is_idempotent_and_concurrent_safe():
    """Повторный close() (например, в lifespan + при ошибке) не должен
    закрывать контекст-менеджеры дважды.
    """
    client = MCPClient()

    opened = AsyncMock()
    opened.__aexit__ = AsyncMock(return_value=None)
    streams = AsyncMock()
    streams.__aexit__ = AsyncMock(return_value=None)

    client._session = MagicMock()
    client._session_cm = opened
    client._streams_cm = streams
    client._owner_task = asyncio.current_task()

    await client.close()
    await client.close()  # второй раз — no-op

    opened.__aexit__.assert_awaited_once()
    streams.__aexit__.assert_awaited_once()
