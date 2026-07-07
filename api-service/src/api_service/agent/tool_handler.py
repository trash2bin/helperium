"""Tool execution handler — single responsibility: call MCP tools.

``ToolHandler`` owns the individual tool-call loop: for each tool the model
requested it calls the MCP client, handles errors gracefully so one failed
tool doesn't kill the turn, builds ``role="tool"`` messages, and logs
everything extensively.

It does NOT decide which tools to call or how to react to results.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from .mcp_client import MCPClient, ToolResult
from .turn_context import TurnContext
from .types import (
    AgentEvent,
    ParsedToolCall,
    ToolCallEventData,
    ToolResultEventData,
)
from .conversation import ConversationManager

from api_service.backlog import backlog

logger = logging.getLogger("api_service.agent.tool_handler")


class ToolHandler:
    """Execute tool calls from the LLM and yield events + append results."""

    def __init__(
        self,
        mcp_client: MCPClient,
        conversation_manager: ConversationManager,
    ) -> None:
        self._mcp = mcp_client
        self._conv_mgr = conversation_manager

    async def execute(
        self,
        tool_calls: list[ParsedToolCall],
        session: Any,
        ctx: TurnContext,
    ) -> AsyncIterator[AgentEvent]:
        """Execute every tool call in *tool_calls* and yield events.

        Appends role="tool" messages to ``ctx.messages`` and
        ``ctx.turn_messages`` for each result.  One failed tool does not
        abort the others.
        """
        for tool_call in tool_calls:
            name: str = tool_call["name"]
            arguments: dict[str, Any] = tool_call["arguments"]
            tool_call_id: str = (
                tool_call.get("id") or f"call_{name}_{uuid.uuid4().hex[:8]}"
            )

            # ── Log the request ────────────────────────────────────────
            backlog.tool_call(
                ctx.session_id,
                ctx.turn_id,
                ctx.iteration,
                name,
                arguments,
            )
            yield AgentEvent(
                "tool_call",
                ToolCallEventData(
                    id=tool_call_id,
                    name=name,
                    arguments=arguments,
                ),
            )

            # ── Execute with per-tool error recovery ────────────────────
            try:
                tool_result: ToolResult = await self._mcp.call_tool(
                    session, name, arguments
                )

                # DETAILED LOGGING
                logger.info(
                    "[TOOL_HANDLER] Tool %s returned OK=%s, ContentLength=%d",
                    name,
                    tool_result.ok,
                    len(tool_result.tool_content),
                )
                logger.debug(
                    "[TOOL_HANDLER] Tool %s full content: %s",
                    name,
                    tool_result.tool_content,
                )

            except Exception as exc:
                logger.exception(
                    "[TOOL_HANDLER] Tool call '%s' failed with exception", name
                )
                tool_result = ToolResult(
                    tool_content=json.dumps(
                        {"error": True, "message": str(exc)},
                        ensure_ascii=False,
                    ),
                    reminder=(
                        f"Инструмент '{name}' завершился ошибкой: {exc}. "
                        "Попробуй другой инструмент или ответь пользователю, "
                        "что сервис временно недо��тупен."
                    ),
                    ok=False,
                    error=str(exc),
                )

            # ── Log the result ──────────────────────────────────────────
            backlog.tool_result(
                ctx.session_id,
                ctx.turn_id,
                ctx.iteration,
                name,
                tool_result.tool_content,
                duration_ms=0,
            )

            yield AgentEvent(
                "tool_result",
                ToolResultEventData(
                    id=tool_call_id,
                    name=name,
                    result=tool_result.tool_content,
                ),
            )

            # Post-tool reminder is logged but NOT added to LLM messages
            # to avoid breaking strict role alternation (required by
            # Mistral / Claude / etc.).
            if tool_result.reminder:
                logger.info(
                    "[TOOL_HANDLER] Tool reminder (logged only): %s",
                    tool_result.reminder,
                )

            # ── Append role="tool" message ──────────────────────────────
            tool_message: dict[str, Any] = {
                "role": "tool",
                "content": tool_result.tool_content,
                "tool_call_id": tool_call_id,
                "name": name,
            }
            logger.info(
                "[TOOL_HANDLER] Adding tool message: Role=tool, ContentLength=%d",
                len(tool_result.tool_content),
            )
            ctx.messages.append(tool_message)
            ctx.turn_messages.append(tool_message)
