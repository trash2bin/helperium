"""Main agent orchestrator that coordinates LLM, MCP, and conversation."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast

from demo.api.backlog import backlog
from demo.settings import settings

from .conversation import ConversationManager
from .llm_client import LLMClient
from .mcp_client import MCPClient
from .tool_parser import ToolCallParser
from .types import (
    AgentEventData,
    EventType,
    ErrorEventData,
    FinalEventData,
    Message,
    ParsedToolCall,
    SessionId,
    StatusEventData,
    TokenEventData,
    ToolCallEventData,
    ToolMessage,
    ToolResultEventData,
    TurnId,
    TurnMessages,
)

logger = logging.getLogger("demo.api.agent.orchestrator")


# System prompt for the agent
SYSTEM_PROMPT = """
Ты университетский ассистент с доступом к базе данных через MCP-инструменты.

КРИТИЧЕСКИ ВАЖНЫЕ ПРАВИЛА:
1. Ты НЕ знаешь никаких данных о студентах, расписании, оценках, преподавателях или документах без инструментов.
2. При любом вопросе о данных университета сначала используй MCP-инструмент.
3. Не выдумывай ответ из памяти.
4. Если вопрос общий — отвечай кратко и по делу.

ПРАВИЛА ОТВЕТА:
- Отвечай на языке пользователя, по умолчанию используй русский.
- Если данных нет — прямо скажи об этом.
- Если не понял запрос — уточни.
""".strip()


@dataclass(slots=True)
class AgentEvent:
    """Event emitted by the agent during processing."""
    type: EventType
    data: AgentEventData


class LLMAgent:
    """Main agent that orchestrates LLM, MCP, and conversation management."""

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        mcp_client: MCPClient | None = None,
        conversation_manager: ConversationManager | None = None,
    ) -> None:
        """
        Initialize the agent with optional component overrides for testing.

        Args:
            llm_client: LLM client for model interactions
            mcp_client: MCP client for tool interactions
            conversation_manager: Manager for conversation history
        """
        # Initialize components
        self.llm_client = llm_client or self._create_llm_client()
        self.mcp_client = mcp_client or MCPClient()
        self.conversation_manager = (
            conversation_manager or ConversationManager()
        )
        self.tool_parser = ToolCallParser()

        # Configuration from settings
        self.max_iterations = settings.agent_max_iterations
        self.max_empty_rounds = settings.agent_max_empty_rounds

    def _create_llm_client(self) -> LLMClient:
        """Create LLM client from settings."""
        model_name = settings.ollama_model
        known_providers = (
            "ollama/",
            "ollama_chat/",
            "openai/",
            "anthropic/",
            "deepseek/",
            "huggingface/",
            "mistral/",
            "groq/",
            "together_ai/",
        )

        if settings.ollama_url and not model_name.startswith(known_providers):
            model = f"ollama_chat/{model_name}"
        else:
            model = model_name

        api_base = (
            settings.ollama_url.rstrip("/")
            if settings.ollama_url
            else None
        )

        return LLMClient(
            model=model,
            api_base=api_base,
            timeout=settings.request_timeout,
            temperature=settings.agent_temperature,
            max_tokens_thinking=settings.agent_max_tokens_thinking,
            enable_thinking=settings.think_mode,
        )

    async def stream_answer(
        self, user_message: str, session_id: SessionId = "default"
    ) -> AsyncIterator[str]:
        """Backward-compatible token stream for the existing server.py."""
        streamed_text = ""
        async for event in self.stream_events(user_message, session_id=session_id):
            if event.type == "token":
                token = str(event.data)
                streamed_text += token
                yield token
            elif event.type == "final":
                content = (
                    event.data.get("content")
                    if isinstance(event.data, dict)
                    else None
                )
                if content:
                    suffix = self._unstreamed_suffix(
                        streamed_text, str(content)
                    )
                    if suffix:
                        yield suffix

    async def stream_sse(
        self, user_message: str, session_id: SessionId = "default"
    ) -> AsyncIterator[str]:
        """Stream Server-Sent Events."""
        async for event in self.stream_events(user_message, session_id=session_id):
            yield self._format_sse_event(event)

    async def stream_events(
        self, user_message: str, session_id: SessionId = "default"
    ) -> AsyncIterator[AgentEvent]:
        """Stream agent events (tokens, tool calls, results, etc.)."""
        session_id = self.conversation_manager.normalize_session_id(session_id)
        logger.info(
            "[AGENT] User message for session %s: %s...",
            session_id,
            user_message[:100],
        )

        lock = self.conversation_manager.get_session_lock(session_id)
        async with lock:
            async for event in self._run_turn(user_message, session_id):
                yield event

    async def _run_turn(
        self, user_message: str, session_id: SessionId
    ) -> AsyncIterator[AgentEvent]:
        """Execute a single conversation turn with multiple iterations."""
        # Build initial messages
        messages: list[dict[str, Any]] = self._build_messages_raw(user_message, session_id)
        turn_messages: list[dict[str, Any]] = [
            {"role": "user", "content": user_message}
        ]

        turn_id: TurnId = backlog.turn_start(session_id, user_message)

        try:
            async with self.mcp_client.get_session() as session:
                tools: list[dict[str, Any]] = await self.mcp_client.list_tools(session)
                logger.info(
                    "[AGENT] Available tools: %s",
                    [t.get("function", {}).get("name") for t in tools],
                )

                empty_rounds = 0
                is_finished = False

                for iteration in range(self.max_iterations):
                    iteration_empty_rounds = empty_rounds
                    iteration_completed = False

                    async for event in self._handle_iteration(
                        iteration,
                        session,
                        session_id,
                        turn_id,
                        messages,
                        turn_messages,
                        tools,
                        empty_rounds,
                    ):
                        if event.type == "final":
                            is_finished = True
                            iteration_completed = True
                        elif event.type == "tool_call":
                            iteration_completed = True
                        elif event.type == "tool_result":
                            iteration_completed = True
                        elif event.type == "status":
                            data = event.data
                            if (
                                isinstance(data, dict)
                                and data.get("phase") == "tool_calls"
                            ):
                                iteration_completed = True
                            elif (
                                isinstance(data, dict)
                                and data.get("phase") == "empty_round"
                            ):
                                empty_round_value = data.get("empty_rounds")
                                if isinstance(empty_round_value, int):
                                    iteration_empty_rounds = empty_round_value
                        yield event

                    empty_rounds = 0 if iteration_completed else iteration_empty_rounds

                    # Check if we should stop
                    if is_finished or empty_rounds >= self.max_empty_rounds:
                        break

                # Fallback only when no final answer was produced.
                if not is_finished:
                    async for event in self._run_fallback(
                        messages, turn_messages, session_id, is_finished,
                    ):
                        yield event

        except Exception as exc:
            backlog.error(
                session_id, turn_id, self.max_iterations, str(exc)
            )
            yield AgentEvent("error", ErrorEventData(message=str(exc)))
            raise

    async def _handle_iteration(
        self,
        iteration: int,
        session: Any,
        session_id: SessionId,
        turn_id: TurnId,
        messages: list[dict[str, Any]],
        turn_messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        empty_rounds: int,
    ) -> AsyncIterator[AgentEvent]:
        """Handle a single iteration of the agent loop."""
        logger.info(
            "[AGENT] Iteration %s/%s - calling model...",
            iteration + 1,
            self.max_iterations,
        )
        backlog.model_request(session_id, turn_id, iteration, messages, tools)

        # Call LLM and stream tokens
        final_message: dict[str, Any] | None = None
        async for token, final in self.llm_client.stream_completion(
            messages, tools
        ):
            if token:
                yield AgentEvent("token", TokenEventData(data=token))
            elif final:
                final_message = final

        # If no final message, try fallback from previous
        if final_message is None:
            final_message = self.llm_client.last_final_message

        # Handle empty response
        if final_message is None:
            new_empty_rounds = empty_rounds + 1
            backlog.empty_round(
                session_id,
                turn_id,
                iteration,
                "",
                messages,
            )
            yield AgentEvent(
                "status",
                StatusEventData(
                    phase="empty_round",
                    iteration=iteration,
                    empty_rounds=new_empty_rounds,
                ),
            )
            return

        # Log model response
        backlog.model_response(
            session_id,
            turn_id,
            iteration,
            final_message,
            duration_ms=0,
            token_usage=final_message.pop("_usage", None),
        )

        # Extract components from final message
        reasoning: str | None = final_message.get("reasoning_content")
        tool_calls: list[ParsedToolCall] = self.tool_parser.extract_tool_calls(
            final_message
        )
        content: str = (final_message.get("content") or "").strip()

        # Log reasoning if present
        if reasoning:
            backlog.empty_round(session_id, turn_id, iteration, reasoning, messages)

        # Handle tool calls
        if tool_calls:
            async for event in self._handle_tool_calls(
                tool_calls,
                session,
                session_id,
                turn_id,
                iteration,
                messages,
                turn_messages,
                final_message,
            ):
                yield event
            return  # Continue to next iteration

        # Handle final content
        if content:
            async for event in self._handle_final_content(
                final_message,
                content,
                messages,
                turn_messages,
                session_id,
            ):
                yield event
            return  # We're done

        # Handle partial response (no content, no tool calls)
        async for event in self._handle_partial_response(
            reasoning,
            iteration,
            empty_rounds,
            session_id,
            turn_id,
            messages,
        ):
            yield event

    async def _handle_tool_calls(
        self,
        tool_calls: list[ParsedToolCall],
        session: Any,
        session_id: SessionId,
        turn_id: TurnId,
        iteration: int,
        messages: list[dict[str, Any]],
        turn_messages: list[dict[str, Any]],
        final_message: dict[str, Any],
    ) -> AsyncIterator[AgentEvent]:
        """Handle tool calls from LLM response."""
        yield AgentEvent(
            "status",
            StatusEventData(
                phase="tool_calls",
                iteration=iteration,
                count=len(tool_calls),
            ),
        )

        # Format tool calls for model history
        final_message["tool_calls"] = self.tool_parser.format_for_model(tool_calls)
        messages.append(final_message)
        turn_messages.append(final_message)

        # Process each tool call
        for tool_call in tool_calls:
            name: str = tool_call["name"]
            arguments: dict[str, Any] = tool_call["arguments"]
            tool_call_id: str = tool_call.get("id") or f"call_{name}_{uuid.uuid4().hex[:8]}"

            # Log tool call
            backlog.tool_call(session_id, turn_id, iteration, name, arguments)
            yield AgentEvent(
                "tool_call",
                ToolCallEventData(
                    id=tool_call_id,
                    name=name,
                    arguments=arguments,
                ),
            )

            # Call the tool
            tool_result: str = await self.mcp_client.call_tool(
                session, name, arguments
            )
            backlog.tool_result(
                session_id, turn_id, iteration, name, tool_result, duration_ms=0
            )

            yield AgentEvent(
                "tool_result",
                ToolResultEventData(
                    id=tool_call_id,
                    name=name,
                    result=tool_result,
                ),
            )

            # Add tool response to messages
            tool_message: dict[str, Any] = {
                "role": "tool",
                "content": tool_result,
                "tool_call_id": tool_call_id,
                "name": name,
            }
            messages.append(tool_message)
            turn_messages.append(tool_message)

    async def _handle_final_content(
        self,
        final_message: dict[str, Any],
        content: str,
        messages: list[dict[str, Any]],
        turn_messages: list[dict[str, Any]],
        session_id: SessionId,
    ) -> AsyncIterator[AgentEvent]:
        """Handle final content response from LLM."""
        final_message["content"] = content
        messages.append(final_message)
        turn_messages.append(final_message)
        self.conversation_manager.remember_turn(session_id, cast(TurnMessages, turn_messages))

        yield AgentEvent("final", FinalEventData(content=content))

    async def _handle_partial_response(
        self,
        reasoning: str | None,
        iteration: int,
        empty_rounds: int,
        session_id: SessionId,
        turn_id: TurnId,
        messages: list[dict[str, Any]],
    ) -> AsyncIterator[AgentEvent]:
        """Handle partial response (no content, no tool calls)."""
        new_empty_rounds = empty_rounds + 1

        yield AgentEvent(
            "status",
            StatusEventData(
                phase="empty_round",
                iteration=iteration,
                empty_rounds=new_empty_rounds,
            ),
        )

        # Add reasoning to history if present
        if reasoning:
            messages.append(
                {
                    "role": "assistant",
                    "content": reasoning,
                }
            )

        # Add system prompt to encourage action
        messages.append(
            {
                "role": "system",
                "content": (
                    "Верни только tool_calls или финальный ответ. "
                    "Опирайся на предыдущие сообщения и reasoning_content и действуй"
                ),
            }
        )

    async def _run_fallback(
        self,
        messages: list[dict[str, Any]],
        turn_messages: list[dict[str, Any]],
        session_id: SessionId,
        is_finished: bool = False,
    ) -> AsyncIterator[AgentEvent]:
        """Run fallback stream when no final answer was produced."""
        final_parts: list[str] = []

        async for token in self.llm_client.get_final_message(messages):
            final_parts.append(token)
            yield AgentEvent("token", TokenEventData(data=token))

        if not final_parts and not is_finished:
            fallback_msg = "Извините, модель завершила работу без ответа. Попробуйте уточнить запрос."
            final_parts.append(fallback_msg)
            yield AgentEvent("token", TokenEventData(data=fallback_msg))

        turn_messages.append({"role": "assistant", "content": "".join(final_parts)})
        self.conversation_manager.remember_turn(session_id, cast(TurnMessages, turn_messages))

    def _build_messages_raw(
        self, user_message: str, session_id: SessionId
    ) -> list[dict[str, Any]]:
        """Build the raw messages list for the LLM (as dicts for compatibility)."""
        history: list[dict[str, Any]] = self.conversation_manager.get_history_messages(
            session_id
        )
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            *history,
            {"role": "user", "content": user_message},
        ]

    @staticmethod
    def _format_sse_event(event: AgentEvent) -> str:
        """Format an event as Server-Sent Event."""
        payload = json.dumps(event.data, ensure_ascii=False)
        return f"event: {event.type}\ndata: {payload}\n\n"

    @staticmethod
    def _unstreamed_suffix(streamed_text: str, final_text: str) -> str:
        """Get the suffix of final_text that wasn't streamed."""
        if not streamed_text:
            return final_text
        if final_text.startswith(streamed_text):
            return final_text[len(streamed_text) :]
        return ""

    async def health(self) -> dict[str, Any]:
        """Get agent health status."""
        return {
            "status": "ok",
            "model": self.llm_client.model,
            "api_base": self.llm_client.api_base,
            "thinking_enabled": self.llm_client.enable_thinking,
        }


# Default agent instance
agent = LLMAgent()
