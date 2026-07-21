"""Legacy adapters for backward compatibility.

These adapters bridge the gap between the old (sync / stream_completion-based)
interfaces and the new async Protocol interfaces used by the Pipeline.

Every class here is a "temporary" compatibility shim — they exist so that
existing tests and server.py imports keep working without a full migration
of every call site in one shot.
"""

from __future__ import annotations

import logging
from typing import Any

from .models import CompletionRequest, CompletionResponse
from .tool_parser import ToolCallParser

from api_service.backlog import backlog
from api_service.spending import get_spending_checker

logger = logging.getLogger("api_service.agent.legacy_adapters")


class _AsyncSpendingTracker:
    """Async wrapper around the sync SpendingChecker singleton.

    Adapts SpendingChecker (sync) to SpendingTracker protocol (async).
    """

    async def record(self, tenant_id: str, cost: float) -> None:
        get_spending_checker().record_spending(tenant_id, cost)

    async def check_limits(self, tenant_id: str) -> tuple[bool, str]:
        return get_spending_checker().check_limits(tenant_id)


class _OldStyleLLMAdapter:
    """Adapter: old LLMClient protocol → new LLMProvider protocol.

    Wraps old-style clients that expose ``stream_completion()``
    (which yields ``(token | None, final_message | None)`` tuples)
    into the new ``complete() → CompletionResponse`` contract.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.model: str = getattr(inner, "model", "unknown")
        self.api_base: str | None = getattr(inner, "api_base", None)
        self.enable_thinking: bool = getattr(inner, "enable_thinking", False)

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        """Call old-style stream_completion and build a CompletionResponse."""
        content_chars: list[str] = []
        final_message: dict[str, Any] | None = None

        async for token, final in self._inner.stream_completion(
            req.messages,
            tools=req.tools if req.tools else None,
            tenant_ids=req.tenant_ids,
        ):
            if token:
                content_chars.append(token)
            elif final:
                final_message = final
                break

        # Try fallback to last_final_message if stream produced nothing
        if final_message is None:
            final_message = getattr(self._inner, "last_final_message", None)

        if final_message is None:
            # Empty response
            return CompletionResponse(
                content="",
                content_tokens=list(content_chars),
                tool_calls=[],
            )

        final_message.pop("_usage", None)
        reasoning = final_message.get("reasoning_content")

        # Extract tool calls
        parser = ToolCallParser()
        tool_calls_raw = parser.extract_tool_calls(final_message)

        content = (final_message.get("content") or "").strip()

        return CompletionResponse(
            content=content,
            content_tokens=list(content_chars)
            if content_chars
            else ([content] if content else []),
            tool_calls=tool_calls_raw,
            reasoning_content=reasoning,
            cost=getattr(self._inner, "last_cost", 0.0),
        )


class _AsyncBacklogWriter:
    """Sync wrapper around the ModelBacklog singleton.

    All methods are sync because the underlying ``ModelBacklog`` writes
    to local files synchronously.  Stages call these methods without
    ``await``.

    Satisfies the ``BacklogWriter`` protocol structurally.
    """

    def record_llm_call(self, session_id: str, **kwargs: Any) -> None:
        backlog.record_llm_call(session_id, **kwargs)

    def tool_call(
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        name: str,
        arguments: dict[str, Any],
    ) -> None:
        backlog.tool_call(session_id, turn_id, iteration, name, arguments)

    def tool_result(
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        name: str,
        result: str,
        duration_ms: float = 0.0,
    ) -> None:
        backlog.tool_result(session_id, turn_id, iteration, name, result, duration_ms)

    def error(
        self,
        session_id: str,
        turn_id: str,
        iteration: int,
        error: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        backlog.error(session_id, turn_id, iteration, error, context)
