"""Pydantic domain models for the agent module.

Replaces ad-hoc TypedDicts and plain dataclasses with validated models
that can be used across component boundaries.
"""

from __future__ import annotations

from pydantic import BaseModel, SecretStr
from typing import Any, Literal


class UsageInfo(BaseModel):
    """Token usage information for an LLM call."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class CompletionRequest(BaseModel):
    """Request payload for an LLM completion call."""

    messages: list[dict]
    tools: list[dict] | None = None
    stream: bool = True
    tenant_ids: list[str] | None = None


class CompletionResponse(BaseModel):
    """Complete response from an LLM completion call.

    Contains the final content, optional tool calls, reasoning, usage,
    and cost tracking.
    """

    content: str = ""
    tool_calls: list[Any] = []
    reasoning_content: str | None = None
    usage: UsageInfo | None = None
    cost: float = 0.0
    role: str = "assistant"
    content_tokens: list[str] = []  # Streaming buffer


class ProviderConfig(BaseModel):
    """Configuration for an LLM provider.

    Stored in the provider store and used to create LLM clients.
    """

    name: str
    model: str
    api_key: SecretStr = SecretStr("")
    api_base: str = ""
    enabled: bool = True
    priority: int = 0
    source: Literal["env", "store"] = "store"
    provider: str = ""
    label: str = ""
