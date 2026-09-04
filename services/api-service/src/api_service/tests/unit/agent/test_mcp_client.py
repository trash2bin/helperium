"""Unit tests for MCPClient.

Tests the MCP SDK-based client by mocking _get_connection to avoid
real SSE connections. Follows the same pattern as test_mcp_client_timeout.py.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from api_service.agent.mcp_client import (
    MCPClient,
    _SessionProxy,
    _TenantConnection,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_conn() -> MagicMock:
    """Build a mock _TenantConnection with a mock session.

    Both ``call_lock`` and ``list_lock`` are mocked so that
    ``async with`` context-manager calls succeed immediately.
    """
    conn = MagicMock()
    conn.tenant_id = "test-tenant"
    conn.session = AsyncMock()
    # Locks use acquire()/release() (bounded lock wait is separate from tool
    # execution), not async-with protocol.
    conn.call_lock = MagicMock()
    conn.call_lock.acquire = AsyncMock(return_value=True)
    conn.call_lock.release = MagicMock()
    conn.list_lock = MagicMock()
    # Production takes list_lock via acquire()/release() with split budgets
    # (same pattern as call_lock), not async-with.
    conn.list_lock.acquire = AsyncMock(return_value=True)
    conn.list_lock.release = MagicMock()
    conn.consecutive_tool_timeouts = 0
    return conn


def _mock_tool(
    name: str, description: str, input_schema: dict | None = None
) -> MagicMock:
    """Create a mock MCP Tool object."""
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.input_schema = input_schema or {"type": "object", "properties": {}}
    return tool


def _mock_result(content_parts: list[dict], is_error: bool = False) -> MagicMock:
    """Create a mock CallToolResult."""
    result = MagicMock()
    result.content = []
    for part in content_parts:
        block = MagicMock()
        block.type = part.get("type", "text")
        block.text = part.get("text", "")
        result.content.append(block)
    result.is_error = is_error
    return result


@pytest.fixture
def mcp_client() -> MCPClient:
    return MCPClient()


# ── _build_tool_result tests ─────────────────────────────────────────────────


class TestBuildToolResult:
    """Tests for _build_tool_result — pure static method, no mocking needed."""

    def test_success_text_result(self):
        """Text-only success result should produce ok=True with content."""
        result = _mock_result([{"type": "text", "text": "Student: Ivan"}])
        tr = MCPClient._build_tool_result("get_student", result)
        assert tr.ok is True
        assert tr.error is None
        assert "Student: Ivan" in tr.tool_content
        assert "ОБЯЗАТЕЛЬНО" in tr.reminder

    def test_success_json_unwrap(self):
        """JSON string in text content should be unwrapped."""
        inner = {"id": "1", "name": "Ivan"}
        result = _mock_result([{"type": "text", "text": json.dumps(inner)}])
        tr = MCPClient._build_tool_result("get_student", result)
        assert tr.ok is True
        parsed = json.loads(tr.tool_content)
        assert parsed == inner

    def test_error_result(self):
        """is_error=True should produce ok=False with error message."""
        result = _mock_result(
            [{"type": "text", "text": "Student not found"}], is_error=True
        )
        tr = MCPClient._build_tool_result("find_student", result)
        assert tr.ok is False
        assert tr.error == "Student not found"
        assert "TOOL_ERROR" in tr.reminder
        assert "'find_student'" in tr.reminder
        assert "FAILED" in tr.reminder

    def test_error_result_preserves_structured_error_code(self):
        result = _mock_result(
            [
                {
                    "type": "text",
                    "text": '{"error_code":"INVALID_RELATION","message":"unknown relation"}',
                }
            ],
            is_error=True,
        )

        tool_result = MCPClient._build_tool_result("db_related", result)

        assert tool_result.ok is False
        assert tool_result.error_code == "INVALID_RELATION"
        assert json.loads(tool_result.tool_content)["error_code"] == "INVALID_RELATION"

    def test_error_reminder_uses_actual_tool_name(self):
        """Error reminder example should reference the actual tool name, not a hardcoded one."""
        result = _mock_result(
            [{"type": "text", "text": "Missing required pattern"}], is_error=True
        )
        tr = MCPClient._build_tool_result("search_doctors", result)
        assert tr.ok is False
        assert "TOOL_ERROR" in tr.reminder
        assert "'search_doctors'" in tr.reminder
        # The example in the reminder must use the actual tool name, not 'search_auto_parts'
        assert "search_auto_parts" not in tr.reminder

    def test_empty_result(self):
        """Empty or null text should produce ok=True with data=None."""
        for empty in ["", "null"]:
            result = _mock_result([{"type": "text", "text": empty}])
            tr = MCPClient._build_tool_result("find_student", result)
            assert tr.ok is True
            assert "записи нет" in tr.reminder

    def test_none_string_result(self):
        """The literal 'None' string is NOT valid JSON and must survive as raw text."""
        result = _mock_result([{"type": "text", "text": "None"}])
        tr = MCPClient._build_tool_result("find_student", result)
        assert tr.ok is True
        assert tr.tool_content is not None

    def test_multiple_text_blocks(self):
        """Multiple text blocks should be joined with newlines."""
        result = _mock_result(
            [
                {"type": "text", "text": "line1"},
                {"type": "text", "text": "line2"},
            ]
        )
        tr = MCPClient._build_tool_result("get_data", result)
        assert tr.ok is True
        assert "line1\nline2" in tr.tool_content


# ── list_tools tests ─────────────────────────────────────────────────────────


class TestListTools:
    """Tests for MCPClient.list_tools."""

    async def _session(self, client: MCPClient):
        return _SessionProxy(client, tenant_ids=[])

    @pytest.mark.asyncio
    async def test_list_tools_success(self, mcp_client: MCPClient):
        """list_tools should return formatted tool dicts."""
        conn = _make_conn()
        conn.session.list_tools = AsyncMock(
            return_value=MagicMock(
                tools=[
                    _mock_tool(
                        "get_student",
                        "Get student info",
                        {"type": "object", "properties": {"id": {"type": "string"}}},
                    ),
                ]
            )
        )
        mcp_client._get_connection = AsyncMock(return_value=conn)  # type: ignore[method-assign]

        session = await self._session(mcp_client)
        tools = await mcp_client.list_tools(session)

        assert len(tools) == 1
        assert tools[0]["function"]["name"] == "get_student"
        assert tools[0]["function"]["description"] == "Get student info"
        assert tools[0]["function"]["parameters"] == {
            "type": "object",
            "properties": {"id": {"type": "string"}},
        }

    @pytest.mark.asyncio
    async def test_list_tools_reconnect_on_failure(self, mcp_client: MCPClient):
        """list_tools should reconnect on first failure and retry."""
        conn = _make_conn()
        conn.session.list_tools = AsyncMock()
        conn.session.list_tools.side_effect = Exception("Connection lost")

        reconn = _make_conn()
        reconn.session.list_tools = AsyncMock(
            return_value=MagicMock(
                tools=[_mock_tool("get_student", "Get student info")]
            )
        )

        mcp_client._get_connection = AsyncMock(return_value=conn)
        mcp_client._reconnect = AsyncMock(return_value=reconn)  # type: ignore[method-assign]

        session = await self._session(mcp_client)
        tools = await mcp_client.list_tools(session)

        assert len(tools) == 1
        assert tools[0]["function"]["name"] == "get_student"
        mcp_client._reconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_list_tools_tool_not_found_returns_empty(self, mcp_client: MCPClient):
        """'Tool not found' error should not trigger reconnect."""
        conn = _make_conn()
        conn.session.list_tools = AsyncMock(side_effect=Exception("Tool not found"))
        mcp_client._get_connection = AsyncMock(return_value=conn)
        mcp_client._reconnect = AsyncMock()

        session = await self._session(mcp_client)
        tools = await mcp_client.list_tools(session)

        assert tools == []
        mcp_client._reconnect.assert_not_called()


# ── call_tool tests ──────────────────────────────────────────────────────────


class TestCallTool:
    """Tests for MCPClient.call_tool."""

    async def _session(self, client: MCPClient):
        return _SessionProxy(client, tenant_ids=[])

    @pytest.mark.asyncio
    async def test_call_tool_success(self, mcp_client: MCPClient):
        """call_tool should return ok=True with content."""
        conn = _make_conn()
        conn.session.call_tool = AsyncMock(
            return_value=_mock_result(
                [{"type": "text", "text": "Student: Ivan Ivanov"}]
            )
        )
        mcp_client._get_connection = AsyncMock(return_value=conn)

        session = await self._session(mcp_client)
        tr = await mcp_client.call_tool(session, "get_student", {"id": "1"})

        assert tr.ok is True
        assert tr.error is None
        assert "Student: Ivan Ivanov" in tr.tool_content
        assert "ОБЯЗАТЕЛЬНО" in tr.reminder
        conn.session.call_tool.assert_awaited_once_with("get_student", {"id": "1"})

    @pytest.mark.asyncio
    async def test_call_tool_transport_cancellation_returns_retryable_result(
        self, mcp_client: MCPClient
    ):
        """An MCP transport cancellation is data-plane failure, not a silent SSE end."""
        conn = _make_conn()
        conn.session.call_tool = AsyncMock(side_effect=asyncio.CancelledError())
        mcp_client._get_connection = AsyncMock(return_value=conn)  # type: ignore[method-assign]

        session = await self._session(mcp_client)
        result = await mcp_client.call_tool(session, "db_search", {"pattern": "books"})

        assert result.ok is False
        assert result.error == "Tool connection was interrupted; retry shortly."
        assert "connection was interrupted" in result.tool_content
        assert "127.0.0.1" not in result.tool_content

    @pytest.mark.asyncio
    async def test_call_tool_shared_parent_cancellation_returns_retryable_result(
        self, mcp_client: MCPClient
    ):
        """The SDK may cancel its parent on GET-stream loss; it is retryable."""
        parent_task = asyncio.current_task()
        assert parent_task is not None

        async def cancel_parent(*_args, **_kwargs):
            parent_task.cancel()
            await asyncio.Event().wait()

        conn = _make_conn()
        conn.session.call_tool = AsyncMock(side_effect=cancel_parent)
        mcp_client._get_connection = AsyncMock(return_value=conn)  # type: ignore[method-assign]

        session = await self._session(mcp_client)
        result = await mcp_client.call_tool(session, "db_search", {"pattern": "books"})

        assert result.ok is False
        assert result.error == "Tool connection was interrupted; retry shortly."
        assert parent_task.cancelling() == 0

    @pytest.mark.asyncio
    async def test_call_tool_reconnect_cancellation_returns_retryable_result(
        self, mcp_client: MCPClient
    ):
        """Cancellation during retry must not end a connected chat SSE stream."""
        initial_conn = _make_conn()
        initial_conn.session.call_tool = AsyncMock(side_effect=ConnectionError("reset"))
        retry_conn = _make_conn()
        retry_conn.session.call_tool = AsyncMock(side_effect=asyncio.CancelledError())
        mcp_client._get_connection = AsyncMock(return_value=initial_conn)  # type: ignore[method-assign]
        mcp_client._reconnect = AsyncMock(return_value=retry_conn)  # type: ignore[method-assign]

        session = await self._session(mcp_client)
        result = await mcp_client.call_tool(session, "db_search", {"pattern": "books"})

        assert result.ok is False
        assert result.error == "Tool connection was interrupted; retry shortly."
        mcp_client._reconnect.assert_awaited_once_with(session.tenant_ids)  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_call_tool_gateway_error(self, mcp_client: MCPClient):
        """call_tool should return ok=False when tool returns error."""
        conn = _make_conn()
        conn.session.call_tool = AsyncMock(
            return_value=_mock_result(
                [{"type": "text", "text": "Student not found"}], is_error=True
            )
        )
        mcp_client._get_connection = AsyncMock(return_value=conn)

        session = await self._session(mcp_client)
        tr = await mcp_client.call_tool(session, "get_student", {"id": "999"})

        assert tr.ok is False
        assert tr.error == "Student not found"
        assert "TOOL_ERROR" in tr.reminder
        assert "'get_student'" in tr.reminder
        assert "FAILED" in tr.reminder

    @pytest.mark.asyncio
    async def test_call_tool_reconnect_on_failure(self, mcp_client: MCPClient):
        """call_tool should reconnect on non-ToolNotFound error and retry."""
        conn = _make_conn()
        conn.session.call_tool = AsyncMock(side_effect=Exception("Connection lost"))
        mcp_client._get_connection = AsyncMock(return_value=conn)

        reconn = _make_conn()
        reconn.session.call_tool = AsyncMock(
            return_value=_mock_result([{"type": "text", "text": "Retry worked"}])
        )
        mcp_client._reconnect = AsyncMock(return_value=reconn)

        session = await self._session(mcp_client)
        tr = await mcp_client.call_tool(session, "retry_test", {"arg": 1})

        assert tr.ok is True
        assert "Retry worked" in tr.tool_content
        mcp_client._reconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_call_tool_tool_not_found_returns_error(self, mcp_client: MCPClient):
        """'Tool not found' error should NOT trigger reconnect."""
        conn = _make_conn()
        conn.session.call_tool = AsyncMock(side_effect=Exception("Tool not found"))
        mcp_client._get_connection = AsyncMock(return_value=conn)
        mcp_client._reconnect = AsyncMock()

        session = await self._session(mcp_client)
        tr = await mcp_client.call_tool(session, "ghost_tool", {})

        assert tr.ok is False
        assert "Tool not found" in (tr.error or "")
        mcp_client._reconnect.assert_not_called()

    @pytest.mark.asyncio
    async def test_call_tool_unwraps_json_data(self, mcp_client: MCPClient):
        """JSON text content should be unwrapped for cleaner tool_content."""
        inner = {"id": "1", "name": "Ivan"}
        conn = _make_conn()
        conn.session.call_tool = AsyncMock(
            return_value=_mock_result([{"type": "text", "text": json.dumps(inner)}])
        )
        mcp_client._get_connection = AsyncMock(return_value=conn)

        session = await self._session(mcp_client)
        tr = await mcp_client.call_tool(session, "get_student", {"id": "1"})

        parsed = json.loads(tr.tool_content)
        assert parsed == inner

    @pytest.mark.asyncio
    async def test_call_tool_keeps_plain_text(self, mcp_client: MCPClient):
        """Plain text (not JSON) should be kept as-is."""
        conn = _make_conn()
        conn.session.call_tool = AsyncMock(
            return_value=_mock_result([{"type": "text", "text": "plain text response"}])
        )
        mcp_client._get_connection = AsyncMock(return_value=conn)

        session = await self._session(mcp_client)
        tr = await mcp_client.call_tool(session, "greet", {"who": "world"})

        assert "plain text response" in tr.tool_content


class TestReconnectRace:
    """_reconnect raced-insert must keep exactly one live connection."""

    @pytest.mark.asyncio
    async def test_concurrent_reconnects_keep_winner_close_loser(
        self, mcp_client: MCPClient
    ):
        conn_old = _make_conn()
        conn_old.close = AsyncMock()
        mcp_client._connections["t9"] = conn_old

        conn_a = _make_conn()
        conn_b = _make_conn()
        conn_a.tenant_id = "t9"
        conn_b.tenant_id = "t9"
        gate = asyncio.Event()

        async def fake_open(_tenant_ids=None):
            await gate.wait()
            if not opened:
                opened.append(conn_a)
                return conn_a
            opened.append(conn_b)
            return conn_b

        opened: list[_TenantConnection | None] = []

        mcp_client._open_connection = AsyncMock(side_effect=fake_open)  # type: ignore[method-assign]
        close_spy = AsyncMock()
        mcp_client._close_redundant_connection = AsyncMock(side_effect=close_spy)  # type: ignore[method-assign]

        task_a = asyncio.create_task(mcp_client._reconnect(["t9"]))
        task_b = asyncio.create_task(mcp_client._reconnect(["t9"]))
        await asyncio.sleep(0.02)
        gate.set()
        results = await asyncio.gather(task_a, task_b, return_exceptions=True)

        stored = mcp_client._connections.get("t9")
        assert stored is conn_a or stored is conn_b
        for r in results:
            assert not isinstance(r, BaseException), f"reconnect raised: {r}"
        # Loser must be scheduled/awaited for close, never silently dropped.
        assert mcp_client._close_redundant_connection.await_count >= 1


class TestBreakerStore:
    """Circuit-breaker evidence accumulates even without registry entries."""

    @pytest.mark.asyncio
    async def test_cold_handshake_failure_accumulates_breaker_state(
        self, mcp_client: MCPClient
    ):
        async def failing_open(_tenant_ids=None):
            raise ConnectionError("gateway down")

        mcp_client._open_connection = AsyncMock(side_effect=failing_open)  # type: ignore[method-assign]
        session = _SessionProxy(mcp_client, tenant_ids=["dead-tenant"])

        tr = await mcp_client.call_tool(session, "db_search", {})
        assert tr.ok is False
        failures_before = self._store_failures(mcp_client, "dead-tenant")
        assert failures_before >= 1, "cold failure left no breaker evidence"

        tr2 = await mcp_client.call_tool(session, "db_search", {})
        assert tr2.ok is False
        failures_after = self._store_failures(mcp_client, "dead-tenant")
        assert failures_after == failures_before + 1

    @pytest.mark.asyncio
    async def test_list_tools_handshake_failure_returns_empty_and_marks(
        self, mcp_client: MCPClient
    ):
        async def failing_open(_tenant_ids=None):
            raise ConnectionError("gateway down")

        mcp_client._open_connection = AsyncMock(side_effect=failing_open)  # type: ignore[method-assign]
        session = _SessionProxy(mcp_client, tenant_ids=["dead-tenant"])
        tools = await mcp_client.list_tools(session)
        assert tools == []
        assert session.list_tools_failed is True
        assert self._store_failures(mcp_client, "dead-tenant") >= 1

    @staticmethod
    def _store_failures(client: MCPClient, key: str) -> int:
        return client._breaker_state.get(key, (0, 0.0))[0]


class TestMCPLogPrivacy:
    """INFO diagnostics retain operational context without tool payloads."""

    @pytest.mark.asyncio
    async def test_call_log_exposes_only_argument_count(
        self, mcp_client: MCPClient, caplog: pytest.LogCaptureFixture
    ):
        conn = _make_conn()
        conn.session.call_tool = AsyncMock(
            return_value=_mock_result([{"type": "text", "text": "ok"}])
        )
        mcp_client._get_connection = AsyncMock(return_value=conn)  # type: ignore[method-assign]
        arguments = {"email": "ivan@example.test", "access_token": "super-secret"}

        caplog.set_level(logging.INFO, logger="api_service.agent.mcp_client")
        await mcp_client.call_tool(
            _SessionProxy(mcp_client, tenant_ids=["private-tenant"]),
            "db_search",
            arguments,
        )

        messages = [
            record.getMessage()
            for record in caplog.records
            if "Calling tool" in record.getMessage()
        ]
        assert messages == [
            "[MCP] Calling tool db_search for tenants=['private-tenant'] (argument_count=2)"
        ]
        assert "email" not in messages[0]
        assert "ivan@example.test" not in messages[0]
        assert "access_token" not in messages[0]
        assert "super-secret" not in messages[0]

    def test_result_log_exposes_length_but_not_content(
        self, caplog: pytest.LogCaptureFixture
    ):
        raw_result = "customer email=ivan@example.test token=super-secret"

        caplog.set_level(logging.INFO, logger="api_service.agent.mcp_client")
        MCPClient._build_tool_result(
            "db_search", _mock_result([{"type": "text", "text": raw_result}])
        )

        messages = [
            record.getMessage()
            for record in caplog.records
            if "_build_tool_result" in record.getMessage()
        ]
        assert messages == [
            "[MCP] _build_tool_result for db_search: is_error=False, result_length=51"
        ]
        assert raw_result not in messages[0]
        assert "ivan@example.test" not in messages[0]
        assert "super-secret" not in messages[0]


class TestMCPCallTimeoutContract:
    """Production callers must not swallow a caller-owned cancellation."""

    def test_production_callers_do_not_wrap_mcp_call_in_deadline(self):
        """RED for direct nesting or a simple local alias in one function.

        MCPClient translates an SDK transport cancellation into a retryable tool
        result when the browser is still connected. An outer deadline would use
        the same cancellation mechanism and could therefore be swallowed. The
        public callers must leave all MCP deadline enforcement to MCPClient.

        This deliberately catches only direct nesting and ``x = call_tool()`` /
        ``await wait_for(x)`` (or ``await x`` under ``asyncio.timeout``) in the
        same function. It does not perform inter-function, ``asyncio.gather``,
        closure, object-attribute, or intentionally obfuscated data-flow analysis.
        """
        source_root = Path(__file__).resolve().parents[4] / "api_service"
        violations: list[str] = []

        for path in source_root.rglob("*.py"):
            if path.name == "mcp_client.py" or "tests" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for function in ast.walk(tree):
                if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                violations.extend(self._deadline_violations(path, function))

        assert not violations, (
            "MCP public call is wrapped in an external deadline. "
            "Move deadline enforcement into MCPClient instead: " + "; ".join(violations)
        )

    @classmethod
    def _deadline_violations(
        cls, path: Path, function: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> list[str]:
        nodes = list(cls._function_scope_nodes(function))
        parents = {
            child: parent
            for parent in [function, *nodes]
            for child in ast.iter_child_nodes(parent)
            if child in nodes
        }
        aliases = cls._mcp_call_aliases(nodes)
        violations: list[str] = []

        for node in nodes:
            if cls._is_mcp_call(node):
                violations.extend(cls._direct_deadline_violations(path, node, parents))
            if cls._is_wait_for(node):
                violations.extend(cls._wait_for_alias_violations(path, node, aliases))
            if isinstance(node, ast.AsyncWith) and cls._is_timeout_context(node):
                violations.extend(cls._timeout_alias_violations(path, node, aliases))

        return violations

    @staticmethod
    def _function_scope_nodes(
        function: ast.FunctionDef | ast.AsyncFunctionDef,
    ):
        """Yield nodes in one function, deliberately excluding nested scopes."""

        def walk(node: ast.AST):
            for child in ast.iter_child_nodes(node):
                if isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
                ):
                    continue
                yield child
                yield from walk(child)

        yield from walk(function)

    @classmethod
    def _direct_deadline_violations(
        cls, path: Path, node: ast.Call, parents: dict[ast.AST, ast.AST]
    ) -> list[str]:
        violations: list[str] = []
        ancestor = parents.get(node)
        while ancestor is not None and not isinstance(
            ancestor, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
        ):
            if cls._is_wait_for(ancestor):
                violations.append(f"{path}:{node.lineno} -> asyncio.wait_for")
            if isinstance(ancestor, ast.AsyncWith) and cls._is_timeout_context(
                ancestor
            ):
                violations.append(f"{path}:{node.lineno} -> asyncio.timeout context")
            ancestor = parents.get(ancestor)
        return violations

    @staticmethod
    def _mcp_call_aliases(nodes: list[ast.AST]) -> dict[str, int]:
        aliases: dict[str, int] = {}
        for node in nodes:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if not isinstance(
                value, ast.Call
            ) or not TestMCPCallTimeoutContract._is_mcp_call(value):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    aliases[target.id] = node.lineno
        return aliases

    @classmethod
    def _wait_for_alias_violations(
        cls, path: Path, node: ast.Call, aliases: dict[str, int]
    ) -> list[str]:
        if not node.args or not isinstance(node.args[0], ast.Name):
            return []
        alias = node.args[0]
        assigned_at = aliases.get(alias.id)
        if assigned_at is None or assigned_at >= node.lineno:
            return []
        return [
            f"{path}:{node.lineno} -> asyncio.wait_for({alias.id}) "
            f"(MCP alias at line {assigned_at})"
        ]

    @classmethod
    def _timeout_alias_violations(
        cls, path: Path, node: ast.AsyncWith, aliases: dict[str, int]
    ) -> list[str]:
        violations: list[str] = []
        for descendant in ast.walk(node):
            if not isinstance(descendant, ast.Await) or not isinstance(
                descendant.value, ast.Name
            ):
                continue
            alias = descendant.value
            assigned_at = aliases.get(alias.id)
            if assigned_at is not None and assigned_at < node.lineno:
                violations.append(
                    f"{path}:{descendant.lineno} -> asyncio.timeout context "
                    f"awaiting {alias.id} (MCP alias at line {assigned_at})"
                )
        return violations

    @staticmethod
    def _is_mcp_call(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"call_tool", "list_tools"}
        )

    @staticmethod
    def _is_wait_for(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and ast.unparse(node.func.value) == "asyncio"
            and node.func.attr == "wait_for"
        )

    @staticmethod
    def _is_timeout_context(node: ast.AsyncWith) -> bool:
        return any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Attribute)
            and ast.unparse(item.context_expr.func.value) == "asyncio"
            and item.context_expr.func.attr == "timeout"
            for item in node.items
        )
