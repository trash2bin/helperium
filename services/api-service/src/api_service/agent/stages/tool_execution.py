"""Stage 4 — Tool Execution.

Выполнить tool calls, вернуть результаты.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from ..error_context import ErrorContext
from ..pipeline import PipelineContext
from ..types import AgentEvent, ToolCallEventData, ToolResultEventData

logger = logging.getLogger("api_service.agent.stages")


class ToolExecutionStage:
    """Выполнить tool calls, вернуть результаты.

    Берёт pending_calls из ctx.turn.pending_calls, выполняет каждый
    через ctx.mcp_session.call_tool(), сохраняет результаты в
    ctx.turn.messages как role="tool" и в ctx.turn.tool_results.
    """

    async def run(self, ctx: PipelineContext) -> AsyncIterator[AgentEvent]:
        # ToolExecutionStage срабатывает только когда есть pending_calls
        if not ctx.turn.pending_calls:
            return

        # Pre-resolve display names
        display_names: dict[str, str] = {}
        for tc in ctx.turn.pending_calls:
            n = tc.get("name", "")
            if n and n not in display_names:
                try:
                    dn = await ctx.mcp_session.get_display_name(n)
                    display_names[n] = n if not isinstance(dn, str) or not dn else dn
                except Exception:
                    display_names[n] = n

        for tool_call in ctx.turn.pending_calls:
            # Respect pipeline stop signal — skip remaining tools
            if ctx.should_stop:
                break
            # IMPORTANT: tool_calls come from LiteLLM in the format:
            #   {"id":"call_x", "type":"function",
            #    "function": {"name":"search_auto_parts", "arguments":"{}"}}
            # NOT the old OpenAI message format with top-level name/arguments.
            name: str = tool_call.get("name") or tool_call.get("function", {}).get(
                "name", ""
            )

            raw_args = tool_call.get("arguments") or tool_call.get("function", {}).get(
                "arguments", {}
            )
            if isinstance(raw_args, str):
                try:
                    arguments = json.loads(raw_args)
                except (json.JSONDecodeError, TypeError):
                    arguments = {}
            elif isinstance(raw_args, dict):
                arguments = raw_args
            else:
                arguments = {}
            tool_call_id: str = (
                tool_call.get("id", "") or f"call_{name}_{uuid.uuid4().hex[:8]}"
            )
            display_name = display_names.get(name, name)

            # 📊 Backlog: tool call
            ctx.backlog.tool_call(
                ctx.turn.session_id,
                ctx.turn.turn_id,
                ctx.turn.iteration,
                name,
                arguments,
            )
            yield AgentEvent(
                "tool_call",
                ToolCallEventData(
                    id=tool_call_id,
                    name=name,
                    display_name=display_name,
                    arguments=arguments,
                ),
            )

            logger.info(
                "[TOOL_STAGE] Executing tool %s for iteration=%d with args=%s",
                name,
                ctx.turn.iteration,
                arguments,
            )

            # The deterministic scripted-LLM environment can hold execution
            # after the client-visible tool_call event. This creates a stable
            # fault-injection boundary for Docker resilience E2E without
            # affecting any normal runtime.
            if os.environ.get("USE_SCRIPTED_LLM") == "1":
                try:
                    test_delay_ms = float(
                        os.environ.get("SCRIPTED_TOOL_EXECUTION_DELAY_MS", "0")
                    )
                except ValueError:
                    test_delay_ms = 0
                if test_delay_ms > 0:
                    await asyncio.sleep(test_delay_ms / 1000)

            # Execute
            start_time = time.time()
            try:
                tool_result = await ctx.mcp_session.call_tool(name, arguments)
                logger.info(
                    "[TOOL_STAGE] Tool %s OK=%s, ContentLength=%d, Iteration=%d, Args=%s",
                    name,
                    tool_result.ok,
                    len(tool_result.tool_content),
                    ctx.turn.iteration,
                    arguments,
                )
            except Exception as exc:
                ec = (
                    ctx.error_context.with_stage("tool_execution")
                    if ctx.error_context is not None
                    else ErrorContext(stage="tool_execution")
                )
                ctx.error_context = ec
                ec.error_code = "tool_call_failed"
                ec.message = str(exc)
                ec.metadata = {
                    "tool_name": name,
                    "tool_call_id": tool_call_id,
                }
                logger.error(
                    "[STAGE][TOOL] Tool call '%s' failed",
                    name,
                    extra=ec.to_dict(),
                )
                logger.info(
                    "[TOOL_STAGE] Tool %s FAILED, Iteration=%d, Args=%s, Error=%s",
                    name,
                    ctx.turn.iteration,
                    arguments,
                    str(exc),
                )
                from ..mcp_client import ToolResult

                tool_result = ToolResult(
                    tool_content=json.dumps(
                        {"error": True, "message": str(exc)},
                        ensure_ascii=False,
                    ),
                    reminder=(
                        f"Инструмент '{name}' завершился ошибкой: {exc}. "
                        "Попробуй другой инструмент или ответь пользователю."
                    ),
                    ok=False,
                    error=str(exc),
                )

            # Truncate for backlog
            tool_content = tool_result.tool_content
            if len(tool_content) > 10_000:
                tool_content_short = (
                    tool_content[:10_000]
                    + f"\n...(truncated, {len(tool_result.tool_content)} chars)"
                )
            else:
                tool_content_short = tool_content

            ctx.backlog.tool_result(
                ctx.turn.session_id,
                ctx.turn.turn_id,
                ctx.turn.iteration,
                name,
                tool_content_short,
                duration_ms=int((time.time() - start_time) * 1000),
            )

            result_payload: dict[str, Any] = {
                "id": tool_call_id,
                "name": name,
                "display_name": display_name,
                "result": tool_result.tool_content,
            }
            if not tool_result.ok:
                result_payload["isError"] = True
                ctx.bench["tool_errors"] += 1

            # Check for empty result (LLM guessed wrong params)
            if tool_result.ok:
                try:
                    parsed = json.loads(tool_result.tool_content)
                    if isinstance(parsed, dict) and parsed.get("total") == 0:
                        ctx.bench["empty_results"] += 1
                except (json.JSONDecodeError, TypeError):
                    pass

            yield AgentEvent("tool_result", ToolResultEventData(**result_payload))

            # Store result in tool_results
            ctx.turn.tool_results.append(
                {
                    "tool_call_id": tool_call_id,
                    "name": name,
                    "result": tool_result.tool_content,
                }
            )

            # Append role="tool" to messages
            # LLM-friendly content: for errors, use clear text with [TOOL_ERROR] prefix
            # so the model knows this is a failed invocation, not data
            llm_content = tool_result.tool_content
            if not tool_result.ok:
                # Extract error message from JSON if possible
                try:
                    err_parts = json.loads(tool_result.tool_content)
                    err_msg = err_parts.get("error", tool_result.tool_content)
                except (json.JSONDecodeError, TypeError):
                    err_msg = tool_result.tool_content
                llm_content = f"[TOOL_ERROR] Tool '{name}' returned an error: {err_msg}. Do NOT repeat the same call with the same arguments."

            ctx.turn.messages.append(
                {
                    "role": "tool",
                    "content": llm_content,
                    "tool_call_id": tool_call_id,
                    "name": name,
                }
            )
            ctx.turn.turn_messages.append(
                {
                    "role": "tool",
                    "content": llm_content,
                    "tool_call_id": tool_call_id,
                    "name": name,
                }
            )

            # Inject reminder as system message so LLM understands what to do
            # after a failed tool call (only when reminder is available).
            if not tool_result.ok and tool_result.reminder:
                ctx.turn.messages.append(
                    {"role": "system", "content": tool_result.reminder}
                )
                ctx.turn.turn_messages.append(
                    {"role": "system", "content": tool_result.reminder}
                )

        # Clear pending calls
        ctx.turn.pending_calls = []
