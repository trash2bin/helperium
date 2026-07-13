"""Main agent orchestrator — thin coordinator that wires handlers together.

Responsibility
--------------
Drive a single conversation turn: build context, loop through LLM →
possible tool calls → LLM again, emit events, handle fallback.

It delegates every specialised concern to dedicated modules (handlers)
and only owns the *sequence* of steps and the loop termination logic.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from helperium_sdk.settings import settings

from .conversation import ConversationManager
from .event_stream import format_sse_event, unstreamed_suffix
from .fallback_handler import FallbackHandler
from .llm_client import LLMClient, LLMClientProtocol, create_client
from .llm_handler import LLMHandler
from .mcp_client import MCPClient
from .prompts import SYSTEM_PROMPT
from .tool_handler import ToolHandler
from .tool_parser import ToolCallParser
from .token_estimator import estimate_tokens
from .turn_context import TurnContext
from .types import (
    AgentEvent,
    ErrorEventData,
    SessionId,
)

from api_service.backlog import backlog
from api_service.guardrails import get_guard_checker
from api_service.error_messages import classify_error
from api_service.spending import get_spending_checker

logger = logging.getLogger("api_service.agent.orchestrator")


class LLMAgent:
    """Thin orchestrator that wires handlers into a conversation turn loop.

    Components are injected in ``__init__`` (with defaults for production)
    so tests can substitute mocks freely.
    """

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        mcp_client: MCPClient | None = None,
        conversation_manager: ConversationManager | None = None,
    ) -> None:
        # ── Core components ─────────────────────────────────────────────
        self.llm_client: LLMClientProtocol = llm_client or create_client()
        self.mcp_client = mcp_client or MCPClient()
        self.conversation_manager = conversation_manager or ConversationManager()
        self.tool_parser = ToolCallParser()

        # ── Handlers (wired with injected components) ──────────────────
        self._llm_handler = LLMHandler(self.llm_client, self.tool_parser)
        self._tool_handler = ToolHandler(self.mcp_client, self.conversation_manager)
        self._fallback_handler = FallbackHandler(
            self.llm_client, self.conversation_manager
        )

        # ── Settings ──────────��────────────────────────────────────────
        self.max_iterations = settings.agent_max_iterations
        self.max_empty_rounds = settings.agent_max_empty_rounds
        self.max_turn_tokens = settings.agent_max_turn_tokens

    # ── Public entry points ────────────────────��─────────────────────────

    def create_per_request_llm(
        self, llm_config: dict | None = None
    ) -> LLMClient | LLMClientProtocol:
        """Create a per-request LLM client from per-agent config.

        Allows each request to use a different model/provider without
        recreating the entire orchestrator.
        """
        if llm_config:
            return create_client(llm_config)
        return self.llm_client

    async def stream_answer(
        self, user_message: str, session_id: SessionId = "default"
    ) -> AsyncIterator[str]:
        """Backward-compatible token stream (plain strings, no SSE).

        Used by ``server.py`` before the event-stream API was added.
        """
        streamed_text = ""
        async for event in self.stream_events(user_message, session_id=session_id):
            if event.type == "token":
                token = str(event.data)
                streamed_text += token
                yield token
            elif event.type == "final":
                content = (
                    event.data.get("content") if isinstance(event.data, dict) else None
                )
                if content:
                    suffix = unstreamed_suffix(streamed_text, str(content))
                    if suffix:
                        yield suffix

    async def stream_sse(
        self, user_message: str, session_id: SessionId = "default"
    ) -> AsyncIterator[str]:
        """Stream Server-Sent Events (legacy compatibility)."""
        async for event in self.stream_events(user_message, session_id=session_id):
            yield format_sse_event(event)

    async def stream_events(
        self,
        user_message: str,
        session_id: SessionId = "default",
        tenant_ids: list[str] | None = None,
        llm_config: dict | None = None,
        llm_client: LLMClient | None = None,
        system_prompt: str | None = None,
        lang: str = "ru",
    ) -> AsyncIterator[AgentEvent]:
        """Stream agent events: tokens, tool calls, tool results, final.

        This is the main entry point for new code.

        Args:
            user_message:   Raw text from the user.
            session_id:     Conversation session identifier.
            tenant_ids:     Scopes the MCP session to one or more tenants.
            llm_config:     Overrides the global LLM config for this request.
            llm_client:     Overrides the LLM client for this request (e.g. prioritized).
            system_prompt:  Overrides the global system prompt.
        """
        session_id = self.conversation_manager.normalize_session_id(session_id)
        logger.info(
            "[AGENT] User message for session %s (tenants: %s): %s",
            session_id,
            tenant_ids or ["(default)"],
            user_message[:100],
        )

        # Use per-request LLM client if explicitly provided, or build from config.
        if llm_client:
            request_llm = llm_client
        elif llm_config:
            request_llm = self.create_per_request_llm(llm_config)
        else:
            request_llm = self.llm_client

        lock = await self.conversation_manager.get_session_lock(session_id)
        async with lock:
            async for event in self._run_turn(
                user_message,
                session_id,
                tenant_ids,
                request_llm=request_llm,
                system_prompt=system_prompt,
                lang=lang,
            ):
                yield event

    # ── Health ──────────���─────────────────────────────────────────────────

    async def health(self) -> dict[str, Any]:
        """Get agent health status."""
        return {
            "status": "ok",
            "model": self.llm_client.model,
            "api_base": self.llm_client.api_base,
            "thinking_enabled": self.llm_client.enable_thinking,
        }

    # ── Internal: turn loop ──────────────────────────────────────────────

    async def _run_turn(
        self,
        user_message: str,
        session_id: SessionId,
        tenant_ids: list[str] | None = None,
        request_llm: LLMClientProtocol | None = None,
        system_prompt: str | None = None,
        lang: str = "ru",
    ) -> AsyncIterator[AgentEvent]:
        """Execute a single conversation turn with multiple iterations."""
        # ── 0. Guard: check input for prompt injection ───────────────
        guard_result = get_guard_checker().check_input(user_message)
        if guard_result.blocked:
            logger.warning("[GUARD] Blocked message: %s", guard_result.reason)
            backlog.error(session_id, "guard-block", 0, guard_result.reason)
            yield AgentEvent(
                "error",
                ErrorEventData(
                    message="Ваше сообщение заблокировано системой безопасности."
                ),
            )
            return

        # ── 1. Build initial context ─────────────────────────────────
        effective_prompt = system_prompt or SYSTEM_PROMPT

        ctx = await TurnContext.build(
            user_message=user_message,
            session_id=session_id,
            system_prompt=effective_prompt,
            conversation_manager=self.conversation_manager,
            tenant_ids=tenant_ids,
        )
        ctx.turn_id = backlog.turn_start(session_id, user_message)

        # Re-wire handlers if a per-request LLM was provided.
        if request_llm and request_llm is not self.llm_client:
            self._llm_handler = LLMHandler(request_llm, self.tool_parser)
            self._fallback_handler = FallbackHandler(
                request_llm, self.conversation_manager
            )

        try:
            async with self.mcp_client.get_session(tenant_ids=tenant_ids) as session:
                # ── 2. Discover tools ──────────────────────────────────
                ctx.tools = await self.mcp_client.list_tools(session)
                logger.info(
                    "[AGENT] Available tools: %s",
                    [t.get("function", {}).get("name") for t in ctx.tools],
                )

                # ── 3. Agent loop ──────────────────────────────────────
                for iteration in range(self.max_iterations):
                    ctx.iteration = iteration

                    # 3a. Call LLM → stream tokens + determine outcome
                    _llm_start = time.monotonic()
                    async for event in self._llm_handler.stream_and_parse(ctx):
                        yield event
                    _llm_duration = (time.monotonic() - _llm_start) * 1000

                    # Guard: check output for leaks
                    if ctx.outcome == "final" and ctx.turn_messages:
                        last_msg = ctx.turn_messages[-1]
                        if last_msg.get("role") == "assistant":
                            content = last_msg.get("content", "")
                            output_check = get_guard_checker().check_output(content)
                            if output_check.blocked:
                                logger.warning(
                                    "[GUARD] Blocked output: %s (session %s)",
                                    output_check.reason,
                                    session_id,
                                )
                                last_msg["content"] = (
                                    "[Ответ заблокирован системой безопа��ности]"
                                )

                    # Record spending for tenant
                    _cost = (
                        self._llm_handler._llm.last_cost
                        if hasattr(self._llm_handler._llm, "last_cost")
                        else 0.0
                    )
                    if _cost > 0 and tenant_ids:
                        for tid in tenant_ids:
                            get_spending_checker().record_spending(tid, _cost)

                    # Check spending limit
                    if tenant_ids:
                        for tid in tenant_ids:
                            _allowed, _reason = get_spending_checker().check_limits(tid)
                            if not _allowed:
                                logger.warning("[SPENDING] %s", _reason)
                                yield AgentEvent(
                                    "error",
                                    ErrorEventData(
                                        message="Лимит расходов исчерпан для этого тенанта."
                                    ),
                                )
                                return

                    # Record LLM call in backlog
                    if (
                        hasattr(self._llm_handler._llm, "last_usage")
                        and self._llm_handler._llm.last_usage
                    ):
                        usage = self._llm_handler._llm.last_usage
                        backlog.record_llm_call(
                            session_id=session_id,
                            model=getattr(self._llm_handler._llm, "model", "unknown"),
                            provider=getattr(
                                self._llm_handler._llm, "model", "unknown"
                            ).split("/")[0]
                            if "/" in getattr(self._llm_handler._llm, "model", "")
                            else "unknown",
                            duration_ms=_llm_duration,
                            prompt_tokens=usage.get("prompt_tokens", 0),
                            completion_tokens=usage.get("completion_tokens", 0),
                            total_tokens=usage.get("total_tokens", 0),
                            cost=self._llm_handler._llm.last_cost,
                            status="success",
                            tenant_ids=tenant_ids or [],
                            turn_id=ctx.turn_id,
                            iteration=ctx.iteration,
                        )

                    # 3b. Dispatch based on outcome
                    if ctx.outcome == "final" or ctx.is_finished:
                        await self.conversation_manager.aremember_turn(
                            ctx.session_id,
                            ctx.turn_messages,  # type: ignore[arg-type]
                        )
                        return  # success

                    if ctx.outcome == "tool_calls":
                        async for event in self._tool_handler.execute(
                            ctx.pending_tool_calls,
                            session,
                            ctx,
                        ):
                            yield event
                        continue  # next iteration

                    # 3c. Empty-round check
                    if ctx.empty_rounds >= self.max_empty_rounds:
                        logger.info(
                            "[AGENT] Empty rounds limit hit (%d) — stopping",
                            ctx.empty_rounds,
                        )
                        break

                    # 3d. Token budget check
                    if estimate_tokens(ctx.messages) >= self.max_turn_tokens:
                        logger.warning(
                            "[AGENT] Turn token budget exceeded (%d ≥ %d)",
                            estimate_tokens(ctx.messages),
                            self.max_turn_tokens,
                        )
                        break

                # ── 4. Fallback (no final answer) ───────────────────────
                if not ctx.is_finished:
                    async for event in self._fallback_handler.run(
                        ctx, was_finished=False
                    ):
                        yield event

        except Exception as exc:
            backlog.error(session_id, ctx.turn_id, ctx.iteration, str(exc))
            yield AgentEvent("error", ErrorEventData(message=classify_error(exc, lang)))


# ── Default singleton ─────────────────────────────────────────────────────
agent = LLMAgent()
