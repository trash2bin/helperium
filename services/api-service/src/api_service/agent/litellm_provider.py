"""LiteLLMProvider — адаптер LiteLLM под LLMProvider протокол.

Единственная responsibility: позвать LLM через litellm.acompletion(),
вернуть CompletionResponse.

Без cost, без метрик, без backlog, без last_final_message.
"""

from __future__ import annotations

import logging
from typing import Any

import litellm
from litellm.types.utils import ModelResponse

from .models import CompletionRequest, CompletionResponse, UsageInfo

logger = logging.getLogger("api_service.agent.litellm_provider")

# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 1 (primary): LiteLLM's built-in function-to-prompt conversion
# ═══════════════════════════════════════════════════════════════════════════════
# Contract: LiteLLM wraps tools/{'function':{...}} into a text prompt for
# models without native function calling (Ollama, local models, MiniMax).
# It then parses the model's text response back into msg.tool_calls so
# LLMStage sees structured tool_calls. This is the FIRST line of defence.
#
# If this flag is OFF, models without native tool support write JSON tool
# calls as plain text in content — which may leak to the user unless
# LAYER 2 (ToolCallParser) or LAYER 3 (safety net) catch it.
litellm.add_function_to_prompt = True


class LiteLLMProvider:
    """Реализует LLMProvider через LiteLLM.

    Используется ProviderPool для orchestrated LLM-вызовов.
    Не хранит состояние между вызовами (stateless).
    """

    def __init__(
        self,
        model: str,
        api_base: str | None = None,
        api_key: str | None = None,
        timeout: float = 120.0,
        temperature: float = 0.5,
        max_tokens_thinking: int = 4096,
        enable_thinking: bool = False,
    ) -> None:
        """Initialise the provider wrapper.

        Args:
            model: LiteLLM model identifier (e.g. ``"openai/gpt-4o-mini"``).
            api_base: Optional custom API base URL.
            api_key: API key (passed to litellm; if None, LiteLLM reads env vars).
            timeout: Request timeout in seconds (default 120).
            temperature: Sampling temperature (0-2).
            max_tokens_thinking: Maximum tokens for thinking/reasoning.
            enable_thinking: Whether to emit ``extra_body`` with ``think: True``.
        """
        self.model = model
        self.api_base = api_base
        self.api_key = api_key
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens_thinking = max_tokens_thinking
        self.enable_thinking = enable_thinking

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        """Чистый LLM вызов. Без побочных эффектов.

        Args:
            req: The completion request with messages, optional tools.

        Returns:
            A ``CompletionResponse`` with the model's output.
            ``cost`` is always 0 — the caller is responsible for cost tracking.
        """
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": req.messages,
            "temperature": self.temperature,
            "timeout": self.timeout,
        }

        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.enable_thinking:
            kwargs["extra_body"] = {"think": True}
        if req.tools:
            kwargs["tools"] = req.tools
        if req.stream:
            kwargs["stream"] = False  # complete() is non-streaming by contract

        # ── Make the call ──────────────────────────────────────────────────
        response = await litellm.acompletion(**kwargs)

        if not isinstance(response, ModelResponse):
            logger.error("Expected ModelResponse, got %s", type(response).__name__)
            raise TypeError(f"Expected ModelResponse, got {type(response).__name__}")

        # ── Extract result ─────────────────────────────────────────────────
        choice = response.choices[0]
        msg = choice.message

        content: str = msg.content or ""

        tool_calls: list[dict[str, Any]] = []
        raw_tool_calls = msg.tool_calls or []
        for tc in raw_tool_calls:
            tool_calls.append(
                {
                    "id": getattr(tc, "id", None),
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments or "{}",
                    },
                }
            )

        if tool_calls:
            logger.info(
                "[LITELLM_PROVIDER] Model returned %d tool_calls via native/"
                "add_function_to_prompt path (not fallback parser)",
                len(tool_calls),
            )

        reasoning: str | None = getattr(msg, "reasoning_content", None)

        usage_info: UsageInfo | None = None
        raw_usage = getattr(response, "usage", None)
        if raw_usage is not None:
            usage_info = UsageInfo(
                prompt_tokens=getattr(raw_usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(raw_usage, "completion_tokens", 0) or 0,
                total_tokens=getattr(raw_usage, "total_tokens", 0) or 0,
            )

        # ── Extract real cost from LiteLLM response (LOW-1 fix) ──────
        # LiteLLM stores cost in two possible places:
        #   1. response.usage.cost — per-request cost returned by provider
        #   2. response._hidden_params['cost'] — LiteLLM-calculated cost
        # We check both, falling back to 0.0 (safe for local models).
        cost = 0.0
        if raw_usage is not None:
            cost = getattr(raw_usage, "cost", None) or 0.0
        if cost == 0.0:
            hidden = getattr(response, "_hidden_params", None)
            if hidden is not None:
                cost = hidden.get("cost", 0.0) or 0.0

        # Populate content_tokens for streaming consumers (LLMStage)
        # When req.stream=True, content_tokens carries the raw streaming
        # output token-by-token. Since litellm.acompletion(stream=False)
        # returns the full content at once, we split into the single
        # token for protocol compliance.
        content_tokens: list[str] = []
        if req.stream:
            if content:
                # Split into characters for token-level granularity
                # (LiteLLM non-streaming gives content as a single string)
                content_tokens = [content]
            elif reasoning:
                content_tokens = [reasoning]

        return CompletionResponse(
            content=content,
            tool_calls=tool_calls,
            reasoning_content=reasoning,
            usage=usage_info,
            cost=cost,
            content_tokens=content_tokens,
        )
